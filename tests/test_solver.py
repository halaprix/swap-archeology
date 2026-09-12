from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction

import pytest

from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address, PoolRecord, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.evaluator.fakes import ConstantProductPoolState
from swaparch.solver.baseline import BaselineSolver


def test_default_intermediates_follow_the_current_universe():
    states = (pool('ab', A, B, 1000, 1000), pool('bc', B, C, 1000, 1000))
    request = TradeRequest(A, C, 10)
    solver = BaselineSolver(grid_parts=1)
    results = [Evaluator().evaluate(plan, states) for plan in solver.solve(request, states)]
    assert any(result.feasible and len(result.steps) == 2 for result in results)
    assert set(solver.intermediates) == {A.address, B.address, C.address}
    assert BaselineSolver(intermediates=(), grid_parts=1).solve(request, states) == []
    solver.solve(TradeRequest(A, B, 10), states[:1])
    assert set(solver.intermediates) == {A.address, B.address}

A = Token(1, "0x0000000000000000000000000000000000000001", "A", 18)
B = Token(1, "0x0000000000000000000000000000000000000002", "B", 18)
C = Token(1, "0x0000000000000000000000000000000000000003", "C", 18)


def record(pool_id: str, tokens: tuple[Token, ...]) -> PoolRecord:
    return PoolRecord(
        family="fake",
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


def pool(pool_id: str, token0: Token, token1: Token, reserve0: int, reserve1: int) -> ConstantProductPoolState:
    return ConstantProductPoolState(record(pool_id, (token0, token1)), reserve0, reserve1)


def constant_product_out(reserve_in: int, reserve_out: int, amount_in: int) -> int:
    """Independent exact-in reference for the 0.3% fake-pool fee model."""
    numerator = amount_in * 9_970 * reserve_out
    denominator = reserve_in * 10_000 + amount_in * 9_970
    quotient = Fraction(numerator, denominator)
    return quotient.numerator // quotient.denominator


@dataclass(frozen=True)
class InventoryPool:
    record: PoolRecord
    inventory: int
    rate: int = 1

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if {token_in, token_out} != {A.address, B.address}:
            raise Unsupported("token direction not in pool")
        return amount_in * self.rate

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, InventoryPool]:
        if amount_in > self.inventory:
            raise Unsupported("insufficient inventory")
        return self.quote_exact_in(token_in, token_out, amount_in), replace(
            self, inventory=self.inventory - amount_in
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self.quote_exact_in(token_in, token_out, 0)
        return 1


@dataclass(frozen=True)
class TrianglePool:
    record: PoolRecord
    uses: int = 0

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if token_in == token_out or token_in not in {A.address, B.address, C.address} or token_out not in {A.address, B.address, C.address}:
            raise Unsupported("token direction not in pool")
        return max(0, amount_in - self.uses - 1)

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, TrianglePool]:
        return self.quote_exact_in(token_in, token_out, amount_in), replace(self, uses=self.uses + 1)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return 1


@dataclass(frozen=True)
class InventoryTriangle:
    record: PoolRecord
    inventory: int

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if token_in == token_out or token_in not in {A.address, B.address, C.address} or token_out not in {A.address, B.address, C.address}:
            raise Unsupported("token direction not in pool")
        return amount_in

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, InventoryTriangle]:
        if amount_in > self.inventory:
            raise Unsupported("insufficient inventory")
        return self.quote_exact_in(token_in, token_out, amount_in), replace(
            self, inventory=self.inventory - amount_in
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return 1


@dataclass(frozen=True)
class OrderTriangle:
    record: PoolRecord
    uses: int = 0

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if token_in == token_out or token_in not in {A.address, B.address, C.address} or token_out not in {A.address, B.address, C.address}:
            raise Unsupported("token direction not in pool")
        if (token_in, token_out) == (A.address, B.address) and self.uses >= 2:
            return amount_in * 100
        return amount_in

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, OrderTriangle]:
        return self.quote_exact_in(token_in, token_out, amount_in), replace(self, uses=self.uses + 1)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return 1


def test_exhaustive_integer_grid_matches_manual_allocation_and_conserves_input() -> None:
    states = (pool("first", A, B, 100, 100), pool("second", A, B, 100, 100))
    request = TradeRequest(A, B, 20)
    plans = BaselineSolver(intermediates=(), grid_parts=request.amount_in).solve(request, states)
    split = next(plan for plan in plans if plan.search_info["kind"] == "split")
    manual = [
        constant_product_out(100, 100, first_input)
        + constant_product_out(100, 100, request.amount_in - first_input)
        for first_input in range(request.amount_in + 1)
    ]
    assert Evaluator().evaluate(split, states).amount_out == max(manual)
    for plan in plans:
        evaluation = Evaluator().evaluate(plan, states)
        assert evaluation.feasible
        assert evaluation.amount_in_spent == request.amount_in
        assert evaluation.residual_in == 0


def test_odd_grid_split_conserves_input_and_records_actual_allocation() -> None:
    states = (pool("first", A, B, 100, 100), pool("second", A, B, 100, 100))
    request = TradeRequest(A, B, 51)
    plans = BaselineSolver(intermediates=(), grid_parts=4).solve(request, states)
    split = next(plan for plan in plans if plan.search_info["kind"] == "split")
    allocations = [row["amount_in"] for row in split.search_info["allocation"]]
    assert allocations in ([12, 39], [39, 12], [25, 26], [26, 25], [38, 13], [13, 38])
    assert sum(allocations) == request.amount_in
    assert Evaluator().evaluate(split, states).feasible


def test_split_considers_both_execution_orders() -> None:
    state = OrderTriangle(record("triangle", (A, B, C)))
    plans = BaselineSolver(intermediates=(C,), grid_parts=3).solve(TradeRequest(A, B, 5), (state,))
    split = next(plan for plan in plans if plan.search_info["kind"] == "split")
    assert [(step.token_in, step.token_out, step.amount_in) for step in split.steps] == [
        (A.address, C.address, 1),
        (C.address, B.address, 1),
        (A.address, B.address, 4),
    ]
    assert Evaluator().evaluate(split, (state,)).amount_out == 401


def test_split_is_not_worse_than_retained_unsplit_candidates() -> None:
    states = (pool("shallow", A, B, 40, 40), pool("deep", A, B, 1_000, 1_000))
    plans = BaselineSolver(intermediates=(), grid_parts=10).solve(TradeRequest(A, B, 20), states)
    outputs = {plan.search_info["kind"]: Evaluator().evaluate(plan, states).amount_out for plan in plans}
    split = next(plan for plan in plans if plan.search_info["kind"] == "split")
    assert outputs["split"] >= outputs["direct"]
    assert split.search_info["path_count"] == len(split.search_info["allocation"])
    assert split.search_info["fallback_kind"] == "direct"


def test_two_hop_plan_uses_the_updated_same_pool_state() -> None:
    state = TrianglePool(record("triangle", (A, B, C)))
    request = TradeRequest(A, C, 10)
    plans = BaselineSolver(intermediates=(B,), grid_parts=2).solve(request, (state,))
    path = next(plan for plan in plans if plan.search_info["kind"] == "path")
    evaluation = Evaluator().evaluate(path, (state,))
    assert [step.amount_in for step in path.steps] == [10, 9]
    assert evaluation.amount_out == 7


def test_finite_inventory_split_fills_cap_and_spends_the_remainder() -> None:
    capped = InventoryPool(record("psm", (A, B)), inventory=5, rate=2)
    fallback = InventoryPool(record("market", (A, B)), inventory=10)
    request = TradeRequest(A, B, 10)
    plans = BaselineSolver(intermediates=(), grid_parts=10).solve(request, (capped, fallback))
    split = next(plan for plan in plans if plan.search_info["kind"] == "split")
    assert [(step.pool_id, step.amount_in) for step in split.steps] == [("psm", 5), ("market", 5)]
    evaluation = Evaluator().evaluate(split, (capped, fallback))
    assert evaluation.feasible
    assert evaluation.amount_out == 15
    assert evaluation.amount_in_spent == request.amount_in


def test_rounded_grid_assigns_complements_to_each_route_regardless_of_state_order() -> None:
    high = InventoryPool(record("high", (A, B)), inventory=4, rate=2)
    low = InventoryPool(record("low", (A, B)), inventory=5)
    request = TradeRequest(A, B, 5)
    for states in ((high, low), (low, high)):
        plans = BaselineSolver(intermediates=(), grid_parts=3).solve(request, states)
        split = next(plan for plan in plans if plan.search_info["kind"] == "split")
        evaluation = Evaluator().evaluate(split, states)
        assert evaluation.feasible
        assert evaluation.amount_out == 9
        assert sum(row["amount_in"] for row in split.search_info["allocation"]) == 5


def test_same_inventory_state_cannot_be_consumed_twice() -> None:
    state = InventoryTriangle(record("triangle-cap", (A, B, C)), inventory=10)
    solver = BaselineSolver(intermediates=(C,), grid_parts=2)
    plans = solver.solve(TradeRequest(A, B, 6), (state,))
    assert all(plan.search_info["kind"] != "path" for plan in plans)
    assert {
        "path": ["triangle-cap", "triangle-cap"],
        "pool": "triangle-cap",
        "input": 6,
        "reason": "insufficient inventory",
    } in solver.unsupported


def test_request_validation_and_shared_capacity_rejection() -> None:
    solver = BaselineSolver(intermediates=())
    for amount in (True, 1.0):
        with pytest.raises(ValueError, match="integer"):
            solver.solve(TradeRequest(A, B, amount), ())
    with pytest.raises(ValueError, match="positive"):
        solver.solve(TradeRequest(A, B, 0), ())
    with pytest.raises(ValueError, match="distinct"):
        solver.solve(TradeRequest(A, A, 1), ())
    other_chain = Token(2, B.address, "B", 18)
    with pytest.raises(ValueError, match="one chain"):
        solver.solve(TradeRequest(A, other_chain, 1), ())
    for grid_parts in (True, 1.0, 0):
        with pytest.raises(ValueError, match="positive integer"):
            BaselineSolver(intermediates=(), grid_parts=grid_parts)

    left = pool("left", A, B, 100, 100)
    right = pool("right", A, B, 100, 100)
    left = replace(left, shared_capacity_ids=("shared",))
    right = replace(right, shared_capacity_ids=("shared",))
    solver = BaselineSolver(intermediates=(), grid_parts=2)
    plans = solver.solve(TradeRequest(A, B, 10), (left, right))
    assert [plan.search_info["kind"] for plan in plans] == ["direct", "split"]
    assert plans[1].search_info["fallback_kind"] == "direct"
    assert any(item["reason"] == "shared capacity not modelled" for item in solver.unsupported)


def test_direct_search_keeps_a_later_best_pool() -> None:
    states = tuple(
        InventoryPool(record(f"slow-{index}", (A, B)), inventory=10)
        for index in range(12)
    ) + (InventoryPool(record("best", (A, B)), inventory=10, rate=2),)
    plans = BaselineSolver(intermediates=(), grid_parts=1).solve(TradeRequest(A, B, 10), states)
    direct = next(plan for plan in plans if plan.search_info["kind"] == "direct")
    assert direct.steps[0].pool_id == "best"
