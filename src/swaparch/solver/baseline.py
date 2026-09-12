"""Finite, stateful baseline route search.

This is a candidate generator for the phase-2 comparison, not a global router:
it considers at most two paths, each with at most two hops, and split sizes on a
finite integer grid.  The evaluator remains the source of truth for feasibility
and final output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from swaparch.core.protocols import PoolState, Unsupported
from swaparch.core.types import Address, Plan, Step, Token, TradeRequest, norm_address
from swaparch.evaluator.evaluate import Evaluator


@dataclass(frozen=True)
class _Route:
    hops: tuple[tuple[str, Address, Address], ...]

    @property
    def pools(self) -> list[str]:
        return [pool_id for pool_id, _, _ in self.hops]


class BaselineSolver:
    """Enumerate direct, one-intermediate, and two-route grid candidates."""

    name = "baseline"

    def __init__(
        self,
        intermediates: Iterable[Token | Address] | None = None,
        grid_parts: int = 10,
    ) -> None:
        if type(grid_parts) is not int or grid_parts < 1:
            raise ValueError("grid_parts must be a positive integer")
        self._infer_intermediates = intermediates is None
        values = () if intermediates is None else intermediates
        self.intermediates = tuple(
            value.address if isinstance(value, Token) else norm_address(value) for value in values
        )
        self.grid_parts = grid_parts
        self.unsupported: list[dict[str, Any]] = []
        self._evaluator = Evaluator()

    def solve(self, request: TradeRequest, states: Iterable[PoolState]) -> list[Plan]:
        self._validate_request(request)
        self.unsupported = []
        state_list = tuple(states)
        if self._infer_intermediates:
            self.intermediates = tuple(sorted({token.address for state in state_list
                                                for token in state.tokens()}))
        state_by_pool = {state.record.pool_id: state for state in state_list}

        direct_routes = self._routes_between(
            request.token_in.address, request.token_out.address, state_list
        )
        path_routes = [
            _Route(first.hops + second.hops)
            for intermediate in self.intermediates
            if intermediate not in {request.token_in.address, request.token_out.address}
            for first in self._routes_between(request.token_in.address, intermediate, state_list)
            for second in self._routes_between(intermediate, request.token_out.address, state_list)
        ]

        best_direct = self._best_unsplit("direct", direct_routes, request, state_list, state_by_pool)
        best_path = self._best_unsplit("path", path_routes, request, state_list, state_by_pool)
        best_split = self._best_split(
            direct_routes + path_routes, request, state_list, state_by_pool
        )
        best_unsplit = max(
            (candidate for candidate in (best_direct, best_path) if candidate),
            default=None,
            key=lambda candidate: candidate[0],
        )
        if best_unsplit and (best_split is None or best_split[0] < best_unsplit[0]):
            best_split = (best_unsplit[0], self._split_fallback(best_unsplit[1]))
        winners = [candidate for candidate in (best_direct, best_path, best_split) if candidate]
        return [plan for _, plan in sorted(winners, key=lambda candidate: candidate[0], reverse=True)]

    @staticmethod
    def _validate_request(request: TradeRequest) -> None:
        if type(request.amount_in) is not int:
            raise ValueError("request input must be an integer")
        if request.amount_in <= 0:
            raise ValueError("request requires positive input")
        if request.token_in.chain != request.token_out.chain:
            raise ValueError("request tokens must be on one chain")
        if request.token_in.address == request.token_out.address:
            raise ValueError("request tokens must be distinct")

    @staticmethod
    def _routes_between(
        token_in: Address, token_out: Address, states: Iterable[PoolState]
    ) -> list[_Route]:
        return [
            _Route(((state.record.pool_id, token_in, token_out),))
            for state in states
            if {token_in, token_out}.issubset({token.address for token in state.tokens()})
        ]

    def _best_unsplit(
        self,
        kind: str,
        routes: Iterable[_Route],
        request: TradeRequest,
        states: tuple[PoolState, ...],
        state_by_pool: dict[str, PoolState],
    ) -> tuple[int, Plan] | None:
        candidates = [
            candidate
            for route in routes
            if (candidate := self._candidate(kind, (route,), (request.amount_in,), request, states, state_by_pool))
            is not None
        ]
        return max(candidates, default=None, key=lambda candidate: candidate[0])

    def _best_split(
        self,
        routes: list[_Route],
        request: TradeRequest,
        states: tuple[PoolState, ...],
        state_by_pool: dict[str, PoolState],
    ) -> tuple[int, Plan] | None:
        best: tuple[int, Plan] | None = None
        route_quotes: dict[tuple[_Route, int], int | None] = {}
        route_capacities = {
            route: self._capacity_owners(route, state_by_pool)
            for route in routes
        }
        amount_firsts = sorted(
            {
                amount
                for part in range(1, self.grid_parts)
                for amount in (
                    request.amount_in * part // self.grid_parts,
                    request.amount_in - request.amount_in * part // self.grid_parts,
                )
                if 0 < amount < request.amount_in
            }
        )
        pairs = [
            (first, second, self._split_relation(first, second, route_capacities))
            for first, second in combinations(routes, 2)
        ]
        reported_conflicts: set[tuple[_Route, _Route]] = set()
        for amount_first in amount_firsts:
            for first, second, relation in pairs:
                if relation == "capacity-conflict":
                    # This is the evaluator's static rejection.  Recording it once makes the
                    # excluded pair visible without compiling every allocation/order of it.
                    if (first, second) not in reported_conflicts:
                        self._record_failure((first, second), (request.amount_in,),
                                             ("shared capacity not modelled",))
                        reported_conflicts.add((first, second))
                    continue
                for ordered_routes, allocations in (
                    ((first, second), (amount_first, request.amount_in - amount_first)),
                    ((second, first), (request.amount_in - amount_first, amount_first)),
                ):
                    if relation == "overlap":
                        # A shared pool must advance in plan order, so it cannot use the
                        # independent route cache.  Its exact sequential output is still
                        # enough to avoid materializing losing plans.
                        amount_out = self._ordered_output(ordered_routes, allocations, state_by_pool)
                    else:
                        amount_out = self._split_output(
                            ordered_routes, allocations, state_by_pool, route_quotes
                        )
                    # These are exact immutable-state simulations.  Compile and evaluate
                    # only a possible winner, retaining the evaluator as final authority.
                    candidate = (
                        self._candidate(
                            "split", ordered_routes, allocations, request, states, state_by_pool
                        )
                        if amount_out is not None and (best is None or amount_out > best[0])
                        else None
                    )
                    if candidate and (best is None or candidate[0] > best[0]):
                        best = candidate
        return best

    @staticmethod
    def _capacity_owners(
        route: _Route, states: dict[str, PoolState]
    ) -> dict[str, str] | None:
        """Return capacity-id owners, or None when the route is statically invalid."""
        owners: dict[str, str] = {}
        for pool_id in dict.fromkeys(route.pools):
            state = states.get(pool_id)
            if state is None:
                return None
            for capacity_id in state.capacity_ids():
                owner = owners.setdefault(capacity_id, pool_id)
                if owner != pool_id:
                    return None
        return owners

    @staticmethod
    def _split_relation(
        first: _Route,
        second: _Route,
        capacities: dict[_Route, dict[str, str] | None],
    ) -> str:
        """Classify whether independent exact route quotes can be safely added."""
        if set(first.pools) & set(second.pools):
            return "overlap"
        first_owners, second_owners = capacities[first], capacities[second]
        if first_owners is None or second_owners is None:
            return "capacity-conflict"
        if any(first_owners.get(capacity_id) != owner
               for capacity_id, owner in second_owners.items() if capacity_id in first_owners):
            return "capacity-conflict"
        return "independent"

    def _split_output(
        self,
        routes: tuple[_Route, _Route],
        allocations: tuple[int, int],
        states: dict[str, PoolState],
        cache: dict[tuple[_Route, int], int | None],
    ) -> int | None:
        outputs = [self._route_output(route, amount, states, cache)
                   for route, amount in zip(routes, allocations, strict=True)]
        return None if any(output is None for output in outputs) else sum(outputs)

    def _ordered_output(
        self,
        routes: tuple[_Route, _Route],
        allocations: tuple[int, int],
        states: dict[str, PoolState],
    ) -> int | None:
        """Quote two overlapping routes in their executed order without building steps."""
        current = dict(states)
        amount_out = 0
        for route, allocation in zip(routes, allocations, strict=True):
            amount = allocation
            for pool_id, token_in, token_out in route.hops:
                state = current.get(pool_id)
                if state is None:
                    self._record_unsupported(route, pool_id, amount, "unknown pool")
                    return None
                try:
                    amount, next_state = state.swap(token_in, token_out, amount)
                except Unsupported as exc:
                    self._record_unsupported(route, pool_id, amount, str(exc))
                    return None
                if type(amount) is not int or amount < 0:
                    return None
                current[pool_id] = next_state
            amount_out += amount
        return amount_out

    def _route_output(
        self,
        route: _Route,
        allocation: int,
        states: dict[str, PoolState],
        cache: dict[tuple[_Route, int], int | None],
    ) -> int | None:
        """Quote one ordered path exactly, including its own repeated-pool state."""
        key = (route, allocation)
        if key in cache:
            return cache[key]
        current = dict(states)
        amount = allocation
        for pool_id, token_in, token_out in route.hops:
            state = current.get(pool_id)
            if state is None:
                self._record_unsupported(route, pool_id, amount, "unknown pool")
                cache[key] = None
                return None
            try:
                amount, next_state = state.swap(token_in, token_out, amount)
            except Unsupported as exc:
                self._record_unsupported(route, pool_id, amount, str(exc))
                cache[key] = None
                return None
            # Let the evaluator preserve its validation behaviour for malformed adapter output.
            if type(amount) is not int or amount < 0:
                cache[key] = None
                return None
            current[pool_id] = next_state
        cache[key] = amount
        return amount

    def _candidate(
        self,
        kind: str,
        routes: tuple[_Route, ...],
        allocations: tuple[int, ...],
        request: TradeRequest,
        states: tuple[PoolState, ...],
        state_by_pool: dict[str, PoolState],
    ) -> tuple[int, Plan] | None:
        steps = self._compile(routes, allocations, state_by_pool)
        if steps is None:
            return None
        plan = Plan(
            request=request,
            steps=tuple(steps),
            solver=self.name,
            search_info={
                "kind": kind,
                "allocation": [
                    {"path": route.pools, "amount_in": amount}
                    for route, amount in zip(routes, allocations, strict=True)
                ],
                "path_count": len(routes),
                "search_limits": {"max_paths": 2, "max_hops": 2, "grid_parts": self.grid_parts},
            },
        )
        evaluation = self._evaluator.evaluate(plan, states)
        if not evaluation.feasible:
            self._record_failure(routes, allocations, evaluation.reasons)
            return None
        return evaluation.amount_out, plan

    @staticmethod
    def _split_fallback(plan: Plan) -> Plan:
        return Plan(
            request=plan.request,
            steps=plan.steps,
            solver=plan.solver,
            search_info={
                **plan.search_info,
                "kind": "split",
                "path_count": 1,
                "fallback_kind": plan.search_info["kind"],
            },
        )

    def _compile(
        self,
        routes: tuple[_Route, ...],
        allocations: tuple[int, ...],
        states: dict[str, PoolState],
    ) -> list[Step] | None:
        current = dict(states)
        steps: list[Step] = []
        for route, allocation in zip(routes, allocations, strict=True):
            amount = allocation
            for pool_id, token_in, token_out in route.hops:
                state = current.get(pool_id)
                if state is None:
                    self._record_unsupported(route, pool_id, amount, "unknown pool")
                    return None
                try:
                    amount_out, next_state = state.swap(token_in, token_out, amount)
                except Unsupported as exc:
                    self._record_unsupported(route, pool_id, amount, str(exc))
                    return None
                steps.append(Step(pool_id, token_in, token_out, amount))
                current[pool_id] = next_state
                amount = amount_out
        return steps

    def _record_failure(
        self, routes: tuple[_Route, ...], allocations: tuple[int, ...], reasons: tuple[str, ...]
    ) -> None:
        self.unsupported.append(
            {
                "path": [pool_id for route in routes for pool_id in route.pools],
                "pool": None,
                "input": sum(allocations),
                "reason": "; ".join(reasons),
            }
        )

    def _record_unsupported(
        self, route: _Route, pool_id: str, amount: int, reason: str
    ) -> None:
        self.unsupported.append(
            {"path": route.pools, "pool": pool_id, "input": amount, "reason": reason}
        )
