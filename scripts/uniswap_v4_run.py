"""Discover V4 ``Initialize`` events at one pinned block.

Offline is the default: it reuses cached RPC responses and refuses a cache
miss.  ``--online`` is intentionally explicit because the lead serializes all
archive acquisition.  Discovery never aliases native ETH (address zero) to
WETH.  It only writes the canonical inventory with ``--write-inventory``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from swaparch.adapters.uniswap_v4 import UniswapV4Adapter
from swaparch.adapters.uniswap_v4.adapter import (
    POOL_MANAGER,
    STATE_VIEW,
    ZERO_HOOK,
    m,
    pool_key_id,
)
from swaparch.core.protocols import Unsupported
from swaparch.core.types import NATIVE_ETH, CallSpec, PoolRecord, SupportStatus, Token, norm_address
from swaparch.discovery.uniswap_v3 import ENDPOINT_TOKENS
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data/discovery/1/uniswap_v4.json"
INITIALIZE_BLOCK = 21688329
V4_QUOTER = "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"
TOPIC_INITIALIZE = "0x" + keccak(
    text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
).hex()
SEL_QUOTE_EXACT_INPUT_SINGLE = "0x" + keccak(
    text="quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))"
)[:4].hex()
assert SEL_QUOTE_EXACT_INPUT_SINGLE == "0xaa9d21cb"
assert TOPIC_INITIALIZE == "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"


def _topic_address(address: str) -> str:
    return "0x" + norm_address(address)[2:].rjust(64, "0")


def _number(value: Any) -> int | None:
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else int(value)


def decode_initialize(log: dict[str, Any]) -> dict[str, Any]:
    """Decode the final-release event; only PoolId/currency0/currency1 index."""
    topics = [str(item).lower() for item in log["topics"]]
    if len(topics) != 4 or topics[0] != TOPIC_INITIALIZE:
        raise ValueError("not a V4 Initialize log")
    raw = str(log["data"])
    data = bytes.fromhex(raw.removeprefix("0x"))
    if len(data) != 160:
        raise ValueError(f"Initialize data expected 160 bytes, got {len(data)}")
    fee, spacing, hooks, sqrt_price_x96, tick = decode(
        ["uint24", "int24", "address", "uint160", "int24"], data
    )
    pool = topics[1]
    currency0, currency1 = (
        norm_address("0x" + topics[2][-40:]), norm_address("0x" + topics[3][-40:])
    )
    derived = pool_key_id(currency0, currency1, fee, spacing, hooks)
    if pool != derived:
        raise ValueError(f"Initialize PoolId mismatch: event {pool}, derived {derived}")
    return {
        "pool": pool,
        "currency0": currency0,
        "currency1": currency1,
        "fee": fee,
        "tick_spacing": spacing,
        "hooks": norm_address(hooks),
        "sqrt_price_x96": sqrt_price_x96,
        "tick": tick,
        "created_block": _number(log.get("blockNumber")),
        "block_hash": log.get("blockHash"),
        "tx_hash": log.get("transactionHash"),
        "log_index": _number(log.get("logIndex")),
    }


def filters(tokens: Iterable[str], from_block: int, to_block: int) -> list[tuple[str, list[str | None]]]:
    """Both indexed currency positions are scanned; no ordering is assumed."""
    out = []
    for currency in dict.fromkeys(norm_address(token) for token in tokens):
        out.extend((
            (currency, [TOPIC_INITIALIZE, None, _topic_address(currency), None]),
            (currency, [TOPIC_INITIALIZE, None, None, _topic_address(currency)]),
        ))
    return out


def trusted_tokens() -> dict[str, Token]:
    """Use existing inventory metadata; do not probe arbitrary log counterparts."""
    out = {NATIVE_ETH: Token(1, NATIVE_ETH, "ETH", 18)}
    for record in load_inventory("uniswap_v3"):
        for token in record.tokens:
            out.setdefault(token.address, token)
    return out


def qualification_records(events: Iterable[dict[str, Any]], tokens: dict[str, Token]) -> tuple[list[PoolRecord], dict[str, int]]:
    """Keep raw discoveries separate from the small trusted-token candidate set."""
    records: list[PoolRecord] = []
    skipped = {"unknown_currency": 0, "hooked": 0, "dynamic_fee": 0}
    for event in events:
        if event["currency0"] not in tokens or event["currency1"] not in tokens:
            skipped["unknown_currency"] += 1
            continue
        if event["hooks"] != ZERO_HOOK:
            skipped["hooked"] += 1
            continue
        if event["fee"] == m.DYNAMIC_FEE_FLAG:
            skipped["dynamic_fee"] += 1
            continue
        records.append(PoolRecord(
            family="uniswap_v4", chain=1, pool_id=f"uniswap_v4:{POOL_MANAGER}:{event['pool']}",
            deployment=POOL_MANAGER, pool=event["pool"],
            tokens=(tokens[event["currency0"]], tokens[event["currency1"]]),
            config={key: event[key] for key in ("currency0", "currency1", "fee", "tick_spacing", "hooks")},
            created_block=event["created_block"],
            discovered_by={"method": "logs:Initialize", "tx_hash": event["tx_hash"], "log_index": event["log_index"]},
            status=SupportStatus.DISCOVERED_UNSUPPORTED,
        ))
    return records, skipped


def _quoter_spec(record: PoolRecord, zero_for_one: bool, amount_in: int) -> CallSpec:
    if not 0 < amount_in <= (1 << 128) - 1:
        raise Unsupported(f"V4Quoter exact-input amount {amount_in} is outside uint128")
    key = record.config
    params = (
        (key["currency0"], key["currency1"], key["fee"], key["tick_spacing"], key["hooks"]),
        zero_for_one,
        amount_in,
        b"",
    )
    data = SEL_QUOTE_EXACT_INPUT_SINGLE + encode(
        ["((address,address,uint24,int24,address),bool,uint128,bytes)"], [params]
    ).hex()
    return CallSpec(V4_QUOTER, data, "univ4:quoter:exactInput")


def qualify_records(
    client: RpcClient,
    block,
    records: list[PoolRecord],
    *,
    min_liquidity: int = 1,
    max_per_pair: int = 5,
    discovery_reference: str | None = None,
) -> dict[str, Any]:
    """Acquire a trusted shortlist then compare local exact-input quotes to V4Quoter.

    StateView's static slot/liquidity pass screens inactive pools before any
    dependent bitmap/tick reads.  Quoter calls remain individual `eth_call`s.
    """
    if max_per_pair < 1:
        raise ValueError("max_per_pair must be positive")
    adapter, store = UniswapV4Adapter(), SnapshotStore()
    static_specs = [spec for record in records for spec in adapter.read_requests(record, block)]
    snapshot = store.extend(block, static_specs, client)
    active: list[tuple[int, PoolRecord]] = []
    screened: dict[str, str] = {}
    for record in records:
        try:
            raw = snapshot.get(adapter.liquidity_spec(record))
            liquidity = decode(["uint128"], bytes.fromhex(raw.raw[2:]))[0] if raw.success else 0
            if liquidity >= min_liquidity:
                active.append((liquidity, record))
            else:
                screened[record.pool_id] = f"liquidity {liquidity} below threshold {min_liquidity}"
        except (KeyError, Unsupported, ValueError, DecodingError):
            screened[record.pool_id] = "missing static StateView liquidity"
    shortlisted: list[PoolRecord] = []
    by_pair: dict[tuple[str, str], list[tuple[int, PoolRecord]]] = {}
    for liquidity, record in active:
        key = tuple(sorted(token.address for token in record.tokens))
        by_pair.setdefault(key, []).append((liquidity, record))
    for candidates in by_pair.values():
        candidates.sort(key=lambda item: (-item[0], item[1].pool))
        shortlisted.extend(record for _, record in candidates[:max_per_pair])
        for _, record in candidates[max_per_pair:]:
            screened[record.pool_id] = f"outside deterministic top {max_per_pair} liquidity shortlist for token pair"
    snapshot, acquisition = acquire({adapter.family: adapter}, shortlisted, block, store, client)
    rows = []
    qualified_states = []
    for record in shortlisted:
        row: dict[str, Any] = {"pool_id": record.pool_id, "supported": False, "checks": []}
        if record.pool_id in acquisition.unsupported:
            row["reason"] = acquisition.unsupported[record.pool_id]
            rows.append(row)
            continue
        try:
            state = adapter.load_state(record, snapshot)
            positives = [0, 0]
            mismatch = False
            for direction, (token_in, token_out) in enumerate(((state.token0, state.token1), (state.token1, state.token0))):
                for amount in (1, 10 ** token_in.decimals, 100 * 10 ** token_in.decimals):
                    check: dict[str, Any] = {"token_in": token_in.address, "token_out": token_out.address, "amount_in": amount}
                    try:
                        local = state.quote_exact_in(token_in.address, token_out.address, amount)
                        quoter = _quoter_spec(record, direction == 0, amount)
                        result = client.eth_call(quoter.to, quoter.data, block)
                        check.update(local_out=local, success=result.success, raw=result.raw, via=result.via)
                        if result.success:
                            quoted = decode(["uint256", "uint256"], bytes.fromhex(result.raw[2:]))[0]
                            check["quoter_out"] = quoted
                            check["matched"] = quoted == local
                            mismatch |= quoted != local
                            positives[direction] += int(quoted > 0 and quoted == local)
                    except (Unsupported, ValueError, TypeError, DecodingError) as exc:
                        check["reason"] = str(exc)
                    row["checks"].append(check)
            row["supported"] = not mismatch and positives[0] >= 2 and positives[1] >= 2
            if not row["supported"]:
                row["reason"] = (
                    "exact-input Quoter mismatch" if mismatch
                    else f"need >=2 positive exact-input Quoter matches per direction; got {positives}"
                )
            elif row["supported"]:
                qualified_states.append((record, state))
        except Unsupported as exc:
            row["reason"] = str(exc)
        rows.append(row)
    parity = None
    parity_candidate = next(((record, state) for record, state in qualified_states if state.tick_liquidity_net), None)
    if qualified_states and parity_candidate is None:
        parity = {"passed": False, "reason": "no initialized tick available for StateView parity"}
    elif parity_candidate:
        parity = _stateview_parity(client, block, snapshot, adapter, *parity_candidate)
    if parity is not None and not parity["passed"]:
        for row in rows:
            if row["supported"]:
                row.update(supported=False, reason="StateView batch/direct raw-byte parity failed")
    report = {"block": block.number, "block_hash": block.hash, "min_liquidity": min_liquidity,
            "max_per_pair": max_per_pair, "static_active": len(active), "shortlisted": len(shortlisted),
            "screened": screened, "acquisition_failures": acquisition.unsupported,
            "network_requests": client.network_requests, "parity": parity, "rows": rows,
            "records": [_record_identity(record) for record in shortlisted],
            "trusted_discovery_reference": discovery_reference}
    report["evidence"] = _persist_qualification(report)
    return report


def _stateview_parity(client: RpcClient, block, snapshot, adapter: UniswapV4Adapter, record: PoolRecord, state) -> dict[str, Any]:
    """Compare one loaded pool's batched StateView bytes to fresh individual calls."""
    bitmap_word = min(state.tick_bitmap)
    tick = min(state.tick_liquidity_net)
    specs = [adapter.slot0_spec(record), adapter.liquidity_spec(record), adapter.bitmap_spec(record, bitmap_word), adapter.tick_spec(record, tick)]
    direct_root = ROOT / "data/validation/multicall-parity/uniswap_v4/direct-cache"
    direct = RpcClient(cache_root=direct_root, offline=client.offline)
    rows = []
    for spec in specs:
        batched = snapshot.get(spec)
        individual = direct.eth_call(spec.to, spec.data, block)
        rows.append({"tag": spec.tag, "to": spec.to, "data": spec.data, "batched": batched.raw,
                     "individual": individual.raw, "batch_success": batched.success,
                     "individual_success": individual.success,
                     "matched": batched.success == individual.success and batched.raw == individual.raw})
    report = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
              "pool_id": record.pool_id, "state_view": STATE_VIEW, "rows": rows,
              "passed": all(row["matched"] for row in rows),
              "direct_network_requests": direct.network_requests,
              "total_network_requests": client.network_requests + direct.network_requests}
    path = ROOT / "data/validation/multicall-parity/uniswap_v4" / f"{block.hash}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    report["evidence"] = str(path.relative_to(ROOT))
    return report


def _record_identity(record: PoolRecord) -> dict[str, Any]:
    return {"pool_id": record.pool_id, "pool": record.pool,
            "tokens": [token.address for token in record.tokens], "config": dict(record.config),
            "created_block": record.created_block}


def _persist_qualification(report: dict[str, Any]) -> str:
    path = ROOT / "data/validation/uniswap_v4" / f"{report['block_hash']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return str(path.relative_to(ROOT))


def qualify_block(
    number: int,
    *,
    online: bool = False,
    write_inventory: bool = False,
    discovery_report: Path | None = None,
    max_per_pair: int = 5,
) -> dict[str, Any]:
    """Qualify one block and return a compact historical-study stage summary."""
    discovered = run_block(number, online=online, write_inventory=False, discovery_report=discovery_report)
    client = RpcClient(offline=not online)
    records, skipped = qualification_records(discovered["pools"], trusted_tokens())
    qualification = qualify_records(
        client, client.get_block(number), records, max_per_pair=max_per_pair,
        discovery_reference=str(discovery_report) if discovery_report else None,
    )
    discovered["qualification"] = qualification
    if write_inventory:
        _write_inventory(discovered)
    supported = sum(row["supported"] for row in qualification["rows"])
    direct_requests = (qualification.get("parity") or {}).get("direct_network_requests", 0)
    return {
        "block": number, "block_hash": qualification["block_hash"],
        "discovered_identities": len(discovered["pools"]), "trusted_candidates": len(records),
        "pre_screen_skipped": skipped, "static_active": qualification["static_active"],
        "shortlisted": qualification["shortlisted"], "supported": supported,
        "unsupported": len(qualification["rows"]) - supported, "parity": qualification["parity"],
        "network_requests": {"discovery": discovered["network_requests"],
                             "qualification": qualification["network_requests"],
                             "direct_parity": direct_requests,
                             "total": discovered["network_requests"] + qualification["network_requests"] + direct_requests},
        "evidence": qualification["evidence"],
    }


def run_block(
    number: int,
    *,
    online: bool = False,
    from_block: int = INITIALIZE_BLOCK,
    endpoint_tokens: Iterable[str] | None = None,
    chunk: int = 100_000,
    write_inventory: bool = False,
    discovery_report: Path | None = None,
) -> dict[str, Any]:
    """Return decoded endpoint-adjacent pools at ``number`` using cached/raw RPC.

    The report is intentionally discovery-only.  A pool is never marked
    supported here: that requires StateView acquisition and independent V4Quoter
    exact-input checks at the same hash.
    """
    if from_block < INITIALIZE_BLOCK or from_block > number:
        raise ValueError(f"from_block must be in [{INITIALIZE_BLOCK}, {number}]")
    client = RpcClient(offline=not online)
    block = client.get_block(number)
    tokens = list(endpoint_tokens or ENDPOINT_TOKENS.values())
    if NATIVE_ETH not in tokens:
        tokens.append(NATIVE_ETH)
    coverage = []
    if discovery_report:
        source = json.loads(discovery_report.read_text())
        if source.get("pool_manager") != POOL_MANAGER or source.get("topic_initialize") != TOPIC_INITIALIZE:
            raise ValueError("discovery report is not canonical V4 Initialize coverage")
        if int(source.get("block", 0)) < number:
            raise ValueError("discovery report ends before requested block")
        source_block = client.get_block(int(source["block"]))
        if source.get("block_hash", "").lower() != source_block.hash:
            raise ValueError("discovery report source block hash does not match the pinned header")
        expected = {tuple(topics) for _, topics in filters(tokens, INITIALIZE_BLOCK, source_block.number)}
        observed = {
            tuple(row.get("topics", [])) for row in source.get("coverage", [])
            if row.get("from_block") == INITIALIZE_BLOCK and row.get("to_block") == source_block.number
        }
        if not expected <= observed:
            raise ValueError("discovery report does not cover every requested Initialize currency filter")
        pools = [row for row in source["pools"] if row.get("created_block") is not None and row["created_block"] <= number]
        coverage.append({"method": "reused Initialize discovery report", "source": str(discovery_report),
                         "source_to_block": source["block"], "filtered_to_block": number})
    else:
        decoded: dict[str, dict[str, Any]] = {}
        for currency, topics in filters(tokens, from_block, number):
            logs = client.get_logs(POOL_MANAGER, topics, from_block, number, chunk=chunk)
            coverage.append({"currency": currency, "topics": topics, "from_block": from_block,
                             "to_block": number, "to_block_hash": block.hash, "logs": len(logs)})
            for log in logs:
                event = decode_initialize(log)
                decoded[event["pool"]] = event
        pools = list(decoded.values())
    pools.sort(key=lambda row: (row["created_block"] or 0, row["pool"]))
    report = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
              "pool_manager": POOL_MANAGER, "topic_initialize": TOPIC_INITIALIZE,
              "coverage": coverage, "pools": pools,
              "hookless_static": sum(row["hooks"] == ZERO_HOOK and row["fee"] != m.DYNAMIC_FEE_FLAG for row in pools),
              "network_requests": client.network_requests,
              "admission": "discovery only; no pool is supported without StateView and independent exact-input Quoter checks"}
    if write_inventory:
        raise ValueError("inventory writes require qualification; use the CLI --qualify --write-inventory")
    return report


def _write_inventory(report: dict[str, Any]) -> None:
    """Merge only quote-qualified, parity-proven records into the inventory."""
    qualification = report.get("qualification")
    if not qualification:
        raise ValueError("inventory writes require qualification")
    parity_passed = bool((qualification.get("parity") or {}).get("passed"))
    inventory = json.loads(INVENTORY.read_text())
    events = {f"uniswap_v4:{POOL_MANAGER}:{event['pool']}": event for event in report["pools"]}
    tokens = trusted_tokens()
    known = {row["pool_id"]: row for row in inventory["pools"]}
    current_hash = report["block_hash"]
    for result in qualification["rows"]:
        if not result["supported"] or not parity_passed:
            existing = known.get(result["pool_id"])
            if existing:
                hashes = set(existing.get("config", {}).get("validated_block_hashes", []))
                hashes.discard(current_hash)
                existing.setdefault("config", {})["validated_block_hashes"] = sorted(hashes)
                existing["status"] = "supported" if hashes else "discovered_unsupported"
                if not hashes:
                    existing["notes"] = result.get("reason", "no current qualified V4 quote validation")
            continue
        event = events[result["pool_id"]]
        token0, token1 = tokens[event["currency0"]], tokens[event["currency1"]]
        existing = known.get(result["pool_id"], {})
        hashes = set(existing.get("config", {}).get("validated_block_hashes", []))
        hashes.add(current_hash)
        known[result["pool_id"]] = {
            "family": "uniswap_v4", "chain": 1, "pool_id": result["pool_id"], "deployment": POOL_MANAGER,
            "pool": event["pool"], "tokens": [
                {"address": token0.address, "symbol": token0.symbol, "decimals": token0.decimals},
                {"address": token1.address, "symbol": token1.symbol, "decimals": token1.decimals},
            ],
            "config": {"currency0": event["currency0"], "currency1": event["currency1"], "fee": event["fee"],
                       "tick_spacing": event["tick_spacing"], "hooks": event["hooks"],
                       "validated_block_hashes": sorted(hashes)},
            "created_block": event["created_block"], "discovered_by": {"method": "logs:Initialize",
                "tx_hash": event["tx_hash"], "log_index": event["log_index"]},
            "status": "supported", "notes": "StateView parity and bidirectional exact-input V4Quoter checks recorded.",
        }
    inventory["pools"] = sorted(known.values(), key=lambda row: row["pool_id"])
    inventory["status"] = "supported" if any(
        row.get("config", {}).get("validated_block_hashes") for row in inventory["pools"]
    ) else "discovered_unsupported"
    INVENTORY.write_text(json.dumps(inventory, indent=1) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("block", type=int, nargs="?", default=25_896_003)
    parser.add_argument("--online", action="store_true", help="allow archive RPC cache misses")
    parser.add_argument("--from-block", type=int, default=INITIALIZE_BLOCK)
    parser.add_argument("--endpoint-token", action="append", default=[], help="additional currency address")
    parser.add_argument("--chunk", type=int, default=100_000)
    parser.add_argument("--qualify", action="store_true", help="run bounded StateView and exact-input Quoter checks")
    parser.add_argument("--max-per-pair", type=int, default=5)
    parser.add_argument("--discovery-report", type=Path, help="reuse a complete later Initialize report, filtered by creation block")
    parser.add_argument("--write-inventory", action="store_true")
    args = parser.parse_args()
    if args.write_inventory and not args.qualify:
        parser.error("--write-inventory requires --qualify")
    if args.qualify:
        print(json.dumps(qualify_block(
            args.block, online=args.online, write_inventory=args.write_inventory,
            discovery_report=args.discovery_report, max_per_pair=args.max_per_pair,
        ), indent=2))
        return
    report = run_block(args.block, online=args.online, from_block=args.from_block,
                       endpoint_tokens=[*ENDPOINT_TOKENS.values(), *args.endpoint_token],
                       chunk=args.chunk, write_inventory=False,
                       discovery_report=args.discovery_report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
