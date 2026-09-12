"""Enrich selected V3 top-pool rows with canonical pool state and token metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eth_abi import decode
from eth_utils import keccak

from swaparch.core.types import CallSpec, norm_address
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import load_inventory

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/discovery-evidence/uniswap-v3-top5"
SIGS = {name: "0x" + keccak(text=sig)[:4].hex() for name, sig in {
    "token0": "token0()", "token1": "token1()", "fee": "fee()",
    "tick_spacing": "tickSpacing()", "liquidity": "liquidity()", "slot0": "slot0()",
    "symbol": "symbol()", "decimals": "decimals()",
}.items()}


def _spec(to: str, method: str, tag: str) -> CallSpec:
    return CallSpec(norm_address(to), SIGS[method], tag)


def selected_specs(rows: list[dict[str, Any]]) -> list[CallSpec]:
    specs = []
    for row in rows:
        pool = norm_address(row["pool"])
        for method in ("token0", "token1", "fee", "tick_spacing", "liquidity", "slot0"):
            specs.append(_spec(pool, method, f"enrich:{method}:{pool}"))
        addresses = row.get("token_addresses", [])
        for address in addresses:
            specs.extend((_spec(address, "symbol", f"enrich:symbol:{address}"),
                          _spec(address, "decimals", f"enrich:decimals:{address}")))
    return list({(spec.to, spec.data): spec for spec in specs}.values())


def _raw(snapshot: Any, spec: CallSpec) -> bytes | None:
    if not snapshot.has(spec):
        return None
    result = snapshot.get(spec)
    if not result.success:
        return None
    try:
        return bytes.fromhex(result.raw[2:])
    except ValueError:
        return None


def _decode_one(snapshot: Any, spec: CallSpec, kind: str) -> Any:
    raw = _raw(snapshot, spec)
    if raw is None:
        return None
    try:
        if kind == "address": return norm_address(decode(["address"], raw)[0])
        if kind == "uint24": return decode(["uint24"], raw)[0]
        if kind == "int24": return decode(["int24"], raw)[0]
        if kind == "uint128": return decode(["uint128"], raw)[0]
        if kind == "slot0": return list(decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], raw))
        if kind == "string": return decode(["string"], raw)[0]
        if kind == "uint8": return decode(["uint8"], raw)[0]
    except (TypeError, ValueError):
        return None
    raise ValueError(kind)


def enrich(report: dict[str, Any], snapshot: Any, *, source_report: str | None = None,
           network_requests: int | None = None) -> dict[str, Any]:
    inventory = {row.pool_id: row for row in load_inventory("uniswap_v3")}
    candidates = {row["pool_id"]: row for row in report.get("candidates", [])}
    rows = []
    for selected in report.get("selected_pools", []):
        candidate = candidates.get(selected["pool_id"], {})
        known = inventory.get(selected["pool_id"])
        addresses = [a for a in candidate.get("tokens", []) if isinstance(a, str) and a.startswith("0x")]
        if not addresses and known:
            addresses = [token.address for token in known.tokens]
        pool = candidate.get("pool") or (known.pool if known else None)
        row = {"pool_id": selected["pool_id"], "pool": pool,
               "selected_for": selected["selected_for"],
               "created_block": candidate.get("created_block", known.created_block if known else None),
               "quote_status": next((x.get("quote_status") for x in report.get("candidates", [])
                                     if x.get("pool_id") == selected["pool_id"]), None),
               "token_addresses": addresses}
        if not pool:
            row["status"] = "missing_pool_identity"
            rows.append(row)
            continue
        pool = norm_address(pool)
        values = {
            "token0": _decode_one(snapshot, _spec(pool, "token0", f"enrich:token0:{pool}"), "address"),
            "token1": _decode_one(snapshot, _spec(pool, "token1", f"enrich:token1:{pool}"), "address"),
            "fee": _decode_one(snapshot, _spec(pool, "fee", f"enrich:fee:{pool}"), "uint24"),
            "tick_spacing": _decode_one(snapshot, _spec(pool, "tick_spacing", f"enrich:tick_spacing:{pool}"), "int24"),
            "liquidity": _decode_one(snapshot, _spec(pool, "liquidity", f"enrich:liquidity:{pool}"), "uint128"),
            "slot0": _decode_one(snapshot, _spec(pool, "slot0", f"enrich:slot0:{pool}"), "slot0"),
        }
        row["pool_state"] = values
        row["token_metadata"] = {}
        for address in addresses:
            row["token_metadata"][address] = {
                "symbol": _decode_one(snapshot, _spec(address, "symbol", f"enrich:symbol:{address}"), "string"),
                "decimals": _decode_one(snapshot, _spec(address, "decimals", f"enrich:decimals:{address}"), "uint8"),
            }
        observed = {values["token0"], values["token1"]} - {None}
        row["addresses_match_discovery"] = not addresses or observed == set(addresses)
        row["initialized"] = bool(values["slot0"] and values["slot0"][0] > 0)
        row["positive_active_liquidity"] = bool(values["liquidity"] and values["liquidity"] > 0)
        metadata_complete = all(
            item["symbol"] is not None and item["decimals"] is not None
            for item in row["token_metadata"].values()
        )
        if not all(value is not None for value in values.values()):
            row["status"] = "missing_pool_state"
        elif not row["addresses_match_discovery"]:
            row["status"] = "address_mismatch"
        elif not metadata_complete:
            row["status"] = "missing_token_metadata"
        else:
            row["status"] = "complete"
        rows.append(row)
    return {"block": report["block"], "block_hash": report["block_hash"],
            "metric": report.get("metric"), "scope": "selected_top5_pool_enrichment",
            "source_report": source_report, "network_requests": network_requests, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    client = RpcClient(offline=not args.online)
    block = client.get_block(int(report["block"]))
    if block.hash.lower() != str(report["block_hash"]).lower():
        raise SystemExit("report block hash does not match cached/resolved block")
    rows = []
    for selected in report.get("selected_pools", []):
        row = next((item for symbol in selected["selected_for"]
                    for item in report.get("top5", {}).get(symbol, [])
                    if item.get("pool_id") == selected["pool_id"]), None)
        if row:
            row = dict(row)
            row["selected_for"] = selected["selected_for"]
            row["token_addresses"] = [address for address in row.get("tokens", [])
                                       if isinstance(address, str) and address.startswith("0x")]
            rows.append(row)
    if not rows:
        rows = [{"pool_id": selected["pool_id"], "pool": next((x["pool"] for x in report.get("candidates", []) if x["pool_id"] == selected["pool_id"]), None),
                 "selected_for": selected["selected_for"], "token_addresses": []} for selected in report.get("selected_pools", [])]
    store = SnapshotStore()
    snapshot = store.extend(block, selected_specs(rows), client) if args.online else store.load(block.chain, block.hash)
    output = enrich(report, snapshot, source_report=str(args.report),
                    network_requests=client.network_requests)
    path = args.report.with_name(f"{args.report.stem}-enriched.json")
    path.write_text(json.dumps(output, indent=1) + "\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
