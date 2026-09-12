"""Rank Uniswap V3 pools by endpoint-token balance at pinned blocks.

The default is strictly offline and reads the existing inventory plus
SnapshotStore. ``--discover`` writes separate PoolCreated evidence and never
modifies the canonical 52-pool inventory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from eth_abi import encode
from eth_utils import keccak

from swaparch.core.types import BlockRef, CallResult, CallSpec, norm_address
from swaparch.discovery.uniswap_v3 import (
    ENDPOINT_TOKENS,
    FACTORY,
    FACTORY_CREATION_BLOCK,
    POOL_CREATED_SIG,
    decode_pool_created,
)
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import load_inventory

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data/discovery/1/uniswap_v3.json"
EVIDENCE = ROOT / "data/discovery-evidence/uniswap-v3-top5"
BALANCE_SELECTOR = "0x" + keccak(text="balanceOf(address)")[:4].hex()
POOL_CREATED_TOPIC = "0x" + keccak(text=POOL_CREATED_SIG).hex()
DEFAULT_PINS = (23549991, 23550060, 24356381, 23728292, 25896003)


def _topic_address(address: str) -> str:
    return "0x" + norm_address(address)[2:].rjust(64, "0")


def balance_spec(pool: str, token: str, symbol: str = "") -> CallSpec:
    """Return the ERC-20 balanceOf(pool) call used by the ranking metric."""
    data = BALANCE_SELECTOR + encode(["address"], [norm_address(pool)]).hex()
    return CallSpec(norm_address(token), data, f"top5:token_balance:{symbol}:{norm_address(pool)}")


def _decode_balance(result: CallResult | None) -> int | None:
    if result is None or not result.success or not result.raw.startswith("0x"):
        return None
    raw = bytes.fromhex(result.raw[2:])
    return int.from_bytes(raw, "big") if len(raw) == 32 else None


def _record_candidates(block_number: int, block_hash: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for record in load_inventory("uniswap_v3"):
        endpoint_symbols = [t.symbol for t in record.tokens if t.symbol in ENDPOINT_TOKENS]
        if not endpoint_symbols:
            continue
        validated = block_hash in record.config.get("validated_block_hashes", []) if block_hash else False
        rows.append({
            "pool_id": record.pool_id,
            "pool": record.pool,
            "tokens": [t.symbol for t in record.tokens],
            "endpoint_symbols": endpoint_symbols,
            "created_block": record.created_block,
            "inventory_status": record.status.value if validated else "discovered_unsupported",
            "inventory_notes": record.notes,
            "not_deployed_at_block": record.created_block is not None and record.created_block > block_number,
            "record": record,
        })
    return rows


def rank_candidates(
    records: Iterable[Mapping[str, Any]], block: BlockRef, snapshot: Any, *, top_n: int = 5
) -> dict[str, Any]:
    """Rank balance-backed candidates and retain every exclusion reason."""
    all_rows: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in records:
        for symbol in candidate["endpoint_symbols"]:
            token = ENDPOINT_TOKENS[symbol]
            spec = balance_spec(candidate["pool"], token, symbol)
            balance = None if candidate["not_deployed_at_block"] else _decode_balance(
                snapshot.get(spec) if snapshot.has(spec) else None
            )
            row = {
                "pool_id": candidate["pool_id"],
                "pool": candidate["pool"],
                "tokens": candidate["tokens"],
                "study_token": symbol,
                "token_balance": balance,
                "balance_call": {"to": spec.to, "data": spec.data, "tag": spec.tag},
                "created_block": candidate["created_block"],
                "inventory_status": candidate["inventory_status"],
                "balance_allowed_at_block": not candidate["not_deployed_at_block"],
            }
            if candidate["not_deployed_at_block"]:
                row["status"] = "not_deployed_at_block"
            elif balance is None:
                row["status"] = "missing_balance"
            elif balance == 0:
                row["status"] = "zero_balance"
            else:
                row["status"] = "ranked"
                by_symbol[symbol].append(row)
            row["quote_status"] = (
                "quote_eligible" if candidate["inventory_status"] == "supported"
                else "excluded_quote"
            )
            if row["quote_status"] == "excluded_quote":
                row["quote_reason"] = "inventory pool has no validated quote support at this block"
            all_rows.append(row)

    top5: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, set[str]] = defaultdict(set)
    for symbol in ENDPOINT_TOKENS:
        winners = sorted(by_symbol[symbol], key=lambda r: (-r["token_balance"], r["pool"]))[:top_n]
        top5[symbol] = winners
        for row in winners:
            selected[row["pool_id"]].add(symbol)
    selected_pools = [
        {"pool_id": pool_id, "selected_for": sorted(symbols)}
        for pool_id, symbols in sorted(selected.items())
    ]
    return {
        "block": block.number,
        "block_hash": block.hash,
        "metric": "token_balance",
        "metric_unit": "raw ERC-20 smallest units; no USD TVL or executable-depth claim",
        "top5": top5,
        "selected_pools": selected_pools,
        "candidates": all_rows,
    }


def _discovery_specs(block: int) -> list[dict[str, Any]]:
    specs = []
    # eth_getLogs cannot OR topic1 with topic2, so retain both indexed-position
    # scans explicitly; topic3 is omitted and therefore covers every fee tier.
    for symbol, token in ENDPOINT_TOKENS.items():
        for position in (1, 2):
            topics: list[Any] = [POOL_CREATED_TOPIC, None, None]
            topics[position] = _topic_address(token)
            specs.append({"symbol": symbol, "position": position, "topics": topics,
                          "from_block": FACTORY_CREATION_BLOCK, "to_block": block})
    return specs


def discover(client: RpcClient, block: BlockRef) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover endpoint pools into evidence files; canonical inventory is read-only."""
    candidates: dict[str, dict[str, Any]] = {}
    coverage = []
    for request in _discovery_specs(block.number):
        identity = json.dumps({"chain": block.chain, "to_block_hash": block.hash, **request}, sort_keys=True).encode()
        digest = hashlib.sha256(identity).hexdigest()
        raw_path = EVIDENCE / "raw-logs" / f"{digest}.json"
        payload = None
        if raw_path.exists():
            payload = json.loads(raw_path.read_text())
            if payload.get("to_block_hash", "").lower() != block.hash.lower():
                payload = None
            else:
                logs = payload["logs"]
        if not raw_path.exists() or payload is None:
            logs = client.get_logs(FACTORY, request["topics"], request["from_block"],
                                   request["to_block"], chunk=request["to_block"] - request["from_block"] + 1)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps({"request": request, "to_block_hash": block.hash,
                                             "logs": logs}, indent=2) + "\n")
        coverage.append({**request, "raw_log_id": digest, "logs_found": len(logs)})
        for log in logs:
            decoded = decode_pool_created(log)
            pool = decoded["pool"]
            row = candidates.setdefault(pool, {"pool": pool, "token0": decoded["token0"],
                                                "token1": decoded["token1"], "fee": decoded["fee"],
                                                "tick_spacing": decoded["tick_spacing"],
                                                "created_block": decoded["created_block"],
                                                "pool_id": f"uniswap_v3:{FACTORY}:{pool}",
                                                "tokens": [decoded["token0"], decoded["token1"]],
                                                "inventory_status": "discovered_unsupported",
                                                "not_deployed_at_block": False})
            row.setdefault("endpoint_symbols", [])
            for name, address in ENDPOINT_TOKENS.items():
                if address in (decoded["token0"], decoded["token1"]) and name not in row["endpoint_symbols"]:
                    row["endpoint_symbols"].append(name)
    return list(candidates.values()), coverage


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block", type=int, action="append", dest="blocks")
    parser.add_argument("--top", type=_positive_int, default=5)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--online", action="store_true", help="allow RPC for --discover")
    return parser.parse_args()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> int:
    args = _parse_args()
    if args.discover and not args.online:
        raise SystemExit("--discover requires --online; default mode is offline")
    numbers = args.blocks or list(DEFAULT_PINS)
    store = SnapshotStore()
    client = RpcClient(offline=not args.online) if args.discover else RpcClient(offline=True)
    for number in numbers:
        block = client.get_block(number)
        if args.discover:
            records, coverage = discover(client, block)
            specs = [balance_spec(row["pool"], ENDPOINT_TOKENS[symbol], symbol)
                     for row in records for symbol in row["endpoint_symbols"]
                     if row["created_block"] is None or row["created_block"] <= block.number]
            snapshot = store.extend(block, specs, client)
        else:
            records, coverage = _record_candidates(block.number, block.hash), []
            snapshot = store.load(block.chain, block.hash)
        report = rank_candidates(records, block, snapshot, top_n=args.top)
        report.update({"mode": "discovery" if args.discover else "existing_inventory",
                       "scope": "discovered_endpoint_full" if args.discover else "existing_inventory_bounded",
                       "coverage": coverage, "snapshot": f"data/snapshots/{block.chain}/{block.hash}",
                       "network_requests": client.network_requests})
        path = EVIDENCE / f"top5-{block.number}-{block.hash}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1) + "\n")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
