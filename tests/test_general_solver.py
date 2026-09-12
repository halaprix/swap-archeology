"""Synthetic-only acceptance checks for the phase-3 candidate solvers.

These pools are deliberately small state machines, not claims about any adapter
or historical venue.  Independent expected values are simple integer rates and
hard input caps.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from swaparch.adapters.uniswap_v2 import UINT112_MAX, UniV2State
from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address, Plan, PoolRecord, Step, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.snapshot.store import SnapshotStore
from swaparch.solver.dual import DualSolver
from swaparch.solver.search import GeneralSearchSolver
from swaparch.universe import load_inventory, load_states


def token(number: int, symbol: str) -> Token:
    return Token(1, f"0x{number:040x}", symbol, 18)


A, B, C, D, E = (token(index, symbol) for index, symbol in enumerate("ABCDE", 1))


def record(pool_id: str, tokens: tuple[Token, ...]) -> PoolRecord:
    return PoolRecord(
        family="synthetic",
        chain=1,
        pool_id=pool_id,
        deployment="0x0000000000000000000000000000000000000010",
        pool="0x0000000000000000000000000000000000000011",
        tokens=tokens,
        config={"synthetic_only": True},
        created_block=0,
        discovered_by={"method": "test"},
        status=SupportStatus.SUPPORTED,
    )


@dataclass(frozen=True)
class RateCap:
    record: PoolRecord
    rate: int = 1
    cap: int = 10**9
    used: int = 0
    gas: int | None = 1

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if (token_in, token_out) != tuple(token.address for token in self.record.tokens):
            raise Unsupported("disabled direction")
        if amount_in <= 0 or self.used + amount_in > self.cap:
            raise Unsupported("finite input cap")
        return amount_in * self.rate

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, RateCap]:
        return self.quote_exact_in(token_in, token_out, amount_in), replace(self, used=self.used + amount_in)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self.quote_exact_in(token_in, token_out, 1)
        return self.gas


@dataclass(frozen=True)
class HooklessUnsupported:
    record: PoolRecord

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        raise Unsupported("unsupported hook-bearing pool")

    def swap(self, token_in: Address, token_out: Address, amount_in: int):
        raise Unsupported("unsupported hook-bearing pool")

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return None


@dataclass(frozen=True)
class ThreeTokenOwner:
    record: PoolRecord
    capacity: int
    used: int = 0
    owner: str = "three-token-owner"

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.owner,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if (token_in, token_out) not in {(A.address, B.address), (B.address, C.address)}:
            raise Unsupported("disabled direction")
        if amount_in <= 0 or self.used + amount_in > self.capacity:
            raise Unsupported("shared finite inventory")
        return amount_in

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, ThreeTokenOwner]:
        return self.quote_exact_in(token_in, token_out, amount_in), replace(self, used=self.used + amount_in)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return 1


def best(request: TradeRequest, states, **limits):
    plans = GeneralSearchSolver(exhaustive=True, include_baseline=False, **limits).solve(request, states)
    assert plans
    evaluation = Evaluator().evaluate(plans[0], states)
    assert evaluation.feasible
    return plans[0], evaluation


def test_three_branches_merge_before_shared_downstream_pool() -> None:
    branches = tuple(RateCap(record(f"a-b-{index}", (A, B)), rate=2, cap=4) for index in range(3))
    downstream = RateCap(record("b-c", (B, C)), rate=1, cap=24)
    plan, evaluation = best(TradeRequest(A, C, 12), branches + (downstream,), max_steps=4, beam_width=512)
    assert evaluation.amount_out == 24
    assert [step.pool_id for step in plan.steps] == ["a-b-0", "a-b-1", "a-b-2", "b-c"]
    assert plan.steps[-1].amount_in == 24


def test_exhaustive_search_does_not_apply_beam_trimming() -> None:
    branches = tuple(RateCap(record(f"a-b-{index}", (A, B)), rate=2, cap=1) for index in range(3))
    downstream = RateCap(record("b-c", (B, C)), rate=1, cap=6)
    plan, evaluation = best(
        TradeRequest(A, C, 3), branches + (downstream,), max_steps=4, beam_width=1, max_expansions=10_000
    )
    assert evaluation.amount_out == 6
    assert plan.search_info["search_truncated"] is False


def test_regular_beam_recovers_three_branch_merge_with_value_ordering() -> None:
    branches = tuple(RateCap(record(f"beam-a-b-{index}", (A, B)), rate=2, cap=4) for index in range(3))
    downstream = RateCap(record("beam-b-c", (B, C)), rate=1, cap=24)
    solver = GeneralSearchSolver(
        max_steps=4, beam_width=512, max_expansions=50_000, grid_parts=32, include_baseline=False
    )
    plans = solver.solve(TradeRequest(A, C, 12), branches + (downstream,))
    assert Evaluator().evaluate(plans[0], branches + (downstream,)).amount_out == 24


def test_only_four_hop_route_is_expressible() -> None:
    states = tuple(
        RateCap(record(pool_id, pair), rate=2)
        for pool_id, pair in (("a-b", (A, B)), ("b-c", (B, C)), ("c-d", (C, D)), ("d-e", (D, E)))
    )
    plan, evaluation = best(TradeRequest(A, E, 3), states, max_steps=4, beam_width=128)
    assert evaluation.amount_out == 48
    assert len(plan.steps) == 4


def test_finite_cap_and_disabled_reverse_are_respected() -> None:
    capped = RateCap(record("psm", (A, B)), rate=2, cap=5)
    fallback = RateCap(record("market", (A, B)), rate=1)
    _, evaluation = best(TradeRequest(A, B, 7), (capped, fallback), max_steps=2, beam_width=64)
    assert evaluation.amount_out == 12


def test_unfunded_cycle_is_not_seeded() -> None:
    states = (RateCap(record("b-c", (B, C))), RateCap(record("c-b", (C, B))))
    plans = GeneralSearchSolver(max_steps=4, exhaustive=True, include_baseline=False).solve(
        TradeRequest(A, E, 3), states
    )
    assert plans == []


def test_three_token_owner_updates_once_and_duplicate_owner_is_rejected() -> None:
    shared = ThreeTokenOwner(record("three", (A, B, C)), capacity=8)
    request = TradeRequest(A, C, 4)
    plan, evaluation = best(request, (shared,), max_steps=2, beam_width=32)
    assert [step.token_out for step in plan.steps] == [B.address, C.address]
    assert evaluation.amount_out == 4

    left = ThreeTokenOwner(record("left", (A, B, C)), capacity=8)
    right = ThreeTokenOwner(record("right", (A, B, C)), capacity=8)
    duplicate = Plan(request, (Step("left", A.address, B.address, 4),
                               Step("right", B.address, C.address, 4)), "test")
    assert Evaluator().evaluate(duplicate, (left, right)).reasons == ("shared capacity not modelled",)


def test_unsupported_hook_is_visible_and_not_quoted_as_constant_product() -> None:
    pool = HooklessUnsupported(record("hook", (A, B)))
    solver = GeneralSearchSolver(max_steps=1, exhaustive=True, include_baseline=False)
    assert solver.solve(TradeRequest(A, B, 2), (pool,)) == []
    assert solver.unsupported[0]["reason"] == "unsupported hook-bearing pool"


def test_dual_uses_scipy_and_recovers_a_funded_v2_plan() -> None:
    state = UniV2State(record("v2", (A, B)), 1_000, 2_000, A, B)
    request = TradeRequest(A, B, 20)
    solver = DualSolver(max_steps=2, beam_width=64, max_expansions=2_000, grid_parts=20)
    plans = solver.solve(request, (state,))
    assert plans
    assert solver.last_diagnostics["status"] in {"converged", "optimizer_failed"}
    assert solver.last_diagnostics["model_coverage"]["v2"] == 1
    assert plans[0].search_info["bound_status"] == "numerical_dual_estimate_not_a_bound"
    assert Evaluator().evaluate(plans[0], (state,)).feasible


def test_v2_support_clamps_to_uint112_post_swap_domain() -> None:
    usdc = Token(1, "0x0000000000000000000000000000000000000009", "USDC", 6)
    state = UniV2State(record("near-uint112", (usdc, A)), UINT112_MAX - 1_000, UINT112_MAX, usdc, A)
    action = DualSolver()._v2_support(
        state, usdc.address, A.address, {usdc.address: 1.0, A.address: 1e18},
        {usdc.address: 6, A.address: 18},
    )
    assert action is not None
    assert 0 < action.amount_in <= 1_000
    assert action.amount_out > 0


def test_v3_domain_searches_below_raw_request_and_uses_token_decimals() -> None:
    block_hash = "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5"
    snapshot = SnapshotStore().load(1, block_hash)
    states, failures = load_states({"uniswap_v3": UniswapV3Adapter()}, load_inventory("uniswap_v3"), snapshot)
    assert failures
    state = next(item for item in states if item.record.pool == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640")
    usdc = next(item for item in state.tokens() if item.symbol == "USDC")
    weth = next(item for item in state.tokens() if item.symbol == "WETH")
    assert DualSolver._v3_domain_cap(state, usdc.address, weth.address, 10**18) > 1

    solver = DualSolver(dual_samples=2)
    solver._v3_support(
        state, usdc.address, weth.address, {usdc.address: 1.0, weth.address: 2_000.0},
        {usdc.address: usdc.decimals, weth.address: weth.decimals}, TradeRequest(weth, usdc, 10**18),
    )
    profile = next(iter(solver._v3_domains.values()))
    assert profile["seed"] == 10**6
    assert profile["accepted_cap"] > 1


def test_explicit_incumbent_is_retained_across_changed_budgets() -> None:
    states = (RateCap(record("capped", (A, B)), rate=3, cap=333), RateCap(record("market", (A, B))))
    request = TradeRequest(A, B, 1_000)
    prior = GeneralSearchSolver(
        max_steps=2, beam_width=128, max_expansions=10_000, grid_parts=3, include_baseline=False
    ).solve(request, states)[0]
    later = GeneralSearchSolver(
        max_steps=2, beam_width=128, max_expansions=10_000, grid_parts=4,
        incumbents=(prior,), include_baseline=False,
    ).solve(request, states)[0]
    assert Evaluator().evaluate(later, states).amount_out >= Evaluator().evaluate(prior, states).amount_out


def test_observed_finite_capacity_boundary_is_an_action_candidate() -> None:
    states = (RateCap(record("capped", (A, B)), rate=3, cap=333), RateCap(record("market", (A, B))))
    plans = GeneralSearchSolver(
        max_steps=2, beam_width=128, max_expansions=10_000, grid_parts=4, include_baseline=False
    ).solve(TradeRequest(A, B, 1_000), states)
    assert Evaluator().evaluate(plans[0], states).amount_out == 1_666


def test_gas_remains_separate_from_gross_output_when_unknown() -> None:
    expensive = RateCap(record("expensive", (A, B)), rate=3, gas=20)
    cheap = RateCap(record("cheap", (A, B)), rate=2, gas=1)
    unknown = RateCap(record("unknown", (A, B)), rate=4, gas=None)
    request = TradeRequest(A, B, 10)
    evaluations = [
        Evaluator().evaluate(Plan(request, (Step(pool.record.pool_id, A.address, B.address, 10),), "test"), (pool,))
        for pool in (expensive, cheap, unknown)
    ]
    assert [item.amount_out for item in evaluations] == [30, 20, 40]
    assert [item.gas_estimate for item in evaluations] == [20, 1, None]
    assert evaluations[0].amount_out - 20 < evaluations[1].amount_out - 1
