"""Offline source-semantics checks for the bounded LitePSM adapter."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.litepsm import (
    HALTED,
    MAX_UINT256,
    SEL_ALLOWANCE,
    SEL_BALANCE_OF,
    SEL_DAI,
    SEL_GEM,
    SEL_POCKET,
    LitePsmAdapter,
    LitePsmState,
)
from swaparch.core.protocols import PoolState, Snapshot, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

PSM = "0xf6e72db5454dd049d0788e411b06cfaf16853042"
POCKET = "0x37305b1cd40574e4c5ce33f8e8306be057fd7341"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
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
        family="maker_sky_psm",
        chain=1,
        pool_id=f"maker_sky_psm:{PSM}:{PSM}",
        deployment=PSM,
        pool=PSM,
        tokens=(Token(1, DAI, "DAI", 18), Token(1, USDC, "USDC", 6)),
        config={"model": "dss-lite-psm"},
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def raw(types: list[str], values: list[object]) -> str:
    return "0x" + abi_encode(types, values).hex()


def fixture(
    adapter: LitePsmAdapter, *, tin: int = 10**16, tout: int = 2 * 10**16,
    allowance: int = 5_000_000,
) -> FakeSnapshot:
    calls: list[CallResult] = []
    values = {
        "litepsm:gem": raw(["address"], [USDC]),
        "litepsm:dai": raw(["address"], [DAI]),
        "litepsm:pocket": raw(["address"], [POCKET]),
        "litepsm:to18ConversionFactor": raw(["uint256"], [10**12]),
        "litepsm:tin": raw(["uint256"], [tin]),
        "litepsm:tout": raw(["uint256"], [tout]),
    }
    for spec in adapter.read_requests(record(), BLOCK):
        calls.append(CallResult(spec, True, values[spec.tag], "fixture"))
    for spec, balance in (
        (adapter.balance_spec(DAI, PSM, "litepsm:daiBalance"), 10_000 * 10**18),
        (adapter.balance_spec(USDC, POCKET, "litepsm:pocketGemBalance"), 5_000_000),
        (adapter.allowance_spec(USDC, POCKET, PSM, "litepsm:pocketGemAllowance"), allowance),
    ):
        calls.append(CallResult(spec, True, raw(["uint256"], [balance]), "fixture"))
    return FakeSnapshot(calls)


def load(**kwargs: int) -> LitePsmState:
    adapter = LitePsmAdapter()
    return adapter.load_state(record(), fixture(adapter, **kwargs))


def test_adapter_uses_source_exact_two_phase_reads() -> None:
    adapter = LitePsmAdapter()
    static = adapter.read_requests(record(), BLOCK)
    assert [spec.data for spec in static] == [
        SEL_GEM,
        SEL_DAI,
        SEL_POCKET,
        "0x4010f777",
        "0x568d4b6f",
        "0xfae036d5",
    ]
    assert adapter.dependent_requests(record(), BLOCK, FakeSnapshot([])) == []
    snap = fixture(adapter)
    phase_one = FakeSnapshot(list(snap._calls.values())[:6])
    dependent = adapter.dependent_requests(record(), BLOCK, phase_one)
    assert [spec.tag for spec in dependent] == [
        "litepsm:daiBalance",
        "litepsm:pocketGemBalance",
        "litepsm:pocketGemAllowance",
    ]
    assert adapter.dependent_requests(record(), BLOCK, snap) == []
    balance = adapter.balance_spec(DAI, PSM, "x")
    assert balance.data.startswith(SEL_BALANCE_OF)
    assert balance.to == DAI
    assert adapter.allowance_spec(USDC, POCKET, PSM, "x").data.startswith(SEL_ALLOWANCE)


def test_protocols_and_loaded_inventories() -> None:
    adapter = LitePsmAdapter()
    state = adapter.load_state(record(), fixture(adapter))
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(state, PoolState)
    assert isinstance(fixture(adapter), Snapshot)
    assert state.dai_buffer == 10_000 * 10**18
    assert state.pocket_gem == 5_000_000
    assert state.pocket_gem_allowance == 5_000_000
    assert state.capacity_ids() == (
        f"maker_sky_psm:{PSM}:dai_buffer",
        f"maker_sky_psm:{PSM}:pocket_usdc",
    )


def test_source_fee_rounding_and_finite_dai_buffer() -> None:
    state = load()
    usdc_in = 1_234_567
    gross = 1_234_567_000_000_000_000
    expected_out = gross - 12_345_670_000_000_000  # Solidity: gross * tin / WAD, floored.
    dai_out, after = state.swap(USDC, DAI, usdc_in)
    assert dai_out == expected_out
    assert after.dai_buffer == state.dai_buffer - expected_out
    assert after.pocket_gem == state.pocket_gem + usdc_in
    with pytest.raises(Unsupported, match="buffer has"):
        state.swap(USDC, DAI, 20_000_000_000)


def test_dai_exact_input_requires_buygem_attainability_and_consumes_inventory() -> None:
    state = load()
    exact_dai_in = 1_020_000_000_000_000_000  # 1 USDC plus a 2% tout fee.
    usdc_out, after = state.swap(DAI, USDC, exact_dai_in)
    assert usdc_out == 1_000_000
    assert after.dai_buffer == state.dai_buffer + exact_dai_in
    assert after.pocket_gem == state.pocket_gem - usdc_out
    assert after.pocket_gem_allowance == state.pocket_gem_allowance - usdc_out
    with pytest.raises(Unsupported, match="leaving .* unspent"):
        state.quote_exact_in(DAI, USDC, exact_dai_in - 1)
    with pytest.raises(Unsupported, match="leaving .* unspent"):
        state.quote_exact_in(DAI, USDC, 1)


def test_direction_halts_and_pocket_capacity_are_explicit() -> None:
    with pytest.raises(Unsupported, match="sellGem is halted"):
        load(tin=HALTED).quote_exact_in(USDC, DAI, 1)
    with pytest.raises(Unsupported, match="buyGem is halted"):
        load(tout=HALTED).quote_exact_in(DAI, USDC, 10**12)
    with pytest.raises(Unsupported, match="buyGem is halted"):
        load(tout=HALTED).quote_exact_in(DAI, USDC, 0)
    state = load(tout=0)
    with pytest.raises(Unsupported, match="pocket has"):
        state.quote_exact_in(DAI, USDC, 6_000_000 * 10**12)


def test_buygem_checks_and_consumes_usdc_allowance_including_max() -> None:
    with pytest.raises(Unsupported, match="USDC allowance"):
        load(tout=0, allowance=999_999).quote_exact_in(DAI, USDC, 10**18)
    state = load(tout=0, allowance=MAX_UINT256)
    _, after = state.swap(DAI, USDC, 10**18)
    assert after.pocket_gem_allowance == MAX_UINT256 - 1_000_000


def test_load_state_reports_missing_failed_and_malformed_reads() -> None:
    adapter = LitePsmAdapter()
    with pytest.raises(Unsupported, match="missing gem\\(\\)"):
        adapter.load_state(record(), FakeSnapshot([]))

    snap = fixture(adapter)
    tin = next(call for call in snap._calls.values() if call.spec.tag == "litepsm:tin")
    snap._calls[(tin.spec.to, tin.spec.data)] = CallResult(tin.spec, False, "0x", "fixture")
    with pytest.raises(Unsupported, match="failed tin\\(\\)"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    tout = next(call for call in snap._calls.values() if call.spec.tag == "litepsm:tout")
    snap._calls[(tout.spec.to, tout.spec.data)] = CallResult(tout.spec, True, "0x1234", "fixture")
    with pytest.raises(Unsupported, match="malformed ABI for tout\\(\\)"):
        adapter.load_state(record(), snap)


def test_state_is_immutable_and_rejects_wrong_pair() -> None:
    state = load()
    with pytest.raises(FrozenInstanceError):
        state.tin = 0  # type: ignore[misc]
    with pytest.raises(Unsupported, match="not LitePSM"):
        state.quote_exact_in(DAI, PSM, 1)


def test_trust_boundaries_reject_non_integers_and_wrong_model() -> None:
    state = load()
    for amount in (True, 1.0):
        with pytest.raises(Unsupported, match="exact input must be a uint256 integer"):
            state.quote_exact_in(DAI, USDC, amount)  # type: ignore[arg-type]
    for value in (True, 1.0, -1, MAX_UINT256 + 1):
        with pytest.raises(Unsupported, match="pocket_gem must be a uint256 integer"):
            dataclasses.replace(state, pocket_gem=value)
    legacy = dataclasses.replace(record(), config={"model": "dss-psm"})
    with pytest.raises(Unsupported, match="not a DssLitePsm record"):
        LitePsmAdapter().load_state(legacy, fixture(LitePsmAdapter()))


def test_other_deployments_and_mismatched_immutable_identity_are_rejected():
    adapter = LitePsmAdapter()
    with pytest.raises(Unsupported, match="qualified Ethereum"):
        adapter.read_requests(dataclasses.replace(record(), pool=POCKET), BLOCK)
    snap = fixture(adapter)
    old = snap.get(CallSpec(PSM, SEL_POCKET))
    snap._calls[(PSM, SEL_POCKET)] = CallResult(old.spec, True, raw(["address"], [PSM]))
    with pytest.raises(Unsupported, match="immutable token/pocket identity mismatch"):
        adapter.load_state(record(), snap)
