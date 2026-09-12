"""Offline checks for the bounded Ethereum wstETH/stETH quote model."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.lido import (
    MAX_SUPPORTED_INPUT,
    SEL_SHARES_OF,
    SEL_STETH,
    SEL_TOTAL_POOLED_ETHER,
    SEL_TOTAL_SHARES,
    SEL_TOTAL_SUPPLY,
    STETH,
    WSTETH,
    LidoAdapter,
    LidoWstEthState,
)
from swaparch.core.protocols import PoolState, Snapshot, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

BLOCK = BlockRef(1, 25896003, "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5", 0)


class FakeSnapshot:
    def __init__(self, calls: list[CallResult]) -> None:
        self.block = BLOCK
        self._calls = {(call.spec.to, call.spec.data): call for call in calls}

    def get(self, spec: CallSpec) -> CallResult:
        return self._calls[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._calls


def record() -> PoolRecord:
    return PoolRecord(
        family="lido",
        chain=1,
        pool_id=f"lido:{WSTETH}:{WSTETH}",
        deployment=WSTETH,
        pool=WSTETH,
        tokens=(Token(1, STETH, "stETH", 18), Token(1, WSTETH, "wstETH", 18)),
        config={"model": "lido_wsteth"},
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def raw(types: list[str], values: list[object]) -> str:
    return "0x" + abi_encode(types, values).hex()


def fixture(adapter: LidoAdapter, *, pooled: int = 121, shares: int = 100, supply: int = 80,
            backing: int = 82) -> FakeSnapshot:
    values = {
        "lido:totalPooledEther": raw(["uint256"], [pooled]),
        "lido:totalShares": raw(["uint256"], [shares]),
        "lido:wstETH.stETH": raw(["address"], [STETH]),
        "lido:wstETH.totalSupply": raw(["uint256"], [supply]),
        "lido:stETH.sharesOf(wstETH)": raw(["uint256"], [backing]),
    }
    return FakeSnapshot([
        CallResult(spec, True, values[spec.tag], "fixture")
        for spec in adapter.read_requests(record(), BLOCK)
    ])


def load(**kwargs: int) -> LidoWstEthState:
    adapter = LidoAdapter()
    return adapter.load_state(record(), fixture(adapter, **kwargs))


def test_source_read_shape_and_protocols() -> None:
    adapter = LidoAdapter()
    specs = adapter.read_requests(record(), BLOCK)
    assert [spec.data for spec in specs] == [
        SEL_TOTAL_POOLED_ETHER,
        SEL_TOTAL_SHARES,
        SEL_STETH,
        SEL_TOTAL_SUPPLY,
        SEL_SHARES_OF + abi_encode(["address"], [WSTETH]).hex(),
    ]
    assert adapter.dependent_requests(record(), BLOCK, FakeSnapshot([])) == []
    state = adapter.load_state(record(), fixture(adapter))
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(state, PoolState)
    assert isinstance(fixture(adapter), Snapshot)
    assert state.capacity_ids() == (
        f"lido:{WSTETH}:wrapper_backing",
    )


def test_getter_reference_math_and_round_trip_are_integer_exact() -> None:
    state = load()
    # Matches getWstETHByStETH and getStETHByWstETH respectively at P=121, S=100.
    assert state.quote_exact_in(STETH, WSTETH, 1_000) == 826
    assert state.quote_exact_in(WSTETH, STETH, 10) == 12
    wrapped, after_wrap = state.swap(STETH, WSTETH, 1_000)
    assert wrapped == 826
    assert after_wrap.wsteth_supply == 906
    assert after_wrap.wrapper_steth_shares == 908
    unwrapped, after_unwrap = after_wrap.swap(WSTETH, STETH, wrapped)
    assert unwrapped == 999
    assert after_unwrap.wsteth_supply == 80
    assert after_unwrap.wrapper_steth_shares == 83


def test_unwrap_uses_finite_supply_and_actual_transfer_share_rounding() -> None:
    state = load(supply=10, backing=9)
    quote, after = state.swap(WSTETH, STETH, 10)
    assert quote == 12  # public getter return / requested stETH transfer amount
    assert after.wsteth_supply == 0
    assert after.wrapper_steth_shares == 0  # transfer(12) moves floor(12*100/121) shares
    assert (9 * 121 // 100) == 10  # recipient balance result can be below quote after two floors
    with pytest.raises(Unsupported, match="supply is 10"):
        state.quote_exact_in(WSTETH, STETH, 11)
    with pytest.raises(Unsupported, match="wrapper holds 8"):
        load(supply=10, backing=8).quote_exact_in(WSTETH, STETH, 10)


def test_zero_and_trust_boundaries_are_explicit() -> None:
    state = load()
    assert state.quote_exact_in(STETH, WSTETH, 0) == 0
    assert state.quote_exact_in(WSTETH, STETH, 0) == 0
    for amount in (True, 1.0, -1, MAX_SUPPORTED_INPUT):
        with pytest.raises(Unsupported, match="exact input"):
            state.quote_exact_in(STETH, WSTETH, amount)  # type: ignore[arg-type]
    for value in (True, 1.0, -1):
        with pytest.raises(Unsupported, match="total_shares must be a uint256 integer"):
            dataclasses.replace(state, total_shares=value)
    with pytest.raises(Unsupported, match=r"getTotalPooledEther\(\) is zero"):
        load(pooled=0)


def test_load_reports_missing_failed_malformed_and_bad_identity() -> None:
    adapter = LidoAdapter()
    with pytest.raises(Unsupported, match="missing wstETH.stETH"):
        adapter.load_state(record(), FakeSnapshot([]))

    snap = fixture(adapter)
    total = next(call for call in snap._calls.values() if call.spec.tag == "lido:totalShares")
    snap._calls[(total.spec.to, total.spec.data)] = CallResult(total.spec, False, "0x", "fixture")
    with pytest.raises(Unsupported, match="failed stETH.getTotalShares"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    total = next(call for call in snap._calls.values() if call.spec.tag == "lido:totalShares")
    snap._calls[(total.spec.to, total.spec.data)] = CallResult(total.spec, True, "0x1234", "fixture")
    with pytest.raises(Unsupported, match="malformed ABI for stETH.getTotalShares"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    steth = next(call for call in snap._calls.values() if call.spec.tag == "lido:wstETH.stETH")
    snap._calls[(steth.spec.to, steth.spec.data)] = CallResult(
        steth.spec, True, raw(["address"], [WSTETH]), "fixture"
    )
    with pytest.raises(Unsupported, match="immutable stETH"):
        adapter.load_state(record(), snap)


def test_state_is_immutable_and_rejects_noncanonical_records_or_pairs() -> None:
    state = load()
    with pytest.raises(FrozenInstanceError):
        state.total_shares = 1  # type: ignore[misc]
    with pytest.raises(Unsupported, match="not Lido"):
        state.quote_exact_in(STETH, WSTETH[:-1] + "1", 1)
    adapter = LidoAdapter()
    with pytest.raises(Unsupported, match="qualified Ethereum"):
        adapter.read_requests(dataclasses.replace(record(), family="other"), BLOCK)
    with pytest.raises(Unsupported, match="lido_wsteth"):
        adapter.read_requests(dataclasses.replace(record(), config={}), BLOCK)
