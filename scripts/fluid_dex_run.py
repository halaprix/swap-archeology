"""Discover and qualify Fluid DEX T1 pre-operation quotes at one pinned block.

Default operation is offline and reads only already-cached headers/calls.  The
designated RPC owner must pass ``--online`` to acquire a missing snapshot.
Discovery never changes the shared inventory unless ``--write-inventory`` is
explicitly supplied; produced records remain ``discovered_unsupported`` because
the local state is deliberately single-use until full Liquidity post-trade
reconstruction exists.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from swaparch.adapters.fluid_dex import NATIVE_SENTINEL, FluidDexAdapter, resolver_for_block
from swaparch.core.protocols import Unsupported
from swaparch.core.types import NATIVE_ETH, CallSpec, PoolRecord, SupportStatus, Token, norm_address
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data/discovery/1/fluid_dex.json"
FACTORY = "0x91716c4eda1fb55e84bf8b4c7085f84285c19085"
OLD_RESOLVER = "0xc93876c0eed99645dd53937b25433e311881a27c"
NEW_RESOLVER = "0x05bd8269a20c472b148246de20e6852091bf16ff"
OLD_RESOLVER_CREATED_BLOCK = 22487434
NEW_RESOLVER_CREATED_BLOCK = 23881741
LIQUIDITY = "0x52aa899454998be5b000ad077a46bbe360f4e497"


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_ALL_POOLS = _selector("getAllPools()")
SEL_SYMBOL = _selector("symbol()")
SEL_DECIMALS = _selector("decimals()")
SEL_ESTIMATE_SWAP_IN = _selector("estimateSwapIn(address,bool,uint256,uint256)")
assert (SEL_ALL_POOLS, SEL_SYMBOL, SEL_DECIMALS, SEL_ESTIMATE_SWAP_IN) == (
    "0xd88ff1f4", "0x95d89b41", "0x313ce567", "0xbb39e3a1",
), "Fluid runner selector table drifted"


def resolver_for(block_number: int) -> str:
    return resolver_for_block(block_number)


def _decode(result, types: list[str], what: str):
    if not result.success:
        raise Unsupported(f"Fluid DEX failed {what}")
    try:
        return abi_decode(types, bytes.fromhex(result.raw.removeprefix("0x")))
    except (TypeError, ValueError) as exc:
        raise Unsupported(f"Fluid DEX malformed {what}: {exc}") from None


def _token_records(store, snapshot, client, block, pools: list[tuple[str, str, str, int]]) -> tuple[dict[str, Token], object, dict[str, str]]:
    addresses = sorted({norm_address(address) for pool in pools for address in pool[1:3]} - {NATIVE_SENTINEL})
    specs = [
        spec for address in addresses for spec in (
            CallSpec(address, SEL_SYMBOL, "fluid_dex:symbol"),
            CallSpec(address, SEL_DECIMALS, "fluid_dex:decimals"),
        )
    ]
    # The caller has already selected online/offline mode; a store extension is
    # valid in either case because offline RpcClient fails closed on a miss.
    snapshot = store.extend(block, specs, client)
    tokens: dict[str, Token] = {}
    failures: dict[str, str] = {}
    tokens[NATIVE_SENTINEL] = Token(chain=block.chain, address=NATIVE_ETH, symbol="ETH", decimals=18)
    for address in addresses:
        try:
            symbol = _decode(snapshot.get(CallSpec(address, SEL_SYMBOL, "fluid_dex:symbol")), ["string"], "token symbol")[0]
            decimals = _decode(snapshot.get(CallSpec(address, SEL_DECIMALS, "fluid_dex:decimals")), ["uint8"], "token decimals")[0]
            tokens[address] = Token(chain=block.chain, address=address, symbol=symbol, decimals=decimals)
        except Unsupported as exc:
            failures[address] = str(exc)
    return tokens, snapshot, failures


def _records(block, resolver: str, pools, tokens: dict[str, Token]) -> tuple[list[PoolRecord], dict[str, str]]:
    records, excluded = [], {}
    for pool, token0, token1, fee in pools:
        pool, token0, token1 = norm_address(pool), norm_address(token0), norm_address(token1)
        if token0 not in tokens or token1 not in tokens:
            excluded[pool] = "token metadata unavailable"
            continue
        records.append(
        PoolRecord(
            family="fluid_dex", chain=block.chain,
            pool_id=f"fluid_dex:{FACTORY}:{pool}", deployment=FACTORY, pool=pool,
            tokens=(tokens[token0], tokens[token1]),
            config={"resolver": resolver, "liquidity": LIQUIDITY, "fee": fee,
                    "resolver_observations": {block.hash: resolver},
                    "resolver_bounds": {"old_through": NEW_RESOLVER_CREATED_BLOCK - 1,
                                        "new_from": NEW_RESOLVER_CREATED_BLOCK},
                    "quote_model": "T1 resolver-normalized pre-operation single-use"},
            created_block=None,
            discovered_by={"method": "resolver:getAllPools", "deployed_by_block": block.number,
                           "evidence": [f"data/snapshots/{block.chain}/{block.hash}"]},
            status=SupportStatus.DISCOVERED_UNSUPPORTED,
            notes="T1 discovery record; explicit qualification and post-trade state work remain.",
        )
        )
    return records, excluded


def _reference_checks(client, block, resolver: str, states) -> list[dict]:
    checks: list[dict] = []
    for state in states:
        for zero_for_one, token_in, token_out in (
            (True, state.token0, state.token1), (False, state.token1, state.token0),
        ):
            for units in (1, 100):
                amount = units * 10**token_in.decimals
                data = SEL_ESTIMATE_SWAP_IN + abi_encode(
                    ["address", "bool", "uint256", "uint256"], [state.record.pool, zero_for_one, amount, 0]
                ).hex()
                spec = CallSpec(resolver, data, "fluid_dex:estimateSwapIn")
                reference = client.eth_call(spec.to, spec.data, block)
                row = {"pool_id": state.record.pool_id, "token_in": token_in.address,
                       "token_out": token_out.address, "amount_in": amount,
                       "reference": asdict(reference)}
                try:
                    row["reference_out"] = _decode(reference, ["uint256"], "estimateSwapIn")[0]
                    row["local_out"] = state.quote_exact_in(token_in.address, token_out.address, amount)
                    row["matched"] = row["local_out"] == row["reference_out"]
                except Unsupported as exc:
                    row["reason"] = str(exc)
                    row["matched"] = False
                    # Resolver returns zero for its own cap/oracle exclusions;
                    # a local refusal is consistent with that no-quote result,
                    # but deliberately remains distinct from integer equality.
                    row["consistent_exclusion"] = (
                        reference.success and row.get("reference_out") == 0
                    )
                checks.append(row)
    return checks


def _qualification(records, checks: list[dict]) -> dict[str, dict]:
    """Require at least one positive, exact resolver match in both directions."""
    results: dict[str, dict] = {}
    for record in records:
        directions = {
            f"{record.tokens[0].address}->{record.tokens[1].address}": {
                "positive_reference": 0, "positive_matches": 0,
                "consistent_exclusions": 0, "numerical_failures": 0,
            },
            f"{record.tokens[1].address}->{record.tokens[0].address}": {
                "positive_reference": 0, "positive_matches": 0,
                "consistent_exclusions": 0, "numerical_failures": 0,
            },
        }
        for check in (row for row in checks if row["pool_id"] == record.pool_id):
            direction = f"{check['token_in']}->{check['token_out']}"
            row = directions[direction]
            if check.get("reference_out", 0) > 0:
                row["positive_reference"] += 1
                row["positive_matches"] += int(check.get("matched", False))
                row["numerical_failures"] += int(not check.get("matched", False))
            else:
                row["consistent_exclusions"] += int(check.get("consistent_exclusion", False))
        valid = all(
            row["positive_reference"] > 0 and row["positive_reference"] == row["positive_matches"]
            for row in directions.values()
        )
        results[record.pool_id] = {"directions": directions, "pre_operation_quote_qualified": valid}
    return results


def _parity(client, block, adapter, state, *, online: bool) -> tuple[list[dict], int]:
    """Compare the Multicall snapshot bytes with uncached direct eth_calls."""
    if state is None:
        return [], 0
    direct = RpcClient(cache_root=ROOT / "data/rpc-cache-fluid-direct", offline=not online)
    direct_block = direct.get_block(block.number)
    if direct_block.hash != block.hash:
        raise RuntimeError("Fluid direct-call parity header hash differs from acquisition header")
    snapshot = SnapshotStore().load(block.chain, block.hash)
    rows = []
    for spec in adapter.read_requests(state.record, block):
        batched = snapshot.get(spec)
        individual = direct.eth_call(spec.to, spec.data, direct_block)
        rows.append({"tag": spec.tag, "to": spec.to, "data": spec.data,
                     "batched": asdict(batched), "individual": asdict(individual),
                     "matched": batched.success == individual.success and batched.raw == individual.raw})
    return rows, direct.network_requests


def run_block(number: int, *, online: bool = False, write_inventory: bool = False) -> dict:
    """Acquire/replay one pin. Online mode is reserved for the serialized RPC owner."""
    if number < OLD_RESOLVER_CREATED_BLOCK:
        return {"block": number, "records": 0, "reason": "Fluid T1 reserves resolver not deployed at block"}
    client = RpcClient(offline=not online)
    block = client.get_block(number)
    store = SnapshotStore()
    resolver = resolver_for(block.number)
    all_pools_spec = CallSpec(resolver, SEL_ALL_POOLS, "fluid_dex:getAllPools")
    snapshot = store.extend(block, [all_pools_spec], client)
    pools = _decode(snapshot.get(all_pools_spec), ["(address,address,address,uint256)[]"], "getAllPools")[0]
    tokens, snapshot, token_failures = _token_records(store, snapshot, client, block, pools)
    records, metadata_excluded = _records(block, resolver, pools, tokens)
    adapter = FluidDexAdapter()
    snapshot, acquisition = acquire({adapter.family: adapter}, records, block, store, client)
    states, excluded = [], {**metadata_excluded}
    for record in records:
        if record.pool_id in acquisition.unsupported:
            excluded[record.pool_id] = acquisition.unsupported[record.pool_id]
            continue
        try:
            states.append(adapter.load_state(record, snapshot))
        except Unsupported as exc:
            excluded[record.pool_id] = str(exc)
    checks = _reference_checks(client, block, resolver, states)
    qualification = _qualification(records, checks)
    parity, direct_requests = _parity(
        client, block, adapter, next((state for state in states if not state.paused), None), online=online
    )
    parity_passed = bool(parity) and all(row["matched"] for row in parity)
    for row in qualification.values():
        row["batch_direct_parity"] = parity_passed
        row["pre_operation_quote_qualified"] &= parity_passed
    out = ROOT / f"data/validation/fluid-dex/{block.hash}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "block": asdict(block), "resolver": resolver, "factory": FACTORY, "liquidity": LIQUIDITY,
        "records": [{"pool_id": r.pool_id, "pool": r.pool, "tokens": [asdict(t) for t in r.tokens],
                     "config": dict(r.config)} for r in records],
        "token_metadata_unsupported": token_failures, "acquisition_unsupported": acquisition.unsupported,
        "load_unsupported": excluded,
        "reference_checks": checks, "batch_direct_parity": parity,
        "pool_qualification": qualification,
        "network_requests": client.network_requests,
        "direct_parity_network_requests": direct_requests,
        "total_network_requests": client.network_requests + direct_requests,
        "scope": "T1 resolver-normalized ADDRESS_DEAD quote checks. No Liquidity operation, utilization, oracle persistence, or post-trade settlement admission.",
    }, indent=2) + "\n")
    if write_inventory:
        inventory = json.loads(INVENTORY.read_text())
        existing = {row["pool_id"]: row for row in inventory.get("pools", [])}
        merged = []
        for record in records:
            qualified = qualification[record.pool_id]["pre_operation_quote_qualified"]
            row = {"family": record.family, "chain": record.chain, "pool_id": record.pool_id,
                   "deployment": record.deployment, "pool": record.pool,
                   "tokens": [asdict(t) for t in record.tokens], "config": dict(record.config),
                   "created_block": record.created_block, "discovered_by": dict(record.discovered_by),
                   "status": "supported" if qualified else record.status.value,
                   "notes": ("Per-hash Fluid T1 pre-operation resolver quote qualification; "
                             "single-use state, no settlement claim." if qualified else record.notes)}
            prior = existing.pop(record.pool_id, None)
            if prior:
                prior_config = dict(prior.get("config", {}))
                observations = dict(prior_config.get("resolver_observations", {}))
                observations.update(row["config"]["resolver_observations"])
                row["config"] = {**prior_config, **row["config"], "resolver_observations": observations}
                hashes = set(prior_config.get("validated_block_hashes", []))
                hashes.discard(block.hash)
                if qualified:
                    hashes.add(block.hash)
                row["config"]["validated_block_hashes"] = sorted(hashes)
                prior_discovery = dict(prior.get("discovered_by", {}))
                prior_bound = prior_discovery.get("deployed_by_block")
                current_bound = row["discovered_by"]["deployed_by_block"]
                if type(prior_bound) is int:
                    row["discovered_by"]["deployed_by_block"] = min(prior_bound, current_bound)
                row["discovered_by"]["evidence"] = sorted(set(
                    prior_discovery.get("evidence", []) + row["discovered_by"]["evidence"]
                ))
                row["created_block"] = prior.get("created_block")
                row["status"] = "supported" if hashes else "discovered_unsupported"
                if not hashes:
                    row["notes"] = record.notes
            else:
                row["config"]["validated_block_hashes"] = [block.hash] if qualified else []
                row["status"] = "supported" if qualified else "discovered_unsupported"
            merged.append(row)
        inventory["pools"] = sorted(merged + list(existing.values()), key=lambda row: row["pool_id"])
        inventory["status"] = ("supported" if any(row["status"] == "supported"
                                                  for row in inventory["pools"])
                               else "discovered_unsupported")
        INVENTORY.write_text(json.dumps(inventory, indent=1) + "\n")
    return {"block": number, "resolver": resolver, "records": len(records), "states": len(states),
            "excluded": len(excluded), "checks": len(checks), "matched": sum(c["matched"] for c in checks),
            "consistent_exclusions": sum(c.get("consistent_exclusion", False) for c in checks),
            "batch_direct_parity": parity_passed,
            "qualified": sum(row["pre_operation_quote_qualified"] for row in qualification.values()),
            "network_requests": client.network_requests, "direct_parity_network_requests": direct_requests,
            "total_network_requests": client.network_requests + direct_requests, "evidence": str(out)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("block", type=int, nargs="?", default=25896003)
    parser.add_argument("--online", action="store_true", help="allow cache misses to use the configured archive RPC")
    parser.add_argument("--write-inventory", action="store_true", help="explicitly replace Fluid discovery records")
    args = parser.parse_args()
    print(json.dumps(run_block(args.block, online=args.online, write_inventory=args.write_inventory), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
