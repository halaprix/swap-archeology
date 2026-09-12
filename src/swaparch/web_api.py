"""Small offline-only HTTP bridge for the historical quote lab."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from swaparch.cli import (
    PROJECT_ROOT,
    _identity,
    _inventory_inputs,
    _snapshot_identity,
    quote_command,
)
from swaparch.explorer import _as_strings, _display_report
from swaparch.rpc.client import RpcClient, RpcError

_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_NUMBER = re.compile(r"[1-9][0-9]*\Z")
_AMOUNT = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_SOLVERS = {"baseline", "search"}


class RequestError(ValueError):
    def __init__(self, code: str, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class QuoteRequest:
    number: int
    block_hash: str
    token_in: str
    token_out: str
    amount: str
    solver: str
    sources: tuple[str, ...] | None


def _safe_error(error: Exception) -> str:
    """Keep local paths, RPC URLs, and environment details out of HTTP output."""
    text = str(error).replace("\n", " ")
    text = re.sub(r"https?://[^\s]+", "[redacted URL]", text)
    text = re.sub(r"/(?:home|mnt|tmp)/[^\s]+", "[redacted path]", text)
    return text[:240] or "quote could not be completed"


class QuoteBridge:
    """Bounded, serialized adapter over the existing offline CLI quote path."""

    def __init__(self, data_root: Path | str = PROJECT_ROOT / "web-data") -> None:
        self.data_root = Path(data_root)
        self.quotes_root = self.data_root / "quotes"
        self._solver_lock = threading.Lock()

    def _resolve_block(self, raw: Any) -> tuple[int, str]:
        if not isinstance(raw, str):
            raise RequestError("invalid_block", "block must be a decimal block number or cached block hash")
        client = RpcClient(offline=True)
        if _NUMBER.fullmatch(raw):
            try:
                block = client.get_block(int(raw))
            except (OSError, RpcError, ValueError):
                raise RequestError("missing_state", "block header is not cached for offline quoting", HTTPStatus.CONFLICT) from None
            return block.number, block.hash
        if not _HASH.fullmatch(raw):
            raise RequestError("invalid_block", "block must be a decimal block number or 0x-prefixed 32-byte hash")
        wanted = raw.lower()
        for header in (PROJECT_ROOT / "data/snapshots/1").glob("*/header.json"):
            try:
                value = json.loads(header.read_text())
            except (OSError, ValueError):
                continue
            if str(value.get("hash", "")).lower() == wanted:
                return int(value["number"]), wanted
        raise RequestError("missing_state", "block hash is not cached for offline quoting", HTTPStatus.CONFLICT)

    def parse_request(self, payload: Any) -> QuoteRequest:
        if not isinstance(payload, dict):
            raise RequestError("invalid_request", "request body must be a JSON object")
        number, block_hash = self._resolve_block(payload.get("block"))
        token_in, token_out, amount = (payload.get(name) for name in ("tokenIn", "tokenOut", "amount"))
        if not all(isinstance(value, str) and value.strip() for value in (token_in, token_out, amount)):
            raise RequestError("invalid_request", "tokenIn, tokenOut, and amount must be non-empty strings")
        solver = payload.get("solver", "baseline")
        if not isinstance(solver, str) or solver not in _SOLVERS:
            raise RequestError("invalid_solver", "solver must be baseline or search")
        raw_sources = payload.get("sources")
        if (any(len(value) > 128 for value in (token_in, token_out))
                or len(amount) > 115 or not _AMOUNT.fullmatch(amount)
                or len(amount.partition(".")[0]) > 78
                or len(amount.partition(".")[2]) > 36):
            raise RequestError("invalid_request", "token names and amount must be bounded plain decimal input")
        if raw_sources is not None and (not isinstance(raw_sources, list)
                                        or not raw_sources
                                        or len(raw_sources) > 16
                                        or not all(isinstance(item, str) and item and len(item) <= 64
                                                   for item in raw_sources)):
            raise RequestError("invalid_sources", "sources must be a non-empty string array when supplied")
        sources = tuple(sorted(set(raw_sources))) if raw_sources is not None else None
        return QuoteRequest(number, block_hash, token_in.strip(), token_out.strip(), amount.strip(), solver, sources)

    def _cache_key(self, request: QuoteRequest) -> str:
        _paths, inventory_id, _inventories = _inventory_inputs()
        snapshot_id = _snapshot_identity(1, request.block_hash)
        code_id = _identity(sorted(Path(__file__).resolve().parent.rglob("*.py")))
        value = {"request": request.__dict__, "inventory": inventory_id,
                 "snapshot": snapshot_id, "code": code_id}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def quote(self, payload: Any) -> dict[str, Any]:
        request = self.parse_request(payload)
        try:
            key = self._cache_key(request)
        except (OSError, ValueError) as error:
            raise RequestError("missing_state", _safe_error(error), HTTPStatus.CONFLICT) from None
        target = self.quotes_root / f"{key}.json"
        if target.is_file():
            try:
                cached = json.loads(target.read_text())
                if cached.get("key") == key and isinstance(cached.get("entry"), dict):
                    return {**cached["entry"], "cached": True}
            except (OSError, ValueError):
                pass
        if not self._solver_lock.acquire(blocking=False):
            raise RequestError("busy", "another offline quote is running", HTTPStatus.TOO_MANY_REQUESTS)
        try:
            # Recheck after winning the lock: only one solver captures stdout.
            if target.is_file():
                try:
                    cached = json.loads(target.read_text())
                    if cached.get("key") == key and isinstance(cached.get("entry"), dict):
                        return {**cached["entry"], "cached": True}
                except (OSError, ValueError):
                    pass
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    quote_command(request.number, request.token_in, request.token_out, request.amount, 4,
                                  solver_name=request.solver, max_steps=4, beam_width=24,
                                  max_expansions=300, families=request.sources, offline=True)
                report = json.loads(output.getvalue())
            except (OSError, RpcError, ValueError, KeyError, json.JSONDecodeError) as error:
                code = "missing_state" if "cache" in str(error).lower() or "offline" in str(error).lower() else "quote_failed"
                raise RequestError(code, _safe_error(error), HTTPStatus.CONFLICT if code == "missing_state" else 422) from None
            if str(report.get("block_hash", "")).lower() != request.block_hash:
                raise RequestError("missing_state", "cached header does not match the quote block", HTTPStatus.CONFLICT)
            selected = [source for source in report.get("sources", [])
                        if isinstance(source, dict) and source.get("selected") is True]
            if not any(type(source.get("usable_pools")) is int and source["usable_pools"] > 0
                       for source in selected):
                raise RequestError("qualification_required", "no selected source has qualified cached state at this block",
                                   HTTPStatus.CONFLICT)
            entry = {"id": key, "origin": "adhoc", "report": _as_strings(_display_report(report)), "cached": False}
            self.quotes_root.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"key": key, "entry": entry}, separators=(",", ":")))
            temporary.replace(target)
            return entry
        finally:
            self._solver_lock.release()

    def reports(self) -> dict[str, Any]:
        entries = []
        for path in sorted(self.quotes_root.glob("*.json")) if self.quotes_root.is_dir() else []:
            try:
                entry = json.loads(path.read_text()).get("entry")
                if isinstance(entry, dict):
                    entries.append({**entry, "cached": True})
            except (OSError, ValueError):
                continue
        return {"reports": entries}


def serve(host: str = "127.0.0.1", port: int = 8765, data_root: Path | str = PROJECT_ROOT / "web-data") -> None:
    bridge = QuoteBridge(data_root)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok", "mode": "offline"})
            elif self.path == "/reports":
                self._send(200, bridge.reports())
            else:
                self._send(404, {"error": {"code": "not_found", "message": "route not found"}})

        def do_POST(self) -> None:
            if self.path != "/quote":
                self._send(404, {"error": {"code": "not_found", "message": "route not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise RequestError("invalid_request", "request body must be between 1 and 16384 bytes")
                self._send(200, bridge.quote(json.loads(self.rfile.read(length))))
            except RequestError as error:
                self._send(error.status, {"error": {"code": error.code, "message": str(error)}})
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"error": {"code": "invalid_json", "message": "request body must be valid JSON"}})

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swaparch-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "web-data")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.data_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
