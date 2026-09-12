"""Exactness checks for the baseline split fast path."""

from __future__ import annotations

from dataclasses import dataclass, replace

from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address, Plan, PoolRecord, Step, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.evaluator.fakes import ConstantProductPoolState
from swaparch.solver.baseline import BaselineSolver


def token(number: int, symbol: str) -> Token:
    return Token(1, f"0x{number:040x}", symbol, 18)


A, B, C = token(1, "A"), token(2, "B"), token(3, "C")


def record(pool_id: str, tokens: tuple[Token, ...]) -> PoolRecord:
    return PoolRecord(
        family="synthetic",
        chain=1,
        pool_id=pool_id,
        deployment="0x0000000000000000000000000000000000000010",
        pool="0x0000000000000000000000000000000000000011",
        tokens=tokens,
        config={},
        created_block=0,
        discovered_by={"method": "test"},
        status=SupportStatus.SUPPORTED,
    )


def pool(pool_id: str, reserve0: int, reserve1: int) -> ConstantProductPoolState:
    return ConstantProductPoolState(record(pool_id, (A, B)), reserve0, reserve1)


def reference_best_split(request: TradeRequest, states: tuple[ConstantProductPoolState, ...], grid_parts: int):
    """Independent full evaluator enumeration for parallel direct pools."""
    evaluator = Evaluator()
    best = None
    amounts = sorted({
        amount
        for part in range(1, grid_parts)
        for amount in (request.amount_in * part // grid_parts,
                       request.amount_in - request.amount_in * part // grid_parts)
        if 0 < amount < request.amount_in
    })
    for first_amount in amounts:
        for left in range(len(states)):
            for right in range(left + 1, len(states)):
                for first, second, allocations in (
                    (states[left], states[right], (first_amount, request.amount_in - first_amount)),
                    (states[right], states[left], (request.amount_in - first_amount, first_amount)),
                ):
                    plan = Plan(
                        request,
                        (
                            Step(first.record.pool_id, A.address, B.address, allocations[0]),
                            Step(second.record.pool_id, A.address, B.address, allocations[1]),
                        ),
                        "reference",
                    )
                    evaluation = evaluator.evaluate(plan, states)
                    if evaluation.feasible and (best is None or evaluation.amount_out > best.amount_out):
                        best = evaluation
    return best


def test_disjoint_split_matches_full_evaluator_grid() -> None:
    states = (pool("one", 90, 120), pool("two", 140, 140), pool("three", 500, 440))
    request = TradeRequest(A, B, 37)
    expected = reference_best_split(request, states, grid_parts=7)
    plans = BaselineSolver(intermediates=(), grid_parts=7).solve(request, states)
    actual = Evaluator().evaluate(next(plan for plan in plans if plan.search_info["kind"] == "split"), states)
    assert actual.feasible and expected is not None
    assert actual.amount_out == expected.amount_out
    assert [(step.step.pool_id, step.step.amount_in) for step in actual.steps] == [
        (step.step.pool_id, step.step.amount_in) for step in expected.steps
    ]


@dataclass(frozen=True)
class OrderedTriangle:
    record: PoolRecord
    uses: int = 0

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if token_in == token_out or {token_in, token_out} - {A.address, B.address, C.address}:
            raise Unsupported("disabled direction")
        return amount_in * 100 if (token_in, token_out) == (A.address, B.address) and self.uses >= 2 else amount_in

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, OrderedTriangle]:
        return self.quote_exact_in(token_in, token_out, amount_in), replace(self, uses=self.uses + 1)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return 1


def test_overlapping_paths_keep_sequential_pool_order() -> None:
    state = OrderedTriangle(record("triangle", (A, B, C)))
    plan = next(plan for plan in BaselineSolver(intermediates=(C,), grid_parts=3).solve(
        TradeRequest(A, B, 5), (state,)
    ) if plan.search_info["kind"] == "split")
    assert [(step.token_in, step.token_out, step.amount_in) for step in plan.steps] == [
        (A.address, C.address, 1),
        (C.address, B.address, 1),
        (A.address, B.address, 4),
    ]
    assert Evaluator().evaluate(plan, (state,)).amount_out == 401


def test_cross_pool_shared_capacity_is_reported_without_split_compilation() -> None:
    states = (
        replace(pool("left", 100, 100), shared_capacity_ids=("shared",)),
        replace(pool("right", 100, 100), shared_capacity_ids=("shared",)),
    )
    solver = BaselineSolver(intermediates=(), grid_parts=6)
    plans = solver.solve(TradeRequest(A, B, 10), states)
    assert [plan.search_info["kind"] for plan in plans] == ["direct", "split"]
    assert plans[1].search_info["fallback_kind"] == "direct"
    assert solver.unsupported == [{
        "path": ["left", "right"], "pool": None, "input": 10,
        "reason": "shared capacity not modelled",
    }]
