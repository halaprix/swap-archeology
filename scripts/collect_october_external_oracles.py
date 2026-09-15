"""Add historically verified external feeds to the October oracle sidecar.

Read-only, block-hash pinned, cache-resumable. A subset never replaces the public
254-block file. RPC credentials remain in the local RpcClient environment.
"""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from eth_abi import decode
from eth_utils import keccak

from scripts.collect_october_oracle_references import atomic_write_json, validate_source_dataset
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
REDSTONE = "0x67F6838e58859d612E4ddF04dA396d6DABB66Dc4"
USDC_USD = "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6"
SOURCE_ID = "redstone_eth_usdc"
MANIFEST = (
    "https://github.com/redstone-finance/redstone-oracles-monorepo/blob/"
    "816de619f59f5b69d9dcc86a804ba479bfe3306d/"
    "packages/relayer-remote-config/main/relayer-manifests-multi-feed/ethereumMultiFeed.json"
)
META = {
    "id": SOURCE_ID, "label": "RedStone ETH / Chainlink USDC", "kind": "oracle",
    "address": REDSTONE, "denominatorAddress": USDC_USD, "source": MANIFEST,
    "description": "RedStone ETH/USD divided by same-block Chainlink USDC/USD; USDC per ETH. "
                   "Mixed-provider reference, no USDC=$1 assumption. Not a swap quote. "
                   "RedStone manifest: 0.5% deviation / 24h heartbeat. Update age retained per block.",
}
CHAOS_META = {
    "id": "chaos_eth_usdc", "label": "Chaos ETH/USD (unresolved)", "kind": "oracle",
    "description": "No verified Ethereum ETH/USD feed address for this historical window. "
                   "The verified contemporaneous Chaos candidate is ETHFI/USD, a different asset.",
}


def decode_feed(results, timestamp: int, expected_description: str) -> dict:
    if len(results) != 3 or not all(r.success for r in results):
        raise ValueError("feed call reverted or missing")
    decimals, = decode(["uint8"], bytes.fromhex(results[0].raw[2:]))
    description, = decode(["string"], bytes.fromhex(results[1].raw[2:]))
    if decimals != 8 or description != expected_description:
        raise ValueError("feed identity/decimals mismatch")
    round_id, answer, started_at, updated_at, answered_in_round = decode(
        ["uint80", "int256", "uint256", "uint256", "uint80"], bytes.fromhex(results[2].raw[2:])
    )
    if answer <= 0 or not 0 < updated_at <= timestamp or answered_in_round < round_id:
        raise ValueError("invalid answer, round or update timestamp")
    return {"answer": str(answer), "decimals": decimals, "description": description,
            "roundId": str(round_id), "answeredInRound": str(answered_in_round),
            "startedAt": started_at, "updatedAt": updated_at, "ageSeconds": timestamp - updated_at}


def cross_rate(eth: dict, usdc: dict) -> dict:
    # A 24h guard is explicit; it does not claim a feed updated every block.
    stale = max(eth["ageSeconds"], usdc["ageSeconds"]) > 86400
    ratio = Fraction(int(eth["answer"]) * 10 ** usdc["decimals"],
                     int(usdc["answer"]) * 10 ** eth["decimals"])
    return {"price": None if stale else float(ratio), "status": "stale" if stale else "ok",
            "reason": f"Update age: RedStone ETH {eth['ageSeconds']}s; Chainlink USDC {usdc['ageSeconds']}s",
            "ethUsd": eth, "usdcUsd": usdc,
            "ratioNumerator": str(ratio.numerator), "ratioDenominator": str(ratio.denominator)}


def merge_values(base: dict, canonical: list[dict], values: dict[int, dict]) -> dict:
    validate_source_dataset(base)
    if len(values) != len(canonical):
        raise ValueError("incomplete external feed rows")
    result = copy.deepcopy(base)
    for row, pin in zip(result["rows"], canonical, strict=True):
        if row["block"] != pin["block"] or row["blockHash"].lower() != pin["blockHash"].lower():
            raise ValueError("sidecar block identity mismatch")
        row["values"][SOURCE_ID] = values[row["block"]]
        row["values"][CHAOS_META["id"]] = {
            "price": None, "status": "unresolved", "reason": CHAOS_META["description"]}
    result["sources"] = [s for s in result["sources"]
                         if s["id"] not in (SOURCE_ID, CHAOS_META["id"])] + [META, CHAOS_META]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", help="comma-separated canonical pins; writes subset evidence only")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    canonical = validate_source_dataset(json.loads((ROOT / "frontend/public/october-sources.json").read_text()))
    requested = set(map(int, args.blocks.split(","))) if args.blocks else {r["block"] for r in canonical}
    if not requested or not requested <= {r["block"] for r in canonical}:
        raise ValueError("requested blocks outside canonical window")
    client = RpcClient(offline=args.offline)
    if client.chain_id() != 1:
        raise ValueError("Ethereum mainnet required")
    mc = Multicall3(client)
    specs = [CallSpec(address, "0x" + keccak(text=signature)[:4].hex(), signature)
             for address in [REDSTONE, USDC_USD]
             for signature in ["decimals()", "description()", "latestRoundData()"]]
    values = {}
    out = ROOT / "outputs/october-external-oracles"
    for row in canonical:
        if row["block"] not in requested:
            continue
        block = client.get_block(row["block"])
        if (block.hash.lower() != row["blockHash"].lower()
                or block.timestamp != int(datetime.fromisoformat(row["timestamp"]).timestamp())):
            raise ValueError("canonical header mismatch")
        results = mc.call(specs, block)
        atomic_write_json(out / f"{block.hash}.json", {
            "block": asdict(block), "manifest": MANIFEST, "calls": [asdict(r) for r in results]})
        try:
            eth = decode_feed(results[:3], block.timestamp, "RedStone Price Feed for ETH")
            usdc = decode_feed(results[3:], block.timestamp, "USDC / USD")
            values[block.number] = cross_rate(eth, usdc)
        except (ValueError, OverflowError) as exc:
            values[block.number] = {"price": None, "status": "invalid_feed", "reason": str(exc)}
    atomic_write_json(out / "latest-run.json", {"source": META, "values": values})
    if not args.blocks:
        target = ROOT / "frontend/public/october-oracle-references.json"
        atomic_write_json(target, merge_values(json.loads(target.read_text()), canonical, values))
    print(json.dumps({"blocks": len(values), "available": sum(v['status'] == 'ok' for v in values.values()),
                      "networkRequests": client.network_requests, "published": not bool(args.blocks)}))


if __name__ == "__main__":
    main()
