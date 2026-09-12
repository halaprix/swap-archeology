"""Offline checks for the V3 loaded-tick continuous candidate model."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, getcontext

import pytest

from swaparch.adapters.uniswap_v2 import UniV2State
from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.adapters.uniswap_v3 import math as m
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, Token, TradeRequest
from swaparch.snapshot.store import SnapshotStore
from swaparch.solver.dual import DualSolver
from swaparch.solver.v3_continuous import loaded_domain, price_optimal_support
from swaparch.universe import load_inventory, load_states


def _token(number: int, symbol: str) -> Token:
    return Token(1, f"0x{number:040x}", symbol, 18)


T0, T1 = _token(1, "T0"), _token(2, "T1")
R0 = Token(1, "0x0000000000000000000000000000000000000003", "R0", 0)
R1 = Token(1, "0x0000000000000000000000000000000000000004", "R1", 0)

PINS = (
    "0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623",
    "0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a",
    "0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc",
    "0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb",
    "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5",
)


def _record(name: str, tokens: tuple[Token, Token] = (T0, T1)) -> PoolRecord:
    return PoolRecord(
        family="synthetic", chain=1, pool_id=name,
        deployment="0x0000000000000000000000000000000000000010",
        pool="0x0000000000000000000000000000000000000011", tokens=tokens,
        config={"synthetic_only": True}, created_block=0, discovered_by={"method": "test"},
        status=SupportStatus.SUPPORTED,
    )


def _bit(tick: int) -> tuple[int, int]:
    word, bit = m.position(m.compress(tick, 1))
    return word, 1 << bit


def _state() -> UniV3State:
    low_word, low_bit = _bit(-100)
    high_word, high_bit = _bit(100)
    return UniV3State(
        _record("continuous-fixture"), 1 << 96, 0, 10**18, 3_000, 1, T0, T1,
        tick_bitmap={low_word: low_bit, high_word: high_bit},
        tick_liquidity_net={-100: 0, 100: 0}, word_lo=-1, word_hi=0,
    )


def _gap_state() -> UniV3State:
    low_a_word, low_a_bit = _bit(-10)
    _, low_b_bit = _bit(-100)
    high_a_word, high_a_bit = _bit(10)
    _, high_b_bit = _bit(100)
    return UniV3State(
        _record("zero-liquidity-gap"), 1 << 96, 0, 0, 500, 1, T0, T1,
        tick_bitmap={low_a_word: low_a_bit | low_b_bit, high_a_word: high_a_bit | high_b_bit},
        tick_liquidity_net={-10: -10**18, -100: 10**18, 10: 10**18, 100: -10**18},
        word_lo=-1, word_hi=0,
    )


def test_within_tick_stationary_price_matches_analytical_formula() -> None:
    state = _state()
    first = loaded_domain(state, True).intervals[0]
    start = Decimal(first.start_sqrt_x96) / Decimal(1 << 96)
    end = Decimal(first.end_sqrt_x96) / Decimal(1 << 96)
    stationary = (start + end) / 2
    gamma = Decimal("0.997")
    support, domain = price_optimal_support(
        state, True, gamma * stationary * stationary, Decimal(1)
    )
    assert support is not None
    selected = Decimal(support.end_sqrt_x96) / Decimal(1 << 96)
    assert end < selected < start
    assert support.objective > 0
    assert support.stop == "marginal_equality"
    assert domain.intervals[0].initialized_boundary is True


def test_loaded_edge_does_not_infer_an_unloaded_word() -> None:
    edge = UniV3State(
        _record("loaded-edge"), 1 << 96, 0, 10**18, 500, 1, T0, T1,
        tick_bitmap={0: 0}, tick_liquidity_net={}, word_lo=0, word_hi=0,
    )
    domain = loaded_domain(edge, False)
    assert len(domain.intervals) == 1
    assert domain.stop.startswith("loaded_domain_edge:")



def test_zero_liquidity_gap_crosses_only_loaded_initialized_ticks() -> None:
    state = _gap_state()
    expected = 998_500_052_003
    assert state.quote_exact_in(T0.address, T1.address, 10**12) == expected
    assert state.quote_exact_in(T1.address, T0.address, 10**12) == expected
    for zero_for_one, gap_tick in ((True, -5), (False, 5)):
        domain = loaded_domain(state, zero_for_one)
        assert domain.intervals[0].liquidity == 10**18
        assert domain.intervals[0].start_sqrt_x96 == m.get_sqrt_ratio_at_tick(
            -10 if zero_for_one else 10
        )
        gap_price = Decimal(m.get_sqrt_ratio_at_tick(gap_tick)) / Decimal(1 << 96)
        gamma = Decimal("0.9995")
        input_value = gamma * gap_price * gap_price if zero_for_one else Decimal(1)
        output_value = Decimal(1) if zero_for_one else gap_price * gap_price / gamma
        support, _ = price_optimal_support(state, zero_for_one, input_value, output_value)
        assert support is None


def test_protocol_price_limits_stop_without_walk_limit_or_tick_crossing() -> None:
    low_word, _ = _bit(m.MIN_TICK)
    high_word, _ = _bit(m.MAX_TICK)
    low = UniV3State(
        _record("near-min"), m.MIN_SQRT_RATIO + 1, m.MIN_TICK, 10**18, 500, 1, T0, T1,
        tick_bitmap={low_word: 0}, tick_liquidity_net={}, word_lo=low_word, word_hi=low_word,
    )
    high = UniV3State(
        _record("near-max"), m.MAX_SQRT_RATIO - 1, m.MAX_TICK - 1, 10**18, 500, 1, T0, T1,
        tick_bitmap={high_word: 0}, tick_liquidity_net={}, word_lo=high_word, word_hi=high_word,
    )
    assert loaded_domain(low, True).stop == "protocol_price_limit"
    assert loaded_domain(high, False).stop == "protocol_price_limit"


def test_decimal_context_is_scoped_even_when_calculation_raises() -> None:
    state = _state()
    before = getcontext().prec
    price_optimal_support(state, True, Decimal(1), Decimal(1))
    assert getcontext().prec == before
    with pytest.raises(InvalidOperation):
        price_optimal_support(state, True, Decimal("sNaN"), Decimal(1))
    assert getcontext().prec == before


def test_continuous_objective_survives_unprofitable_integer_rounding() -> None:
    state = UniV3State(
        _record("fractional-objective", (R0, R1)), 1 << 96, 0, 100, 500, 1, R0, R1,
        tick_bitmap={-1: 0, 0: 0}, tick_liquidity_net={}, word_lo=-1, word_hi=0,
    )
    request = TradeRequest(R0, R1, 1)
    solver = DualSolver()
    local = solver._v3_local_support(
        state, R0.address, R1.address, {R0.address: 1.0, R1.address: 2.0},
        {R0.address: 0, R1.address: 0}, request,
    )
    assert local is not None and local.value == pytest.approx(1.25475267041542567)
    assert local.action is None
    selected = solver._support(
        state, {R0.address: 1.0, R1.address: 2.0}, {R0.address: 0, R1.address: 0}, request,
    )
    assert selected is not None and selected.value == local.value


def test_dual_objective_cache_keeps_nearby_small_log_price_probes_distinct() -> None:
    state = UniV2State(_record("cache-key", (R0, R1)), 1_000, 1_000, R0, R1)
    probe = -13.815510557964274  # exp(probe) == approximately 1e-6

    class Result:
        x = (probe,)
        success = True
        message = "synthetic"
        nit = 1
        fun = 0.0
        jac = ()

    class Optimize:
        @staticmethod
        def minimize(fun, _x, **_kwargs):
            assert fun((probe,)) != fun((probe + 1e-8,))
            return Result()

    solver = DualSolver(max_steps=1, beam_width=4, max_expansions=20, grid_parts=1)
    solver._scipy = lambda: Optimize  # type: ignore[method-assign]
    solver.solve(TradeRequest(R0, R1, 1), (state,))


def test_domain_doubling_budget_is_labelled_a_verified_lower_bound() -> None:
    class NeverExhausted:
        @staticmethod
        def quote_exact_in(_token_in, _token_out, _amount_in):
            return 1

    cap, status = DualSolver._v3_domain_limit(NeverExhausted(), R0.address, R1.address, 1)
    assert cap == 2**32
    assert status == "verified_lower_bound_doubling_budget"


def test_cached_v3_boundary_and_integer_proposal_are_replayed_exactly() -> None:
    block_hash = "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5"
    snapshot = SnapshotStore().load(1, block_hash)
    states, _ = load_states({"uniswap_v3": UniswapV3Adapter()}, load_inventory("uniswap_v3"), snapshot)
    state = next(item for item in states if item.record.pool == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640")
    usdc = next(item for item in state.tokens() if item.symbol == "USDC")
    weth = next(item for item in state.tokens() if item.symbol == "WETH")

    domain = loaded_domain(state, True)
    assert domain.intervals and any(item.initialized_boundary for item in domain.intervals)
    assert domain.stop.startswith("loaded_domain_edge:")
    cap = DualSolver._v3_domain_cap(state, usdc.address, weth.address, 10**18)
    assert state.quote_exact_in(usdc.address, weth.address, cap) > 0
    with pytest.raises(Unsupported, match="insufficient tick coverage"):
        state.quote_exact_in(usdc.address, weth.address, cap + 1)

    solver = DualSolver()
    action = solver._v3_support(
        state, usdc.address, weth.address, {usdc.address: 1.0, weth.address: 3_000.0},
        {usdc.address: usdc.decimals, weth.address: weth.decimals}, TradeRequest(weth, usdc, 10**18),
    )
    assert action is not None
    assert action.amount_out == state.quote_exact_in(usdc.address, weth.address, action.amount_in)
    profile = next(iter(solver._v3_domains.values()))
    assert profile["support_model"] == "continuous loaded-tick intervals"
    assert profile["accepted_cap"] >= profile["quoted_amount_in"]


@pytest.mark.parametrize("block_hash", PINS)
def test_five_qualified_pins_have_noninvented_loaded_domains(block_hash: str) -> None:
    snapshot = SnapshotStore().load(1, block_hash)
    states, _ = load_states({"uniswap_v3": UniswapV3Adapter()}, load_inventory("uniswap_v3"), snapshot)
    state = next(item for item in states if item.record.pool == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640")
    for zero_for_one in (True, False):
        domain = loaded_domain(state, zero_for_one)
        assert domain.intervals
        assert domain.stop.startswith("loaded_domain_edge:")
