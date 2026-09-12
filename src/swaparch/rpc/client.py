"""Block-pinned JSON-RPC client with a raw disk cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import time
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values

from swaparch.core.types import BlockRef, CallResult, CallSpec, norm_address
from swaparch.rpc.headers import HeaderCache, hashes_match

SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def configured_project_root() -> Path:
    """Return the checkout-compatible root containing ``data/`` and ``evidence/``."""
    value = os.environ.get("SWAPARCH_ROOT")
    return Path(value).expanduser().resolve() if value else SOURCE_PROJECT_ROOT


PROJECT_ROOT = configured_project_root()
RETRYABLE_TEXT = (
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "limit exceeded",
    "temporarily",
    "bad gateway",
    "connection reset",
)
RANGE_ERROR_TEXT = (
    "block range",
    "too many results",
    "query returned more than",
    "exceeds",
    "10000",
    "response size",
    "log limit",
    "more than",
)
HASH_UNSUPPORTED_TEXT = (
    "blockhash is not supported",
    "block hash is not supported",
    "cannot unmarshal",
    "invalid argument",
    "expected block number",
)


class RpcError(RuntimeError):
    pass


class RpcResponseError(RpcError):
    def __init__(self, code: int | None, message: str, data: Any = None) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def resolve_rpc_url() -> str | None:
    """Resolution order (docs/INTERFACES.md): ETH_RPC_URL env, RPC_MAINNET env,
    project .env (ETH_RPC_URL then RPC_MAINNET), then explicit SWAPARCH_ENV_FILE."""
    for key in ("ETH_RPC_URL", "RPC_MAINNET"):
        value = os.environ.get(key)
        if value:
            return value
    project_env = PROJECT_ROOT / ".env"
    if project_env.is_file():
        loaded = dotenv_values(project_env)
        for key in ("ETH_RPC_URL", "RPC_MAINNET"):
            if loaded.get(key):
                return str(loaded[key])
    configured_env_file = os.environ.get("SWAPARCH_ENV_FILE")
    if not configured_env_file:
        return None
    env_file = Path(configured_env_file)
    if not env_file.is_file():
        return None
    loaded = dotenv_values(env_file).get("RPC_MAINNET")
    return str(loaded) if loaded else None


def rpc_available() -> bool:
    url = resolve_rpc_url()
    if not url:
        return False
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True


class RpcClient:
    def __init__(
        self,
        cache_root: Path | str = PROJECT_ROOT / "data/rpc-cache",
        session: requests.Session | None = None,
        *,
        offline: bool = False,
    ) -> None:
        self.offline = offline
        url = "http://offline.invalid" if offline else resolve_rpc_url()
        if not url:
            raise RpcError("archive RPC URL is not configured (ETH_RPC_URL / RPC_MAINNET)")
        self._url = url
        self.cache_root = Path(cache_root)
        self.session = session or requests.Session()
        self.session.headers.update({"content-type": "application/json"})
        self.network_requests = 0
        self._next_id = 1
        self._block_hash_calls: bool | None = None
        self._headers = HeaderCache(self.cache_root)
        self._call_db = None

    def __del__(self):
        connection = getattr(self, '_call_db', None)
        if connection is not None:
            connection.close()

    def _call_database(self):
        if self._call_db is None:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.cache_root / 'calls.sqlite3', timeout=30)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.execute('CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, payload BLOB NOT NULL)')
            self._call_db = connection
        return self._call_db

    def chain_id(self) -> int:
        path = self.cache_root / "chain-id.json"
        if path.exists():
            return int(json.loads(path.read_text())["response"]["result"], 16)
        raw = self._rpc("eth_chainId", [])
        self._write_json(
            path,
            {"request": {"method": "eth_chainId", "params": []}, "response": {"result": raw}},
        )
        return int(raw, 16)

    def get_block(self, number_or_hash: int | str) -> BlockRef:
        chain = self.chain_id()
        if isinstance(number_or_hash, int):
            cached = self._headers.load(chain, number_or_hash)
            if cached:
                return cached
            return self._fetch_numbered_block(chain, number_or_hash)
        if number_or_hash == "latest":
            raw = self._rpc("eth_getBlockByNumber", ["latest", False])
            return self._block_ref(chain, raw)
        if number_or_hash.startswith("0x") and len(number_or_hash) == 66:
            raw = self._rpc("eth_getBlockByHash", [number_or_hash.lower(), False])
            block = self._block_ref(chain, raw)
            self._headers.store(chain, raw)
            return block
        number = int(number_or_hash, 0)
        cached = self._headers.load(chain, number)
        return cached or self._fetch_numbered_block(chain, number)

    def eth_call(self, to: str, data: str, block: BlockRef) -> CallResult:
        if block.chain != self.chain_id():
            raise RpcError(f"block belongs to chain {block.chain}, not the configured chain")
        spec = CallSpec(to, data)
        cached = self.cached_call(spec, block)
        if cached:
            return cached

        if self._block_hash_calls is not False:
            params = [{"to": spec.to, "data": spec.data}, {"blockHash": block.hash}]
            try:
                raw = self._rpc("eth_call", params)
                self._block_hash_calls = True
                result = CallResult(spec, True, self._call_data(raw), "eth_call")
                self.store_call_result(result, block, params)
                return result
            except RpcResponseError as exc:
                if self._hash_parameter_unsupported(exc):
                    self._block_hash_calls = False
                elif self._is_call_revert(exc):
                    result = CallResult(spec, False, self._error_data(exc), "eth_call")
                    self.store_call_result(result, block, params)
                    return result
                else:
                    raise

        self._verify_live_hash(block)
        params = [{"to": spec.to, "data": spec.data}, hex(block.number)]
        try:
            raw = self._rpc("eth_call", params)
            result = CallResult(spec, True, self._call_data(raw), "eth_call")
        except RpcResponseError as exc:
            if not self._is_call_revert(exc):
                raise
            result = CallResult(spec, False, self._error_data(exc), "eth_call")
        self._verify_live_hash(block)
        self.store_call_result(result, block, params)
        return result

    def cached_call(self, spec: CallSpec, block: BlockRef) -> CallResult | None:
        path = self._call_cache_path(spec, block)
        database = self.cache_root / 'calls.sqlite3'
        if database.exists():
            if self._call_db is not None:
                row = self._call_db.execute('SELECT payload FROM calls WHERE key=?', (path.stem,)).fetchone()
            else:
                # A closed WAL database is checkpointed; immutable reads need no
                # writable sidecars in read-only/offline sessions.
                suffix = '?mode=ro' if database.with_name('calls.sqlite3-wal').exists() else '?mode=ro&immutable=1'
                with closing(sqlite3.connect(database.resolve().as_uri() + suffix, uri=True, timeout=30)) as connection:
                    row = connection.execute('SELECT payload FROM calls WHERE key=?', (path.stem,)).fetchone()
            if row:
                response = json.loads(zlib.decompress(row[0]))['response']
                return CallResult(spec, bool(response['success']), str(response['raw']), 'cache')
        if not path.exists():
            return None
        response = json.loads(path.read_text())["response"]
        return CallResult(spec, bool(response["success"]), str(response["raw"]), "cache")

    def store_call_result(
        self,
        result: CallResult,
        block: BlockRef,
        params: list[Any] | None = None,
    ) -> None:
        logical_params = params or [
            {"to": result.spec.to, "data": result.spec.data},
            {"blockHash": block.hash},
        ]
        payload = {
                "request": {"method": "eth_call", "params": logical_params},
                "response": {
                    "success": result.success,
                    "raw": result.raw,
                    "via": result.via,
                },
            }
        connection = self._call_database()
        connection.execute('INSERT OR REPLACE INTO calls VALUES (?, ?)',
                           (self._call_cache_path(result.spec, block).stem,
                            zlib.compress(json.dumps(payload, separators=(',', ':')).encode(), 1)))
        connection.commit()

    def get_logs(
        self,
        address: str,
        topics: list[Any],
        from_block: int,
        to_block: int,
        chunk: int,
    ) -> list[dict[str, Any]]:
        address = norm_address(address)
        if chunk < 1:
            raise ValueError("chunk must be positive")
        logs: list[dict[str, Any]] = []
        start = from_block
        current_chunk = chunk
        chain = self.chain_id()
        while start <= to_block:
            end = min(start + current_chunk - 1, to_block)
            digest = self._digest((chain, address, topics, start, end))
            path = self.cache_root / "logs" / f"{digest}.json"
            if path.exists():
                payload = json.loads(path.read_text())
                current = self._fetch_numbered_block(chain, end, use_cache=False)
                if payload["to_block_hash"].lower() == current.hash:
                    logs.extend(payload["response"]["result"])
                    start = end + 1
                    continue
            params = [
                {
                    "address": address,
                    "topics": topics,
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                }
            ]
            try:
                result = self._rpc("eth_getLogs", params)
            except RpcResponseError as exc:
                if self._is_range_error(exc) and current_chunk > 1:
                    current_chunk = max(1, current_chunk // 2)
                    continue
                raise
            end_ref = self.get_block(end)
            self._write_json(
                path,
                {
                    "request": {"method": "eth_getLogs", "params": params},
                    "response": {"result": result},
                    "to_block_hash": end_ref.hash,
                },
            )
            logs.extend(result)
            start = end + 1
        return logs

    def _rpc(self, method: str, params: list[Any], max_attempts: int = 6) -> Any:
        if self.offline:
            raise RpcError(f"offline cache miss: {method}")
        delay = 1
        last_reason = "unknown failure"
        for attempt in range(max_attempts):
            request_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            try:
                self.network_requests += 1
                response = self.session.post(self._url, json=payload, timeout=60)
                if response.status_code == 429 or response.status_code >= 500:
                    last_reason = f"HTTP {response.status_code}"
                    if attempt + 1 < max_attempts:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise RpcError(f"{method} failed after {max_attempts} attempts: {last_reason}")
                if response.status_code >= 400:
                    # Some providers put a valid JSON-RPC error (notably a log
                    # range limit) behind HTTP 400. Let that error take the
                    # normal safe-text/retry/range-routing path below. Other
                    # HTTP errors remain transport failures.
                    if response.status_code != 400:
                        raise RpcError(f"{method} failed with HTTP {response.status_code}")
                    try:
                        body = response.json()
                    except ValueError:
                        raise RpcError(f"{method} failed with HTTP {response.status_code}") from None
                    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
                        raise RpcError(f"{method} failed with HTTP {response.status_code}")
                else:
                    body = response.json()
            except requests.Timeout:
                last_reason = "request timed out"
                if attempt + 1 < max_attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RpcError(f"{method} failed after {max_attempts} attempts: {last_reason}") from None
            except requests.ConnectionError:
                last_reason = "connection failed"
                if attempt + 1 < max_attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RpcError(f"{method} failed after {max_attempts} attempts: {last_reason}") from None
            except requests.RequestException:
                raise RpcError(f"{method} request failed") from None
            except ValueError:
                raise RpcError(f"{method} returned invalid JSON") from None

            error = body.get("error")
            if error is None:
                return body.get("result")
            code = error.get("code")
            message = self._safe_text(error.get("message", "RPC failure"))
            if self._retryable_rpc(code, message) and attempt + 1 < max_attempts:
                last_reason = f"RPC error {code}: {message}"
                time.sleep(delay)
                delay *= 2
                continue
            raise RpcResponseError(code, message, error.get("data"))
        raise RpcError(f"{method} failed after {max_attempts} attempts: {last_reason}")

    def _fetch_numbered_block(
        self, chain: int, number: int, use_cache: bool = True
    ) -> BlockRef:
        if use_cache:
            cached = self._headers.load(chain, number)
            if cached:
                return cached
        raw = self._rpc("eth_getBlockByNumber", [hex(number), False])
        if not isinstance(raw, dict):
            raise RpcError(f"block {number} was not found")
        return self._headers.store(chain, raw)

    def _verify_live_hash(self, block: BlockRef) -> None:
        current = self._fetch_numbered_block(block.chain, block.number, use_cache=False)
        if not hashes_match(current, block.hash):
            raise RpcError(
                f"block {block.number} hash changed: expected {block.hash}, got {current.hash}"
            )

    @staticmethod
    def _block_ref(chain: int, raw: Any) -> BlockRef:
        if not isinstance(raw, dict):
            raise RpcError("block was not found")
        from swaparch.rpc.headers import block_ref_from_rpc

        return block_ref_from_rpc(chain, raw)

    def _call_cache_path(self, spec: CallSpec, block: BlockRef) -> Path:
        digest = self._digest((block.chain, block.hash, spec.to, spec.data))
        return self.cache_root / "calls" / f"{digest}.json"

    @staticmethod
    def _digest(parts: Any) -> str:
        raw = json.dumps(parts, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _error_data(exc: RpcResponseError) -> str:
        if isinstance(exc.data, str) and exc.data.startswith("0x"):
            return exc.data.lower()
        if isinstance(exc.data, dict):
            raw = exc.data.get("data")
            if isinstance(raw, str) and raw.startswith("0x"):
                return raw.lower()
        return "0x"

    @staticmethod
    def _call_data(raw: Any) -> str:
        if not isinstance(raw, str) or not raw.startswith("0x"):
            raise RpcError("eth_call returned invalid data")
        return raw.lower()

    @staticmethod
    def _is_call_revert(exc: RpcResponseError) -> bool:
        return exc.code == 3 or "revert" in exc.message.lower()

    @staticmethod
    def _retryable_rpc(code: int | None, message: str) -> bool:
        lowered = message.lower()
        return code == -32005 or any(marker in lowered for marker in RETRYABLE_TEXT)

    @staticmethod
    def _is_range_error(exc: RpcResponseError) -> bool:
        lowered = exc.message.lower()
        return exc.code == -32005 or any(marker in lowered for marker in RANGE_ERROR_TEXT)

    @staticmethod
    def _hash_parameter_unsupported(exc: RpcResponseError) -> bool:
        lowered = exc.message.lower()
        return exc.code == -32602 or any(marker in lowered for marker in HASH_UNSUPPORTED_TEXT)

    def _safe_text(self, value: Any) -> str:
        text = str(value).replace(self._url, "<redacted>")
        parsed = urlsplit(self._url)
        for secret in (parsed.netloc, parsed.hostname, parsed.path, parsed.query):
            if secret:
                text = text.replace(secret, "<redacted>")
        return re.sub(r"https?://[^\s]+", "<redacted>", text)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")))
        temporary.replace(path)
