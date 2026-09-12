"""Offline solver comparison for an already-cached, hash-pinned snapshot.

Example:
  UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark \
    python scripts/benchmark_solvers.py --block-hash 0xf2c9645... --amount 1000000000000000000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

from swaparch.core.types import TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.snapshot.store import SnapshotStore
from swaparch.solver.baseline import BaselineSolver
from swaparch.solver.dual import DualSolver
from swaparch.solver.search import GeneralSearchSolver
from swaparch.universe import (
    DISCOVERY_ROOT,
    implemented_adapters,
    load_states,
    pool_record_from_json,
    pools_live_at,
)

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _best(solver, request, states):
    started = perf_counter()
    plans = solver.solve(request, states)
    runtime = perf_counter() - started
    evaluations = [Evaluator().evaluate(plan, states) for plan in plans]
    feasible = [item for item in evaluations if item.feasible]
    winner = max(feasible, key=lambda item: item.amount_out, default=None)
    return {
        "runtime_seconds": runtime,
        "candidates": len(plans),
        "feasible_candidates": len(feasible),
        "winner": None if winner is None else {
            "amount_out": winner.amount_out,
            "amount_in_spent": winner.amount_in_spent,
            "residual_in": winner.residual_in,
            "gas_estimate": winner.gas_estimate,
            "net_amount_out": None,
            "gas_cost_output_units": None,
            "steps": [
                {"pool_id": row.step.pool_id, "amount_in": row.step.amount_in,
                 "amount_out": row.amount_out, "token_in": row.step.token_in,
                 "token_out": row.step.token_out}
                for row in winner.steps
            ],
            "search_info": winner.plan.search_info,
        },
        "recovery_errors": list(getattr(solver, "terminal_refusals", ())),
        "search_diagnostics": getattr(solver, "last_diagnostics", None),
    }


def _exclusion_summary(rows, sample_size: int = 32):
    return {"count": len(rows), "sample": rows[:sample_size], "omitted": max(0, len(rows) - sample_size)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare offline routing candidates on a cached snapshot")
    parser.add_argument("--block-hash", required=True)
    parser.add_argument("--amount", type=int, required=True, help="WETH smallest units")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--beam-width", type=int, default=128)
    parser.add_argument("--max-expansions", type=int, default=20_000)
    parser.add_argument("--grid-parts", type=int, default=32)
    parser.add_argument("--dual-samples", type=int, default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.amount <= 0:
        parser.error("--amount must be positive")

    snapshot = SnapshotStore().load(1, args.block_hash)
    inventories = [json.loads(path.read_text()) for path in sorted((DISCOVERY_ROOT / "1").glob("*.json"))]
    adapters = implemented_adapters()
    records = [
        pool_record_from_json(row)
        for inventory in inventories
        for row in inventory.get("pools", [])
    ]
    tokens = {token.address: token for record in records for token in record.tokens}
    request = TradeRequest(tokens[WETH], tokens[USDC], args.amount)
    states, failed = load_states(adapters, pools_live_at(records, snapshot.block.number), snapshot)
    budgets = {"max_steps": args.max_steps, "beam_width": args.beam_width,
               "max_expansions": args.max_expansions, "grid_parts": args.grid_parts}
    baseline = BaselineSolver(grid_parts=args.grid_parts)
    search = GeneralSearchSolver(**budgets)
    dual = DualSolver(**budgets, dual_samples=args.dual_samples)
    results = {
        "code_identity": {
            "python": sys.version,
            "files_sha256": {
                name: _sha256(Path(__file__).resolve().parents[1] / path)
                for name, path in {
                    "dual": "src/swaparch/solver/dual.py",
                    "v3_continuous": "src/swaparch/solver/v3_continuous.py",
                    "search": "src/swaparch/solver/search.py",
                    "baseline": "src/swaparch/solver/baseline.py",
                    "evaluator": "src/swaparch/evaluator/evaluate.py",
                    "lock": "uv.lock",
                }.items()
            },
        },
        "snapshot": {"chain": snapshot.block.chain, "number": snapshot.block.number,
                     "hash": snapshot.block.hash, "timestamp": snapshot.block.timestamp},
        "request": {"token_in": WETH, "token_out": USDC, "amount_in": args.amount,
                    "decimals_in": request.token_in.decimals, "decimals_out": request.token_out.decimals,
                    "scale_in": 10 ** request.token_in.decimals, "scale_out": 10 ** request.token_out.decimals},
        "budgets": budgets,
        "dual_samples": args.dual_samples,
        "admitted_state_universe": {
            "state_pool_ids": [state.record.pool_id for state in states],
            "load_failures": [{"pool": rec.pool_id, "reason": reason} for rec, reason in failed],
            "inventory": [{"family": item["family"], "status": item["status"],
                           "pools": len(item.get("pools", []))} for item in inventories],
        },
        "baseline": _best(baseline, request, states),
        "stateful_search": _best(search, request, states),
        "numerical_dual": _best(dual, request, states),
        "exclusions": {"baseline": _exclusion_summary(baseline.unsupported),
                       "search": _exclusion_summary(search.unsupported),
                       "dual_model": _exclusion_summary(dual.exclusions)},
        "quality": "fresh integer evaluator output; no global-optimality or dual-bound claim",
        "limitations": [
            "No RPC is performed; this is only the cached snapshot's admitted universe.",
            "The dual result is a numerical model estimate, not an upper bound or containment proof.",
            "Gas estimates are reported separately; unknown gas is not converted to net output.",
            "A recovered candidate still needs a fresh exact evaluator result; unfunded cycles are never seeded.",
        ],
    }
    encoded = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
