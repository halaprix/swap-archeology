"""Independent finite-endpoint checks for LitePSM solver support."""

from __future__ import annotations

from decimal import Decimal

import pytest

from swaparch.adapters.litepsm import HALTED, MAX_UINT256, WAD, LitePsmAdapter, LitePsmState
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, Token
from swaparch.snapshot.store import SnapshotStore
from swaparch.solver.dual import DualSolver
from swaparch.solver.litepsm_support import (
    finite_support,
    max_buy_gem_output,
    max_sell_gem_input,
)
from swaparch.universe import load_inventory, load_states

DAI = Token(1, "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18)
USDC = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
PSM = "0xf6e72db5454dd049d0788e411b06cfaf16853042"

PINS = (
    "0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623",
    "0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a",
    "0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc",
    "0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb",
    "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5",
)


def _record() -> PoolRecord:
    return PoolRecord(
        family="maker_sky_psm", chain=1, pool_id=f"maker_sky_psm:{PSM}:{PSM}",
        deployment=PSM, pool=PSM, tokens=(DAI, USDC), config={"model": "dss-lite-psm"},
        created_block=0, discovered_by={"method": "test"}, status=SupportStatus.SUPPORTED,
    )


def _state(*, tin: int = 10**16, tout: int = 2 * 10**16, buffer: int = 100 * 10**18,
           pocket: int = 7, allowance: int = 5) -> LitePsmState:
    return LitePsmState(_record(), DAI, USDC, 10**12, tin, tout, buffer, pocket, allowance)


def test_sell_endpoint_is_exact_and_capacity_boundary_is_replayed() -> None:
    state = _state(buffer=10 * 10**18)
    support = finite_support(state, USDC.address, DAI.address, Decimal(1), Decimal(1))
    assert support.recommendation == "endpoint"
    assert support.integer_amount_in == max_sell_gem_input(state)
    assert support.integer_amount_out == state.quote_exact_in(USDC.address, DAI.address, support.integer_amount_in)
    assert state.quote_exact_in(USDC.address, DAI.address, support.integer_amount_in - 1) > 0
    with pytest.raises(Unsupported, match="buffer has"):
        state.quote_exact_in(USDC.address, DAI.address, support.integer_amount_in + 1)


def test_buy_endpoint_uses_output_capacity_and_exact_input_lattice() -> None:
    state = _state(pocket=7, allowance=5)
    support = finite_support(state, DAI.address, USDC.address, Decimal(1), Decimal(3 * 10**12))
    assert support.variable == "gem_output"
    assert support.capacity == 5
    assert support.recommendation == "endpoint"
    assert support.integer_amount_out == 5
    assert support.integer_amount_in == state._buy_gem_required_dai(5)
    assert state.quote_exact_in(DAI.address, USDC.address, support.integer_amount_in) == 5
    with pytest.raises(Unsupported, match="unspent"):
        state.quote_exact_in(DAI.address, USDC.address, support.integer_amount_in - 1)
    with pytest.raises(Unsupported, match="pocket has|allowance"):
        state.quote_exact_in(DAI.address, USDC.address, state._buy_gem_required_dai(6))
    assert support.input_lattice is not None


def test_halts_fee_100_percent_and_ties_do_not_propose_an_endpoint() -> None:
    halted_sell = finite_support(_state(tin=HALTED), USDC.address, DAI.address, Decimal(1), Decimal(1))
    halted_buy = finite_support(_state(tout=HALTED), DAI.address, USDC.address, Decimal(1), Decimal(1))
    fee_100 = finite_support(_state(tin=WAD), USDC.address, DAI.address, Decimal(1), Decimal(1))
    assert halted_sell.capacity_status == halted_buy.capacity_status == "halted"
    assert fee_100.capacity_status == "fee_100_percent"

    state = _state(tin=10**16)
    rate = Decimal(state.to18_conversion_factor) * (Decimal(WAD - state.tin) / Decimal(WAD))
    tie = finite_support(state, USDC.address, DAI.address, rate, Decimal(1))
    assert tie.recommendation == "tie"
    assert tie.tie_interval == (Decimal(0), Decimal(tie.capacity))
    assert tie.integer_amount_in is None
    assert tie.capacity_status == "exact_replayed_tie_not_selected"
    assert tie.endpoint_amount_in is not None


def test_buy_prefix_includes_uint256_balance_and_fee_checks() -> None:
    factor = 10**12
    headroom = _state(
        tout=0, buffer=MAX_UINT256 - 2 * factor, pocket=5, allowance=5
    )
    assert max_buy_gem_output(headroom) == 2
    support = finite_support(headroom, DAI.address, USDC.address, Decimal(1), Decimal(3 * factor))
    assert support.capacity == support.integer_amount_out == 2
    assert support.capacity_status == "exact_replayed_endpoint"
    assert headroom.quote_exact_in(DAI.address, USDC.address, support.integer_amount_in) == 2
    with pytest.raises(Unsupported, match="DAI balance"):
        headroom.quote_exact_in(DAI.address, USDC.address, headroom._buy_gem_required_dai(3))

    fee_overflow = _state(tout=WAD, buffer=0, pocket=MAX_UINT256, allowance=MAX_UINT256)
    assert max_buy_gem_output(fee_overflow) > 0
    support = finite_support(fee_overflow, DAI.address, USDC.address, Decimal(1), Decimal(3 * factor))
    assert support.recommendation == "endpoint"
    assert support.integer_amount_out == max_buy_gem_output(fee_overflow)


def test_unprofitable_endpoint_is_diagnostic_not_selected_support() -> None:
    state = _state(tout=0, pocket=5, allowance=5)
    support = finite_support(state, DAI.address, USDC.address, Decimal(1), Decimal(1))
    assert support.recommendation == "no_trade"
    assert support.continuous_input == support.continuous_output == support.continuous_objective == 0
    assert support.endpoint_objective is not None and support.endpoint_objective < 0
    assert support.endpoint_amount_in is not None
    assert support.capacity_status == "exact_replayed_unprofitable_not_selected"


def test_dual_local_support_uses_litepsm_action_and_keeps_tie_profile() -> None:
    state = _state(tout=0, pocket=5, allowance=5)
    solver = DualSolver()
    values = {DAI.address: 10**18, USDC.address: 3 * 10**18}
    decimals = {DAI.address: DAI.decimals, USDC.address: USDC.decimals}
    candidate = solver._litepsm_local_support(state, DAI.address, USDC.address, values, decimals)
    assert candidate is not None and candidate.action is not None
    assert candidate.action.amount_out == 5
    profile = solver._litepsm_supports[(state.record.pool_id, DAI.address, USDC.address)]
    assert profile["recommendation"] == "endpoint"
    assert profile["capacity_status"] == "exact_replayed_endpoint"

    tie = solver._litepsm_local_support(
        state, DAI.address, USDC.address, {DAI.address: 10**18, USDC.address: 10**18}, decimals
    )
    assert tie is None
    profile = solver._litepsm_supports[(state.record.pool_id, DAI.address, USDC.address)]
    assert profile["recommendation"] == "tie"
    assert profile["tie_interval"] == ("0", "5")


def test_endpoint_recovery_replays_all_five_cached_pins() -> None:
    for block_hash in PINS:
        snapshot = SnapshotStore().load(1, block_hash)
        states, failures = load_states(
            {"maker_sky_psm": LitePsmAdapter()}, load_inventory("maker_sky_psm"), snapshot
        )
        assert failures
        state = next(item for item in states if item.record.pool == PSM)
        sell = finite_support(state, USDC.address, DAI.address, Decimal(1), Decimal(1))
        buy = finite_support(state, DAI.address, USDC.address, Decimal(1), Decimal(2 * 10**12))
        assert sell.recommendation == buy.recommendation == "endpoint"
        assert sell.integer_amount_out == state.quote_exact_in(USDC.address, DAI.address, sell.integer_amount_in)
        assert buy.integer_amount_out == state.quote_exact_in(DAI.address, USDC.address, buy.integer_amount_in)
