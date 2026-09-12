#!/usr/bin/env python3
"""Emit offline Balancer V3 pool identity and pinned-call specifications.

This deliberately does not perform RPC or HTTP.  The root worker can feed the
JSON records to the shared pinned reader, adding the deployed Vault address
and exact block hashes.  A failed code check must suppress the getter calls at
that pin and retain an ``absent_at_pin`` record.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore

POOLS: dict[str, dict[str, Any]] = {
    "0x1ea5870f7c037930ce1d5d8d9317c670e89e13e3": {
        "name": "Balancer rETH - Aave WETH",
        "type": "STABLE",
        "version": 2,
        "protocol_version": 3,
        "factory": "0xe42c2e153bb0a8899b59c73f5ff941f9742f1197",
        "create_time": 1762802303,
        "tokens": [
            {"address": "0x0bfc9d54fc184518a81162f8fb99c2eaca081202", "symbol": "waEthWETH", "decimals": 18},
            {"address": "0xae78736cd615f374d3085123a210448e74fc6393", "symbol": "rETH", "decimals": 18},
        ],
    },
    "0x85b2b559bc2d21104c4defdd6efca8a20343361d": {
        "name": "Balancer Aave GHO/USDT/USDC",
        "type": "STABLE",
        "version": 1,
        "protocol_version": 3,
        "factory": "0xb9d01ca61b9c181da1051bfdd28e1097e920ab14",
        "create_time": 1736461595,
        "tokens": [
            {"address": "0x7bc3485026ac48b6cf9baf0a377477fff5703af8", "symbol": "waEthUSDT", "decimals": 6},
            {"address": "0xc71ea051a5f82c67adcf634c36ffe6334793d24c", "symbol": "Aave Prime GHO", "decimals": 18},
            {"address": "0xd4fa2d31b7968e448877f69a96de69f5de8cd23e", "symbol": "waEthUSDC", "decimals": 6},
        ],
    },
}

PINS = (23549991, 23550060, 23728292, 24356381, 25896003)
DEFAULT_VAULT = "0xba1333333333a1ba1108e8412f11850a5c319ba9"

POOL_REGISTERED_TOPIC = "0xbc1561eeab9f40962e2fb827a7ff9c7cdb47a9d7c84caeefa4ed90e043842dad"

# These are deliberately signatures and typed arguments, rather than locally
# calculated selectors.  The project's pinned reader owns ABI encoding and
# records the exact bytes/result/revert at a block hash.
POOL_CALLS = (
    ("isPoolRegistered(address)", ("pool",)),
    ("isPoolInitialized(address)", ("pool",)),
    ("getPoolTokens(address)", ("pool",)),
    ("getPoolTokenInfo(address)", ("pool",)),
    ("getCurrentLiveBalances(address)", ("pool",)),
    ("getPoolTokenRates(address)", ("pool",)),
    ("getPoolConfig(address)", ("pool",)),
    ("getHooksConfig(address)", ("pool",)),
    ("getPoolPausedState(address)", ("pool",)),
    ("getPoolData(address)", ("pool",)),
)
BUFFER_CALLS = (
    ("isERC4626BufferInitialized(address)", ("token",)),
    ("getERC4626BufferAsset(address)", ("token",)),
    ("getBufferBalance(address)", ("token",)),
    ("getBufferTotalShares(address)", ("token",)),
)
FACTORY_CALLS = (
    ("getPoolVersion()", ()),
    ("isPoolFromFactory(address)", ("pool",)),
    ("getPoolCount()", ()),
    ("getPoolsInRange(uint256,uint256)", ("0", "256")),
)


def _topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


# Keep these derived topics beside the human-readable event signatures.  The
# known PoolRegistered topic is retained from the project's existing Balancer
# evidence; deriving it requires the complete tuple canonicalisation.
POOL_CREATED_TOPIC = _topic("PoolCreated(address)")
POOL_INITIALIZED_TOPIC = _topic("PoolInitialized(address)")


def _calldata(signature: str, types: list[str] | tuple[str, ...] = (), values: list[Any] | tuple[Any, ...] = ()) -> str:
    return "0x" + keccak(text=signature)[:4].hex() + encode(types, values).hex()


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return value


def decode_value(raw: str, abi_types: list[str] | tuple[str, ...]) -> Any:
    return _jsonable(decode(abi_types, bytes.fromhex(raw[2:])))


def code_is_present(raw: str) -> bool:
    """A code check is strict: malformed data is an error, 0x is absent."""
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise ValueError("eth_getCode returned non-hex data")
    int(raw[2:] or "0", 16)
    return raw.lower() != "0x"


def _code_check(client: RpcClient, address: str, block_hash: str) -> dict[str, Any]:
    params = [address, {"blockHash": block_hash}]
    raw = client._rpc("eth_getCode", params)  # deliberately EIP-1898/hash pinned
    return {
        "request": {"method": "eth_getCode", "params": params},
        "raw": raw.lower(),
        "code_present": code_is_present(raw),
    }


def _call_json(result: Any, abi_types: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    row = {
        "to": result.spec.to,
        "data": result.spec.data,
        "tag": result.spec.tag,
        "success": result.success,
        "raw": result.raw,
        "via": result.via,
    }
    if result.success and result.raw != "0x" and abi_types is not None:
        try:
            row["decoded"] = decode_value(result.raw, abi_types)
        except (DecodingError, ValueError) as exc:  # preserve raw evidence when a provider returns an unexpected shape
            row["decode_error"] = type(exc).__name__
    return row


def _spec(to: str, signature: str, types: list[str] | tuple[str, ...], values: list[Any] | tuple[Any, ...], tag: str) -> CallSpec:
    return CallSpec(to, _calldata(signature, types, values), tag)


def _online_pool_calls(pool: str, vault: str) -> tuple[list[CallSpec], dict[str, tuple[str, ...]]]:
    calls: list[CallSpec] = []
    decodes: dict[str, tuple[str, ...]] = {}

    def add(target: str, signature: str, types: list[str] | tuple[str, ...], values: list[Any] | tuple[Any, ...], tag: str, abi: tuple[str, ...] = ()) -> None:
        call = _spec(target, signature, types, values, tag)
        calls.append(call)
        if abi:
            decodes[call.data] = abi

    add(vault, "isPoolRegistered(address)", ["address"], [pool], "balancer:is_pool_registered", ("bool",))
    add(vault, "isPoolInitialized(address)", ["address"], [pool], "balancer:is_pool_initialized", ("bool",))
    add(vault, "getPoolTokens(address)", ["address"], [pool], "balancer:pool_tokens", ("address[]",))
    add(vault, "getPoolConfig(address)", ["address"], [pool], "balancer:pool_config", ("((bool,bool,bool,bool),uint256,uint256,uint256,uint40,uint32,bool,bool,bool,bool)",))
    add(vault, "getHooksConfig(address)", ["address"], [pool], "balancer:hooks_config", ("((bool,bool,bool,bool,bool,bool,bool,bool,bool,bool),address)",))
    add(vault, "getPoolTokenRates(address)", ["address"], [pool], "balancer:pool_token_rates", ("uint256[]", "uint256[]"))
    add(vault, "getPoolTokenInfo(address)", ["address"], [pool], "balancer:pool_token_info", ("address[]", "(uint8,address,bool)[]", "uint256[]", "uint256[]"))
    add(pool, "getAmplificationParameter()", [], [], "balancer:amplification", ("uint256", "bool", "uint256"))
    return calls, decodes


def _online_buffer_calls(vault: str, tokens: list[str]) -> tuple[list[CallSpec], dict[str, tuple[str, ...]]]:
    calls: list[CallSpec] = []
    decodes: dict[str, tuple[str, ...]] = {}
    for token in tokens:
        for signature, abi, tag in (
            ("isERC4626BufferInitialized(address)", ("bool",), "buffer_initialized"),
            ("getERC4626BufferAsset(address)", ("address",), "buffer_asset"),
            ("getBufferBalance(address)", ("uint256", "uint256"), "buffer_balance"),
            ("getBufferTotalShares(address)", ("uint256",), "buffer_total_shares"),
        ):
            call = _spec(vault, signature, ["address"], [token], f"balancer:{tag}:{token}")
            calls.append(call)
            decodes[call.data] = abi
    return calls, decodes


def _events(client: RpcClient, vault: str, pool: str, factory: str, to_block: int, chunk: int) -> dict[str, list[dict[str, Any]]]:
    return {
        "pool_created": client.get_logs(factory, [POOL_CREATED_TOPIC, "0x" + pool[2:].lower().rjust(64, "0")], 0, to_block, chunk),
        "pool_registered": client.get_logs(vault, [POOL_REGISTERED_TOPIC, "0x" + pool[2:].lower().rjust(64, "0")], 0, to_block, chunk),
        "pool_initialized": client.get_logs(vault, [POOL_INITIALIZED_TOPIC, "0x" + pool[2:].lower().rjust(64, "0")], 0, to_block, chunk),
    }


def run_online(args: argparse.Namespace) -> None:
    vault = args.vault.lower()
    client = RpcClient(cache_root=args.cache_root)
    store = SnapshotStore(root=args.snapshot_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for number in args.blocks:
        block = client.get_block(number)
        code_checks = {"vault": _code_check(client, vault, block.hash)}
        rows: list[dict[str, Any]] = []
        for pool in args.pools or list(POOLS):
            metadata = POOLS[pool]
            row: dict[str, Any] = {
                "chain_id": 1,
                "block": asdict(block),
                "pool": pool,
                "metadata_source": "https://api-v3.balancer.fi/graphql",
                "metadata": metadata,
                "code_checks": {},
                "status": "pending",
                "calls": [],
                "buffers": [],
            }
            pool_code = _code_check(client, pool, block.hash)
            row["code_checks"]["pool"] = pool_code
            if args.events and code_checks["vault"]["code_present"]:
                # Event evidence is useful even when this pool is absent at the
                # selected pin: it establishes the activation boundary.
                row["events"] = _events(client, vault, pool, metadata["factory"], number, args.log_chunk)
            if not code_checks["vault"]["code_present"]:
                row["status"] = "vault_absent_at_pin"
                rows.append(row)
                continue
            if not pool_code["code_present"]:
                row["status"] = "absent_at_pin"
                rows.append(row)
                continue

            identity = client.eth_call(pool, _calldata("getVault()"), block)
            row["pool_get_vault"] = _call_json(identity, ("address",))
            try:
                observed_vault = decode_value(identity.raw, ("address",))[0] if identity.success else None
            except (DecodingError, ValueError):
                observed_vault = None
            if observed_vault != vault:
                row["status"] = "vault_identity_mismatch"
                rows.append(row)
                continue

            specs, decodes = _online_pool_calls(pool, vault)
            snapshot = store.extend(block, specs, client)
            row["calls"] = [_call_json(snapshot.get(call), decodes.get(call.data)) for call in specs]
            tokens_call = next(call for call in specs if call.tag == "balancer:pool_tokens")
            token_result = snapshot.get(tokens_call)
            observed_tokens: list[str] = []
            if token_result.success and token_result.raw != "0x":
                try:
                    observed_tokens = decode_value(token_result.raw, ("address[]",))[0]
                except (DecodingError, ValueError):
                    observed_tokens = []
            row["observed_tokens"] = observed_tokens
            buffer_specs, buffer_decodes = _online_buffer_calls(vault, observed_tokens)
            if buffer_specs:
                buffer_snapshot = store.extend(block, buffer_specs, client)
                row["buffers"] = [_call_json(buffer_snapshot.get(call), buffer_decodes.get(call.data)) for call in buffer_specs]
            row["status"] = "queried"
            rows.append(row)
        output = {
            "schema": "balancer-user-pools-probe/v2",
            "mode": "online",
            "vault": vault,
            "block": asdict(block),
            "code_checks": code_checks,
            "rows": rows,
            "network_requests": client.network_requests,
        }
        (output_dir / f"{block.hash.lower()}.json").write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps({"block": number, "hash": block.hash, "rows": len(rows), "output": str(output_dir / f"{block.hash.lower()}.json"), "network_requests": client.network_requests}), flush=True)


def spec(to: str | None, signature: str, args: list[str], tag: int, purpose: str) -> dict[str, Any]:
    return {
        "to": to,
        "signature": signature,
        "args": args,
        "block": tag,
        "purpose": purpose,
        "encode_and_execute": "root_pinned_reader",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="perform block-pinned RPC and persist one JSON per block hash")
    parser.add_argument("--vault", help="deployed Ethereum Balancer V3 Vault/Explorer address")
    parser.add_argument("--blocks", nargs="+", type=int, default=list(PINS))
    parser.add_argument("--pool", action="append", dest="pools", choices=tuple(POOLS))
    parser.add_argument("--cache-root", type=Path, default=ROOT / "data/rpc-cache")
    parser.add_argument("--snapshot-root", type=Path, default=ROOT / "data/snapshots")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/discovery-evidence/balancer-user-pools")
    parser.add_argument("--events", action="store_true", help="also fetch indexed creation/registration/initialization logs")
    parser.add_argument("--log-chunk", type=int, default=100_000)
    args = parser.parse_args()
    if args.online:
        args.vault = (args.vault or DEFAULT_VAULT).lower()
        run_online(args)
        return
    addresses = args.pools or list(POOLS)
    records: list[dict[str, Any]] = []
    for pool in addresses:
        metadata = POOLS[pool]
        for block in args.blocks:
            calls: list[dict[str, Any]] = []
            # Pool code/getVault is the first identity guard.  The root reader
            # must skip all Vault calls if the pool has no code at this pin.
            calls.append(spec(pool, "getVault()", [], block, "pool_identity"))
            for signature, fields in POOL_CALLS:
                calls.append(spec(args.vault, signature, [pool], block, "pool_state"))
            for token in metadata["tokens"]:
                for signature, fields in BUFFER_CALLS:
                    calls.append(spec(args.vault, signature, [token["address"]], block, "erc4626_buffer_state"))
            for signature, fields in FACTORY_CALLS:
                call_args = [pool] if fields == ("pool",) else list(fields)
                calls.append(spec(metadata["factory"], signature, call_args, block, "factory_identity"))
            records.append(
                {
                    "chain_id": 1,
                    "pool": pool,
                    "block": block,
                    "status_before_code_check": "needs_pinned_code_check",
                    "metadata_source": "https://api-v3.balancer.fi/graphql",
                    "metadata": metadata,
                    "historical_activation_logs": [
                        "Vault.PoolRegistered(address indexed,address indexed,...)",
                        "Vault.PoolInitialized(address indexed)",
                        "Factory.PoolCreated(address indexed)",
                    ],
                    "calls": calls,
                }
            )
    print(json.dumps({"schema": "balancer-user-pools-probe/v1", "records": records}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
