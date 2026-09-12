"""Command-line entry points for pinned infrastructure checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from swaparch.core.types import CallSpec, Plan, Step, Token, TradeRequest
from swaparch.rpc.client import PROJECT_ROOT, RpcClient, RpcError, rpc_available
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore

EVIDENCE = PROJECT_ROOT / "evidence/crash-rescue-simulation/standing-prices.json"
SNAPSHOT_ROOT = PROJECT_ROOT / "data/snapshots"
PREPARED_CONTEXT_LIMIT = 2


@dataclass(frozen=True)
class PreparedQuoteContext:
    """Offline-reusable inventory/snapshot/state preparation for one quote universe."""

    inventories: tuple[dict[str, Any], ...]
    adapter_families: frozenset[str]
    records: tuple[Any, ...]
    tokens: dict[str, Any]
    block: Any
    states: tuple[Any, ...]
    unavailable: tuple[dict[str, Any], ...]


_PREPARED_CONTEXTS: OrderedDict[tuple[str, ...], PreparedQuoteContext] = OrderedDict()
_PARSED_INVENTORIES: OrderedDict[str, tuple[dict[str, Any], ...]] = OrderedDict()


def _identity(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _snapshot_identity(chain: int, block_hash: str) -> str:
    directory = SNAPSHOT_ROOT / str(chain) / block_hash.lower()
    return _identity([directory / "header.json", directory / "calls.json.gz"])


def _cached_context(
    key: tuple[str, ...], factory: Callable[[], PreparedQuoteContext]
) -> PreparedQuoteContext:
    cached = _PREPARED_CONTEXTS.get(key)
    if cached is not None:
        _PREPARED_CONTEXTS.move_to_end(key)
        return cached
    context = factory()
    _PREPARED_CONTEXTS[key] = context
    _PREPARED_CONTEXTS.move_to_end(key)
    while len(_PREPARED_CONTEXTS) > PREPARED_CONTEXT_LIMIT:
        _PREPARED_CONTEXTS.popitem(last=False)
    return context


def _inventory_inputs() -> tuple[list[Path], str, tuple[dict[str, Any], ...]]:
    from swaparch.universe import DISCOVERY_ROOT

    paths = sorted((DISCOVERY_ROOT / "1").glob("*.json"))
    if not paths:
        raise FileNotFoundError(
            "no discovery inventories found under SWAPARCH_ROOT/data/discovery/1; "
            "set SWAPARCH_ROOT to a prepared Swap Archeology data root"
        )
    identity = _identity(paths)
    inventories = _PARSED_INVENTORIES.get(identity)
    if inventories is None:
        inventories = tuple(json.loads(path.read_text()) for path in paths)
        _PARSED_INVENTORIES[identity] = inventories
        while len(_PARSED_INVENTORIES) > PREPARED_CONTEXT_LIMIT:
            _PARSED_INVENTORIES.popitem(last=False)
    else:
        _PARSED_INVENTORIES.move_to_end(identity)
    return paths, identity, inventories


def _prepared_quote_context(
    number: int, selected_families: set[str], offline: bool, *,
    inventory_paths: list[Path] | None = None,
    inventory_id: str | None = None,
    inventories: tuple[dict[str, Any], ...] | None = None,
) -> tuple[PreparedQuoteContext, RpcClient]:
    """Load immutable quote state once, keyed by exact input artifacts.

    The snapshot identity is recomputed after acquisition because dependent
    adapter reads may extend it. A later inventory edit or snapshot extension
    therefore selects a new context without any cache clearing protocol.
    """
    from swaparch.universe import (
        acquire,
        activation_reason,
        implemented_adapters,
        load_states,
        pool_record_from_json,
    )

    if inventory_paths is None or inventory_id is None or inventories is None:
        inventory_paths, inventory_id, inventories = _inventory_inputs()
    adapters = implemented_adapters()
    records = tuple(pool_record_from_json(row) for inventory in inventories
                    if inventory["family"] in adapters for row in inventory.get("pools", []))
    tokens = {token.address: token for record in records for token in record.tokens}
    client = RpcClient(offline=True) if offline else RpcClient()
    block = client.get_block(number)
    family_id = hashlib.sha256("\0".join(sorted(selected_families)).encode()).hexdigest()

    def build() -> PreparedQuoteContext:
        live, unavailable = [], []
        for record in records:
            reason = ("excluded by source selection" if record.family not in selected_families
                      else activation_reason(record, number))
            if reason is None and record.status.value != "supported":
                reason = record.notes or record.status.value
            if reason is None and block.hash not in record.config.get("validated_block_hashes", []):
                reason = "no recorded quote validation at this block hash"
            if reason:
                unavailable.append({"family": record.family, "pool": record.pool,
                                    "status": "not_deployed_at_block" if reason == "not_deployed_at_block"
                                    else "discovered_unsupported", "reason": reason})
            else:
                live.append(record)
        snapshot, acquisition = acquire(adapters, live, block, SnapshotStore(), client)
        states, failed = load_states(adapters, live, snapshot, acquisition.unsupported)
        unavailable.extend({"family": record.family, "pool": record.pool,
                            "status": "discovered_unsupported", "reason": reason}
                           for record, reason in failed)
        return PreparedQuoteContext(
            inventories, frozenset(adapters), records, tokens, block, tuple(states), tuple(unavailable)
        )

    before = _snapshot_identity(block.chain, block.hash)
    key = (block.hash, inventory_id, before, family_id)
    context = _PREPARED_CONTEXTS.get(key)
    if context is not None:
        _PREPARED_CONTEXTS.move_to_end(key)
        return context, client
    context = build()
    after = _snapshot_identity(block.chain, block.hash)
    final_key = (block.hash, inventory_id, after, family_id)
    return _cached_context(final_key, lambda: context), client


def parse_amount(value: str, decimals: int) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise ValueError("amount must be a decimal number") from None
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("amount must be positive and finite")
    numerator, denominator = decimal.as_integer_ratio()
    amount, remainder = divmod(numerator * 10 ** decimals, denominator)
    if remainder:
        raise ValueError(f"amount has more than {decimals} decimal places")
    if amount >= 2 ** 255:
        raise ValueError("amount exceeds the supported positive int256 range")
    return amount


def quote_command(number: int, token_in: str, token_out: str, amount: str,
                  grid_parts: int, output: Path | None = None, *, solver_name: str = "baseline",
                  max_steps: int = 8, beam_width: int = 128, max_expansions: int = 5000,
                  families: tuple[str, ...] | None = None, offline: bool = False,
                  prepared_context: PreparedQuoteContext | None = None,
                  report_annotations: dict[str, Any] | None = None,
                  allow_psm_dai_refund: bool = False) -> int:
    from swaparch.evaluator.evaluate import Evaluator
    from swaparch.solver.baseline import BaselineSolver

    inventory_paths, inventory_id, inventories = _inventory_inputs()
    if prepared_context is not None:
        inventories = prepared_context.inventories
    known_families = {inventory['family'] for inventory in inventories}
    selected_families = known_families if families is None else set(families)
    if not selected_families or selected_families - known_families:
        raise ValueError(f"unknown or empty source selection: {sorted(selected_families - known_families)}")
    preview_tokens = {
        row["address"].lower(): Token(1, row["address"], row["symbol"], int(row["decimals"]))
        for inventory in inventories for pool in inventory.get("pools", []) for row in pool.get("tokens", [])
    }

    def resolve(value):
        matches = [token for token in preview_tokens.values()
                   if value.lower() in (token.address, token.symbol.lower())]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous token {value}")
        return matches[0]

    preview_source, preview_destination = resolve(token_in), resolve(token_out)
    parsed_amount = parse_amount(amount, preview_source.decimals)
    if solver_name == "baseline":
        solver = BaselineSolver(grid_parts=grid_parts)
    elif solver_name in ("search", "dual"):
        from swaparch.solver.dual import DualSolver
        from swaparch.solver.search import GeneralSearchSolver

        cls = GeneralSearchSolver if solver_name == "search" else DualSolver
        solver = cls(grid_parts=grid_parts, max_steps=max_steps,
                     beam_width=beam_width, max_expansions=max_expansions)
    else:
        raise ValueError(f"unknown solver {solver_name}")
    if preview_source.address == preview_destination.address:
        raise ValueError("input and output tokens must differ")
    if prepared_context is None:
        context, client = _prepared_quote_context(
            number, selected_families, offline, inventory_paths=inventory_paths,
            inventory_id=inventory_id, inventories=inventories,
        )
    else:
        context, client = prepared_context, type("OfflineClient", (), {"network_requests": 0})()
    inventories, tokens, block = context.inventories, context.tokens, context.block
    source, destination = tokens[preview_source.address], tokens[preview_destination.address]
    request = TradeRequest(source, destination, parsed_amount, allow_psm_dai_refund)
    states, unavailable, adapters = context.states, list(context.unavailable), context.adapter_families
    sources = []
    for inventory in inventories:
        family = inventory["family"]
        sources.append({"family": family, "status": inventory["status"],
                        "selected": family in selected_families,
                        "discovered_pools": len(inventory.get("pools", [])),
                        "usable_pools": sum(state.record.family == family for state in states)})
        if family not in adapters:
            unavailable.append({"family": family, "status": inventory["status"],
                                "reason": "historical offers unavailable" if inventory["status"] == "unavailable"
                                else "adapter not implemented",
                                "unresolved": inventory.get("unresolved", []),
                                "aliases": [{"pool_id": row["pool_id"],
                                             "target": row["config"].get("aliases"),
                                             "capacity_ids": row["config"].get("capacity_ids", [])}
                                            for row in inventory.get("pools", [])
                                            if row.get("config", {}).get("kind") == "alias"]})
        elif inventory.get("unresolved"):
            unavailable.append({"family": family, "status": "unresolved",
                                "reason": "additional source coverage unresolved",
                                "unresolved": inventory["unresolved"]})

    plans = solver.solve(request, states)
    evaluator = Evaluator()
    evaluated = [evaluator.evaluate(plan, states) for plan in plans]
    feasible = [item for item in evaluated if item.feasible]
    direct = [item for item in feasible if len(item.steps) == 1]

    def single_path(item):
        token, quantity = source.address, request.amount_in
        for row in item.steps:
            if row.step.token_in != token or row.step.amount_in != quantity:
                return False
            token, quantity = row.step.token_out, row.amount_out
        return token == destination.address

    single = [item for item in feasible if single_path(item)]
    reference = []
    for state in states:
        if (state.record.pool == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
                and {source.address, destination.address} == {t.address for t in state.tokens()}):
            reference.append(evaluator.evaluate(
                Plan(request, (Step(state.record.pool_id, source.address, destination.address,
                                    request.amount_in),), "saved-reference",
                     {"kind": "reference", "allocation": [{"path": [state.record.pool_id],
                                                            "amount_in": request.amount_in}]}),
                states))

    def best(items):
        if not items:
            return None
        result = max(items, key=lambda item: item.amount_out)
        return {"amount_out": result.amount_out, "amount_in_spent": result.amount_in_spent,
                "residual_in": result.residual_in, "feasible": result.feasible,
                "solver": result.plan.solver, "search_info": result.plan.search_info,
                "steps": [{"pool_id": row.step.pool_id, "token_in": row.step.token_in,
                           "token_out": row.step.token_out, "amount_in": row.step.amount_in,
                           "amount_out": row.amount_out} for row in result.steps],
                "gas_estimate": result.gas_estimate,
                "terminal_refund": dict(result.terminal_refund)}

    report = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
              "timestamp": block.timestamp,
              "request": {"token_in": source.address, "symbol_in": source.symbol,
                          "decimals_in": source.decimals, "token_out": destination.address,
                          "symbol_out": destination.symbol, "decimals_out": destination.decimals,
                          "amount_in": request.amount_in,
                          "allow_psm_dai_refund": request.allow_psm_dai_refund},
              "single_pool_baseline": best(direct), "best_single_path": best(single),
              "saved_reference_pool": best([item for item in reference if item.feasible]),
              "best_split": best(feasible), "sources": sources, "unsupported": unavailable,
              "requested_solver": solver_name,
              "selected_families": sorted(selected_families), "offline": offline,
              "unsupported_candidates": getattr(solver, "unsupported", []),
              "solver_diagnostics": getattr(solver, "last_diagnostics", {}),
              "model_exclusions": getattr(solver, "exclusions", []),
              "search_limits": ({"max_hops": 2, "max_paths": 2, "grid_parts": grid_parts,
                                 "intermediates": list(solver.intermediates)}
                                if solver_name == "baseline" else
                                {"max_steps": max_steps, "beam_width": beam_width,
                                 "max_expansions": max_expansions, "grid_parts": grid_parts,
                                 "baseline_incumbent": True}),
              "network_requests": client.network_requests,
              "snapshot": f"data/snapshots/{block.chain}/{block.hash}",
              "limitations": ["Output is before gas; adapter gas values are model estimates.",
                              "Best evaluated candidate within reported budgets; no global optimality claim.",
                              "Numerical dual values are model estimates, not certified bounds.",
                              "Historical quote composition; no atomic execution or transfer validation."]}
    if report_annotations:
        report.update(report_annotations)
    encoded = json.dumps(report, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded)
    print(encoded, end="")
    return 0 if feasible else 1


def header_command(number: int) -> int:
    client = RpcClient()
    block = client.get_block(number)
    print(
        json.dumps(
            {
                "chain": block.chain,
                "number": block.number,
                "hash": block.hash,
                "timestamp": block.timestamp,
                "network_requests": client.network_requests,
            },
            indent=2,
        )
    )
    return 0


def pins_check_command() -> int:
    if not rpc_available():
        raise RpcError("RPC_MAINNET endpoint is not reachable")
    client = RpcClient()
    multicall = Multicall3(client)
    rows = json.loads(EVIDENCE.read_text())["rows"]
    output: list[tuple[int, str, str, str]] = []
    failed = False
    for row in rows:
        block = client.get_block(int(row["block"]))
        header_ok = block.hash == row["block_hash"].lower()
        calls = [call for call in row["calls"] if call["request"]["method"] == "eth_call"]
        specs = tuple(
            CallSpec(call["request"]["params"][0]["to"], call["request"]["params"][0]["data"])
            for call in calls
        )
        expected = tuple(call["response"]["result"].lower() for call in calls)
        batched = multicall.call(specs, block)
        multicall_ok = tuple(result.raw for result in batched) == expected
        direct = tuple(client.eth_call(spec.to, spec.data, block) for spec in specs)
        direct_ok = tuple(result.raw for result in direct) == expected
        failed |= not (header_ok and multicall_ok and direct_ok)
        output.append(
            (
                block.number,
                "ok" if header_ok else "FAIL",
                "ok" if multicall_ok else "FAIL",
                "ok" if direct_ok else "FAIL",
            )
        )

    print(f"{'block':>10}  {'header':>6}  {'multicall':>9}  {'direct':>6}")
    for block_number, header, multicall_status, direct in output:
        print(f"{block_number:>10}  {header:>6}  {multicall_status:>9}  {direct:>6}")
    print(f"network requests: {client.network_requests}")
    return 1 if failed else 0


def snapshot_build_command(number: int, specs_file: Path) -> int:
    raw = json.loads(specs_file.read_text())
    rows = raw["calls"] if isinstance(raw, dict) else raw
    specs = [CallSpec(row["to"], row["data"], row.get("tag", "")) for row in rows]
    client = RpcClient()
    block = client.get_block(number)
    snapshot = SnapshotStore().build(block, specs, client)
    print(
        json.dumps(
            {
                "chain": block.chain,
                "block": block.number,
                "block_hash": block.hash,
                "calls": len(snapshot.calls),
                "network_requests": client.network_requests,
            },
            indent=2,
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="swaparch")
    subcommands = command.add_subparsers(dest="command", required=True)
    header = subcommands.add_parser("header")
    header.add_argument("number", type=int)
    subcommands.add_parser("pins-check")
    snapshot = subcommands.add_parser("snapshot-build")
    snapshot.add_argument("--block", type=int, required=True)
    snapshot.add_argument("--specs", type=Path, required=True)
    quote = subcommands.add_parser("quote")
    quote.add_argument("--block", type=int, required=True)
    quote.add_argument("--in", dest="token_in", required=True)
    quote.add_argument("--out", dest="token_out", required=True)
    quote.add_argument("--amount", required=True, help="input token units, as an exact decimal")
    quote.add_argument("--grid-parts", type=int, default=10)
    quote.add_argument("--solver", choices=("baseline", "search", "dual"), default="baseline")
    quote.add_argument("--max-steps", type=int, default=8)
    quote.add_argument("--beam-width", type=int, default=128)
    quote.add_argument("--max-expansions", type=int, default=5000)
    quote.add_argument("--sources", help="comma-separated source families; default: all")
    quote.add_argument("--offline", action="store_true", help="fail on cache misses; never use network")
    quote.add_argument("--output", type=Path, help="also save the JSON report")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "header":
            return header_command(args.number)
        if args.command == "pins-check":
            return pins_check_command()
        if args.command == "quote":
            return quote_command(args.block, args.token_in, args.token_out, args.amount,
                                 args.grid_parts, args.output, solver_name=args.solver,
                                 max_steps=args.max_steps, beam_width=args.beam_width,
                                 max_expansions=args.max_expansions,
                                 families=tuple(args.sources.split(',')) if args.sources is not None else None,
                                 offline=args.offline)
        return snapshot_build_command(args.block, args.specs)
    except (RpcError, OSError, ValueError, KeyError) as exc:
        print(f"swaparch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
