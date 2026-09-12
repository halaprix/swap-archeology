"""Offline evaluator-memo experiment for the stopped historical sweep.

This wraps the existing full-report performance harness.  It is deliberately
not production routing code: it measures whether re-evaluating an identical
step sequence against the same prepared state universe is material work.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any

from swaparch.adapters.uniswap_v2 import UniV2State
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import Unsupported
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver
from swaparch.solver.search import GeneralSearchSolver, _Node

_PERF_SPEC = importlib.util.spec_from_file_location(
    "eval_quote_performance", Path(__file__).with_name("eval_quote_performance.py")
)
assert _PERF_SPEC and _PERF_SPEC.loader
_PERF = importlib.util.module_from_spec(_PERF_SPEC)
_PERF_SPEC.loader.exec_module(_PERF)

CACHE_SIZE = 16_384
VARIANTS = (
    "combined",
    "combined_evaluator_memo",
    "combined_baseline_prefix",
    "combined_indexed_split",
    "combined_search_residual_skip",
    "combined_search_static_metadata",
    "combined_prefix_search_residual_skip",
    "combined_indexed_prefix_search_residual_skip",
    "combined_indexed_prefix_search_residual_skip_search_static_metadata",
)


class EvaluationMemo:
    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.values: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self.hits = self.misses = 0

    def get(self, key: tuple[Any, ...]) -> Any | None:
        value = self.values.get(key)
        if value is None:
            self.misses += 1
            return None
        self.values.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        self.values[key] = value
        self.values.move_to_end(key)
        if len(self.values) > self.maxsize:
            self.values.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self.values), "maxsize": self.maxsize}


class OrderedPrefixMemo:
    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.values: dict[tuple[Any, ...], tuple[Any, tuple[tuple[str, Any], ...], tuple[Any, ...] | None]] = {}
        self.hits = self.misses = 0

    def get(self, key: tuple[Any, ...]) -> tuple[Any, tuple[tuple[str, Any], ...], tuple[Any, ...] | None] | None:
        value = self.values.get(key)
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: tuple[Any, ...], value: tuple[Any, tuple[tuple[str, Any], ...], tuple[Any, ...] | None]) -> None:
        if len(self.values) < self.maxsize:
            self.values[key] = value

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self.values), "maxsize": self.maxsize}


@contextmanager
def evaluator_plan_memo(initial_states: tuple[Any, ...], maxsize: int = CACHE_SIZE) -> Iterator[EvaluationMemo]:
    """Memoize exact evaluator inputs inside one prepared quote only.

    ``Evaluation`` includes its input plan, so a hit replaces that field instead
    of returning metadata attached to the plan first seen by the cache.
    """
    universe = tuple(id(state) for state in initial_states)
    memo = EvaluationMemo(maxsize)
    original = Evaluator.evaluate

    def evaluate(self, plan, states):
        if not isinstance(states, tuple) or tuple(id(state) for state in states) != universe:
            return original(self, plan, states)
        # Evaluator reads request and steps, never solver/search_info.
        key = (universe, plan.request, plan.steps)
        cached = memo.get(key)
        if cached is not None:
            return replace(cached, plan=plan)
        result = original(self, plan, states)
        memo.put(key, result)
        return result

    try:
        Evaluator.evaluate = evaluate
        yield memo
    finally:
        Evaluator.evaluate = original


@contextmanager
def baseline_ordered_prefix_memo(initial_states: tuple[Any, ...], maxsize: int = CACHE_SIZE) -> Iterator[OrderedPrefixMemo]:
    """Cache the first overlapping baseline route from the original state map.

    The second route still runs against the copied updated states.  Cached
    failures replay the baseline's duplicate unsupported diagnostic exactly.
    """
    universe = {state.record.pool_id: id(state) for state in initial_states}
    memo = OrderedPrefixMemo(maxsize)
    original = BaselineSolver._ordered_output
    original_best_split = BaselineSolver._best_split
    allowed_maps: list[dict[str, Any]] = []

    def execute_route(solver, route, allocation, current):
        amount = allocation
        updates = {}
        for pool_id, token_in, token_out in route.hops:
            state = current.get(pool_id)
            if state is None:
                return None, (), (pool_id, amount, "unknown pool")
            try:
                amount, next_state = state.swap(token_in, token_out, amount)
            except Unsupported as exc:
                return None, (), (pool_id, amount, str(exc))
            if type(amount) is not int or amount < 0:
                return None, (), None
            current[pool_id] = next_state
            updates[pool_id] = next_state
        return amount, tuple(updates.items()), None

    def ordered_output(solver, routes, allocations, states):
        if not allowed_maps or states is not allowed_maps[-1]:
            return original(solver, routes, allocations, states)
        first, second = routes
        first_amount, second_amount = allocations
        key = (first, first_amount)
        cached = memo.get(key)
        if cached is None:
            current = dict(states)
            cached = execute_route(solver, first, first_amount, current)
            memo.put(key, cached)
        first_out, updates, failure = cached
        if failure is not None:
            pool_id, amount, reason = failure
            solver._record_unsupported(first, pool_id, amount, reason)
            return None
        if first_out is None:
            return None
        current = dict(states)
        current.update(updates)
        second_out, _, failure = execute_route(solver, second, second_amount, current)
        if failure is not None:
            pool_id, amount, reason = failure
            solver._record_unsupported(second, pool_id, amount, reason)
            return None
        return None if second_out is None else first_out + second_out

    def best_split(solver, routes, request, states, state_by_pool):
        if (len(state_by_pool) != len(universe)
                or any(universe.get(pool_id) != id(state) for pool_id, state in state_by_pool.items())):
            return original_best_split(solver, routes, request, states, state_by_pool)
        allowed_maps.append(state_by_pool)
        try:
            return original_best_split(solver, routes, request, states, state_by_pool)
        finally:
            allowed_maps.pop()

    try:
        BaselineSolver._ordered_output = ordered_output
        BaselineSolver._best_split = best_split
        yield memo
    finally:
        BaselineSolver._ordered_output = original
        BaselineSolver._best_split = original_best_split


@contextmanager
def search_nonterminal_residual_skip() -> Iterator[None]:
    """Avoid proving a nonterminal child infeasible when input remains.

    Evaluator always rejects such a plan with ``input token remains after
    execution``.  Final-depth children still run it, preserving terminal
    refusal diagnostics exactly.
    """
    original = GeneralSearchSolver.solve

    def solve(self, request, states):
        BaselineSolver._validate_request(request)
        state_list = tuple(states)
        self.unsupported = []
        self.truncated = False
        self.beam_pruned = False
        self.terminal_refusals = []
        self._token_decimals = {
            token.address: token.decimals for state in state_list for token in state.tokens()
        }
        self._token_decimals[request.token_in.address] = request.token_in.decimals
        self._token_decimals[request.token_out.address] = request.token_out.decimals
        self._request_input = request.token_in.address
        self._output_values = self._backward_values(state_list, request.token_out.address)
        initial = _Node(((request.token_in.address, request.amount_in),), state_list, ())
        frontier = [initial]
        completions = [plan for plan in self.incumbents if plan.request == request]
        expansions = 0
        seen = set()

        for depth in range(self.max_steps):
            next_frontier = []
            for node in frontier:
                for child in self._children(node, request):
                    if expansions >= self.max_expansions:
                        self.truncated = True
                        break
                    expansions += 1
                    if depth + 1 == self.max_steps or child.held(request.token_in.address) == 0:
                        plan = self._plan(request, child, depth + 1, expansions)
                        evaluated = self._evaluator.evaluate(plan, state_list)
                        if evaluated.feasible:
                            completions.append(plan)
                        elif depth + 1 == self.max_steps:
                            self._record_terminal_refusal(evaluated.reasons)
                    key = self._key(child)
                    if key is None or key not in seen:
                        if key is not None:
                            seen.add(key)
                        next_frontier.append(child)
                if self.truncated:
                    break
            if self.truncated or not next_frontier:
                break
            frontier = next_frontier if self.exhaustive else self._trim(next_frontier, request.token_out.address)

        if self.include_baseline:
            completions.extend(BaselineSolver(grid_parts=self.grid_parts).solve(request, state_list))
        ranked = self._rank_unique(completions, state_list, request, expansions)
        self.last_diagnostics = {
            "expansions": expansions,
            "truncated": self.truncated,
            "beam_pruned": self.beam_pruned,
            "feasible_candidates": len(ranked),
            "dedupe": "v2_v3_only; generic PoolState nodes are not deduplicated",
            "terminal_refusals": list(self.terminal_refusals),
            "ranking": "backward local output-value heuristic; not a bound",
        }
        return ranked

    try:
        GeneralSearchSolver.solve = solve
        yield
    finally:
        GeneralSearchSolver.solve = original


@contextmanager
def baseline_indexed_split_quotes() -> Iterator[None]:
    """Avoid route-object hashing for repeated independent baseline quotes."""
    original = BaselineSolver._best_split

    def best_split(solver, routes, request, states, state_by_pool):
        best = None
        route_quotes = [{} for _ in routes]
        capacities = [solver._capacity_owners(route, state_by_pool) for route in routes]
        pool_sets = [set(route.pools) for route in routes]
        amount_firsts = sorted({
            amount
            for part in range(1, solver.grid_parts)
            for amount in (
                request.amount_in * part // solver.grid_parts,
                request.amount_in - request.amount_in * part // solver.grid_parts,
            )
            if 0 < amount < request.amount_in
        })

        def relation(first_index, second_index):
            if pool_sets[first_index] & pool_sets[second_index]:
                return "overlap"
            first_owners, second_owners = capacities[first_index], capacities[second_index]
            if first_owners is None or second_owners is None:
                return "capacity-conflict"
            if any(
                first_owners.get(capacity_id) != owner
                for capacity_id, owner in second_owners.items()
                if capacity_id in first_owners
            ):
                return "capacity-conflict"
            return "independent"

        pairs = [(first, second, relation(first, second)) for first, second in combinations(range(len(routes)), 2)]
        reported_conflicts = set()

        def route_output(index, amount):
            cache = route_quotes[index]
            if amount not in cache:
                cache[amount] = solver._route_output(routes[index], amount, state_by_pool, {})
            return cache[amount]

        def candidate(first_index, second_index, first_amount, second_amount, amount_out):
            nonlocal best
            if amount_out is None or (best is not None and amount_out <= best[0]):
                return
            maybe = solver._candidate(
                "split",
                (routes[first_index], routes[second_index]),
                (first_amount, second_amount),
                request,
                states,
                state_by_pool,
            )
            if maybe and (best is None or maybe[0] > best[0]):
                best = maybe

        for amount_first in amount_firsts:
            for first, second, split_relation in pairs:
                if split_relation == "capacity-conflict":
                    if (first, second) not in reported_conflicts:
                        solver._record_failure((routes[first], routes[second]), (request.amount_in,), ("shared capacity not modelled",))
                        reported_conflicts.add((first, second))
                    continue
                complement = request.amount_in - amount_first
                if split_relation == "overlap":
                    candidate(first, second, amount_first, complement, solver._ordered_output(
                        (routes[first], routes[second]), (amount_first, complement), state_by_pool
                    ))
                    candidate(second, first, complement, amount_first, solver._ordered_output(
                        (routes[second], routes[first]), (complement, amount_first), state_by_pool
                    ))
                    continue
                first_out, second_out = route_output(first, amount_first), route_output(second, complement)
                amount_out = None if first_out is None or second_out is None else first_out + second_out
                candidate(first, second, amount_first, complement, amount_out)
                candidate(second, first, complement, amount_first, amount_out)
        return best

    try:
        BaselineSolver._best_split = best_split
        yield
    finally:
        BaselineSolver._best_split = original


@contextmanager
def search_static_metadata() -> Iterator[None]:
    """Cache pure action grids and known-unavailable V2/V3 dedupe checks."""
    original = GeneralSearchSolver.solve

    def solve(self, request, states):
        state_list = tuple(states)
        original_amounts = self._amounts
        original_key = self._key
        amounts = {}

        def cached_amounts(state, token_in, token_out, held, request):
            key = (id(state), token_in, token_out, held, request.allow_psm_dai_refund)
            if key not in amounts:
                amounts[key] = original_amounts(state, token_in, token_out, held, request)
            return amounts[key]

        self._amounts = cached_amounts
        if not all(isinstance(state, (UniV2State, UniV3State)) for state in state_list):
            # The actual pinned adapters retain their concrete immutable state
            # type after swap, so this universe can never enter _key's V2/V3 path.
            self._key = lambda node: None
        try:
            return original(self, request, state_list)
        finally:
            self._amounts = original_amounts
            self._key = original_key

    try:
        GeneralSearchSolver.solve = solve
        yield
    finally:
        GeneralSearchSolver.solve = original


def execute_variant(name: str, context: Any, annotation: dict[str, Any], case: tuple[str, str, str]) -> dict[str, Any]:
    if name == "combined":
        return _PERF.execute_variant("combined", context, annotation, case)
    if name not in VARIANTS:
        raise ValueError(f"unknown variant {name}")
    with ExitStack() as stack:
        if name == "combined_evaluator_memo":
            evaluator_memo = stack.enter_context(evaluator_plan_memo(context.states))
        else:
            evaluator_memo = None
        if "indexed_" in name:
            stack.enter_context(baseline_indexed_split_quotes())
        if "baseline_prefix" in name or "prefix_" in name:
            prefix_memo = stack.enter_context(baseline_ordered_prefix_memo(context.states))
        else:
            prefix_memo = None
        if "search_residual_skip" in name:
            stack.enter_context(search_nonterminal_residual_skip())
        if "search_static_metadata" in name:
            stack.enter_context(search_static_metadata())
        result = _PERF.execute_variant("combined", context, annotation, case)
    if evaluator_memo is not None:
        result["cache"]["evaluator_plan_memo"] = evaluator_memo.stats()
    if prefix_memo is not None:
        result["cache"]["baseline_ordered_prefix_memo"] = prefix_memo.stats()
    result["variant"] = name
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Full-report evaluator-memo experiment")
    parser.add_argument("--block", type=int, default=25760917)
    parser.add_argument("--case", nargs=3, metavar=("TOKEN_IN", "TOKEN_OUT", "AMOUNT"), default=("WETH", "USDC", "100"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.block <= 0 or args.repeats <= 0:
        parser.error("--block and --repeats must be positive")

    _PERF.cli._inventory_inputs()
    context, annotation, client = _PERF.prepared_collection_context(args.block)
    assert client.network_requests == 0
    case = tuple(args.case)
    reference: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        order = VARIANTS[repeat % len(VARIANTS):] + VARIANTS[:repeat % len(VARIANTS)]
        for variant in order:
            result = execute_variant(variant, context, annotation, case)
            report = result.pop("report")
            if reference is None:
                reference = report
            else:
                assert report == reference, f"full report parity failed for {variant}"
            result["repeat"] = repeat
            runs.append(result)
            print(f"completed repeat={repeat + 1}/{args.repeats} variant={variant}", file=sys.stderr, flush=True)
    medians = {
        variant: {
            "wall_seconds": median(row["wall_seconds"] for row in runs if row["variant"] == variant),
            "cpu_seconds": median(row["cpu_seconds"] for row in runs if row["variant"] == variant),
        }
        for variant in VARIANTS
    }
    reference_wall = medians["combined"]["wall_seconds"]
    result = {
        "offline": True,
        "network_requests": 0,
        "settings": {"block": args.block, "case": dict(zip(("token_in", "token_out", "amount"), case)), "repeats": args.repeats, "cache_size": CACHE_SIZE},
        "parity": "all full CLI JSON reports, including diagnostics and unsupported lists, matched combined",
        "runs": runs,
        "median_work": {name: {**values, "speedup_vs_combined": reference_wall / values["wall_seconds"]} for name, values in medians.items()},
    }
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
