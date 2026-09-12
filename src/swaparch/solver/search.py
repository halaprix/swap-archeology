"""Bounded stateful candidate search.

This is deliberately a candidate generator.  It advances immutable pool states
and token balances together, so an action can use only funds produced by earlier
actions and repeated use of a pool sees its updated state.  The evaluator still
decides whether a returned plan is a full fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from swaparch.adapters.litepsm import LitePsmState
from swaparch.adapters.uniswap_v2 import UniV2State
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import PoolState, Unsupported
from swaparch.core.types import Address, Plan, Step, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver


@dataclass(frozen=True)
class _Node:
    balances: tuple[tuple[Address, int], ...]
    states: tuple[PoolState, ...]
    steps: tuple[Step, ...]

    def held(self, token: Address) -> int:
        return dict(self.balances).get(token, 0)


class GeneralSearchSolver:
    """Beam search over exact, positive integer actions.

    ``grid_parts`` only samples action sizes; it does not cap routes, hops, or
    participating pools.  ``exhaustive=True`` enumerates every positive amount
    up to the held balance (appropriate only for tiny synthetic fixtures).
    """

    name = "stateful-search"

    def __init__(
        self,
        *,
        max_steps: int = 8,
        beam_width: int = 128,
        max_expansions: int = 20_000,
        grid_parts: int = 32,
        exhaustive: bool = False,
        suggested_actions: Mapping[tuple[str, Address, Address], Iterable[int]] | None = None,
        incumbents: Iterable[Plan] = (),
        include_baseline: bool = True,
    ) -> None:
        for name, value, minimum in (
            ("max_steps", max_steps, 1),
            ("beam_width", beam_width, 1),
            ("max_expansions", max_expansions, 1),
            ("grid_parts", grid_parts, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        self.max_steps = max_steps
        self.beam_width = beam_width
        self.max_expansions = max_expansions
        self.grid_parts = grid_parts
        self.exhaustive = exhaustive
        self.suggested_actions = {
            key: tuple(value) for key, value in (suggested_actions or {}).items()
        }
        self.incumbents = tuple(incumbents)
        self.include_baseline = include_baseline
        self.unsupported: list[dict[str, Any]] = []
        self.truncated = False
        self.beam_pruned = False
        self.last_diagnostics: dict[str, Any] = {}
        self.terminal_refusals: list[str] = []
        self._token_decimals: dict[Address, int] = {}
        self._output_values: dict[Address, Fraction] = {}
        self._request_input: Address = ""
        self._evaluator = Evaluator()

    def solve(self, request: TradeRequest, states: Iterable[PoolState]) -> list[Plan]:
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
        seen: set[tuple[tuple[tuple[Address, int], ...], tuple[str, ...], int]] = set()

        for depth in range(self.max_steps):
            next_frontier: list[_Node] = []
            for node in frontier:
                for child in self._children(node, request):
                    if expansions >= self.max_expansions:
                        self.truncated = True
                        break
                    expansions += 1
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
            # The old finite baseline is an incumbent source only; it does not
            # constrain this search's topology.
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

    def _children(self, node: _Node, request: TradeRequest) -> Iterable[_Node]:
        balances = dict(node.balances)
        for state_index, state in enumerate(node.states):
            addresses = tuple(token.address for token in state.tokens())
            for token_in in addresses:
                held = balances.get(token_in, 0)
                if held <= 0:
                    continue
                for token_out in addresses:
                    if token_out == token_in:
                        continue
                    for amount in self._amounts(state, token_in, token_out, held, request):
                        try:
                            amount_out, updated = state.swap(token_in, token_out, amount)
                        except Unsupported as exc:
                            self._record_unsupported(state, token_in, token_out, amount, str(exc))
                            continue
                        if type(amount_out) is not int or amount_out <= 0:
                            continue
                        child_balances = dict(balances)
                        child_balances[token_in] -= amount
                        child_balances[token_out] = child_balances.get(token_out, 0) + amount_out
                        child_states = list(node.states)
                        child_states[state_index] = updated
                        yield _Node(
                            tuple(sorted((key, value) for key, value in child_balances.items() if value)),
                            tuple(child_states),
                            node.steps + (Step(state.record.pool_id, token_in, token_out, amount),),
                        )

    def _amounts(
        self, state: PoolState, token_in: Address, token_out: Address, held: int,
        request: TradeRequest,
    ) -> tuple[int, ...]:
        values = {held, 1}
        if self.exhaustive:
            values.update(range(1, held + 1))
        else:
            values.update(held * part // self.grid_parts for part in range(1, self.grid_parts))
            boundary = self._capacity_boundary(state, token_in, token_out, held)
            if boundary is not None:
                values.add(boundary)
        if (request.allow_psm_dai_refund and isinstance(state, LitePsmState)
                and state.tout == 0 and token_in == state.dai.address and token_out == state.gem.address):
            # buyGem only accepts multiples of its USDC-to-DAI conversion factor.
            values.add(held - held % state.to18_conversion_factor)
        values.update(self.suggested_actions.get((state.record.pool_id, token_in, token_out), ()))
        return tuple(sorted(value for value in values if type(value) is int and 0 < value <= held))

    @staticmethod
    def _capacity_boundary(
        state: PoolState, token_in: Address, token_out: Address, held: int
    ) -> int | None:
        """Add an observed finite exact-input boundary when the pure quote has a
        monotone accepted prefix.  Unknown/non-monotone models keep their grid.
        """
        try:
            state.quote_exact_in(token_in, token_out, held)
            return None
        except Unsupported:
            pass
        try:
            state.quote_exact_in(token_in, token_out, 1)
        except Unsupported:
            return None
        low, high = 1, held
        while low + 1 < high:
            middle = (low + high) // 2
            try:
                state.quote_exact_in(token_in, token_out, middle)
            except Unsupported:
                high = middle
            else:
                low = middle
        return low

    def _plan(self, request: TradeRequest, node: _Node, depth: int, expansions: int) -> Plan:
        return Plan(
            request=request,
            steps=node.steps,
            solver=self.name,
            search_info={
                "kind": "bounded_stateful_search",
                "search_limits": {
                    "max_steps": self.max_steps,
                    "beam_width": self.beam_width,
                    "max_expansions": self.max_expansions,
                    "grid_parts": self.grid_parts,
                    "exhaustive": self.exhaustive,
                },
                "depth": depth,
                "expansions": expansions,
                "search_truncated": self.truncated,
                "beam_pruned": self.beam_pruned,
            },
        )

    @staticmethod
    def _key(node: _Node) -> tuple[tuple[tuple[Address, int], ...], tuple[str, ...], int] | None:
        # PoolState has no required state-identity method.  Avoid unsoundly
        # deduplicating unmodelled states; trusted immutable V2/V3 dataclasses
        # retain a bounded repr key for practical replay search.
        if not all(isinstance(state, (UniV2State, UniV3State)) for state in node.states):
            return None
        return node.balances, tuple(repr(state) for state in node.states), len(node.steps)

    def _trim(self, nodes: list[_Node], target: Address) -> list[_Node]:
        # No dominance pruning: this only orders the finite beam.
        self.beam_pruned |= len(nodes) > self.beam_width
        return sorted(
            nodes,
            key=lambda node: (
                self._node_value(node),
                -node.held(self._request_input),
                Fraction(node.held(target), 10 ** self._token_decimals.get(target, 0)),
                -len(node.steps),
            ),
            reverse=True,
        )[: self.beam_width]

    def _backward_values(self, states: tuple[PoolState, ...], target: Address) -> dict[Address, Fraction]:
        """Approximate physical-token value in output units using local exact
        quotes.  It guides beam ordering only and is neither price evidence nor
        an optimizer bound."""
        values = {target: Fraction(1)}
        for _ in range(self.max_steps):
            changed = False
            for state in states:
                for token_in in state.tokens():
                    for token_out in state.tokens():
                        if token_in.address == token_out.address or token_out.address not in values:
                            continue
                        input_amount = 10 ** token_in.decimals
                        try:
                            output_amount = state.quote_exact_in(
                                token_in.address, token_out.address, input_amount
                            )
                        except Unsupported:
                            input_amount = 1
                            try:
                                output_amount = state.quote_exact_in(
                                    token_in.address, token_out.address, input_amount
                                )
                            except Unsupported:
                                continue
                        if output_amount <= 0:
                            continue
                        rate = Fraction(
                            output_amount * 10 ** token_in.decimals,
                            input_amount * 10 ** token_out.decimals,
                        )
                        candidate = rate * values[token_out.address]
                        if candidate > values.get(token_in.address, 0.0):
                            values[token_in.address] = candidate
                            changed = True
            if not changed:
                break
        return values

    def _node_value(self, node: _Node) -> Fraction:
        return sum(
            (
                Fraction(amount, 10 ** self._token_decimals.get(address, 0))
                * self._output_values.get(address, Fraction(0))
                for address, amount in node.balances
            ),
            Fraction(0),
        )

    def _rank_unique(
        self,
        plans: Iterable[Plan],
        states: tuple[PoolState, ...],
        request: TradeRequest,
        expansions: int,
    ) -> list[Plan]:
        best: dict[tuple[Step, ...], tuple[int, Plan]] = {}
        for plan in plans:
            evaluation = self._evaluator.evaluate(plan, states)
            if not evaluation.feasible:
                continue
            annotated = Plan(
                request=plan.request,
                steps=plan.steps,
                solver=plan.solver,
                search_info={
                    **plan.search_info,
                    "total_expansions": expansions,
                    "search_truncated": self.truncated,
                    "beam_pruned": self.beam_pruned,
                },
            )
            existing = best.get(plan.steps)
            if existing is None or evaluation.amount_out > existing[0]:
                best[plan.steps] = (evaluation.amount_out, annotated)
        return [item[1] for item in sorted(best.values(), key=lambda item: item[0], reverse=True)]

    def _record_unsupported(
        self, state: PoolState, token_in: Address, token_out: Address, amount: int, reason: str
    ) -> None:
        item = {
            "pool": state.record.pool_id,
            "token_in": token_in,
            "token_out": token_out,
            "input": amount,
            "reason": reason,
        }
        if item not in self.unsupported:
            self.unsupported.append(item)

    def _record_terminal_refusal(self, reasons: tuple[str, ...]) -> None:
        for reason in reasons:
            if reason not in self.terminal_refusals:
                self.terminal_refusals.append(reason)
