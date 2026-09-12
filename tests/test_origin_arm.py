"""Offline checks for the bounded historical Origin Lido ARM model."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.origin_arm import (
    ARM,
    MAX_UINT256,
    PRICE_SCALE,
    SEL_BALANCE_OF,
    SEL_GET_RESERVES,
    SEL_IMPLEMENTATION,
    SEL_PAUSED,
    SEL_SHARES_OF,
    SEL_TOKEN0,
    SEL_TOKEN1,
    SEL_TOTAL_POOLED_ETHER,
    SEL_TOTAL_SHARES,
    SEL_TRADERATE0,
    SEL_TRADERATE1,
    SEL_WITHDRAWS_CLAIMED,
    SEL_WITHDRAWS_QUEUED,
    STETH,
    WETH,
    OriginArmAdapter,
    OriginLidoArmState,
)
from swaparch.core.protocols import PoolState, Snapshot, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

LEGACY = BlockRef(
    1, 23549991, "0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623", 0
)
RESERVES = BlockRef(
    1, 25896003, "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5", 0
)


class FakeSnapshot:
    def __init__(self, block: BlockRef, calls: list[CallResult]) -> None:
        self.block = block
        self._calls = {(call.spec.to, call.spec.data): call for call in calls}

    def get(self, spec: CallSpec) -> CallResult:
        return self._calls[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._calls


def record() -> PoolRecord:
    return PoolRecord(
        family="origin_arm",
        chain=1,
        pool_id=f"origin_arm:{ARM}:{ARM}",
        deployment=ARM,
        pool=ARM,
        tokens=(Token(1, WETH, "WETH", 18), Token(1, STETH, "stETH", 18)),
        config={
            "kind": "arm",
            "generation": "traderate (old) ABI",
            "historical_observations": {
                LEGACY.hash: {
                    "number": LEGACY.number,
                    "get_reserves": False,
                    "paused_getter": False,
                    "implementation": "0x1111111111111111111111111111111111111111",
                },
                RESERVES.hash: {
                    "number": RESERVES.number,
                    "get_reserves": True,
                    "paused_getter": True,
                    "implementation": "0x2222222222222222222222222222222222222222",
                },
            },
        },
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def raw(types: list[str], values: list[object]) -> str:
    return "0x" + abi_encode(types, values).hex()


def fixture(
    adapter: OriginArmAdapter,
    block: BlockRef = RESERVES,
    *,
    rate0: int = 2 * PRICE_SCALE,
    rate1: int = PRICE_SCALE,
    paused: bool = False,
    weth_balance: int = 100,
    queued: int = 80,
    claimed: int = 20,
    arm_shares: int = 50,
    pooled: int = 121,
    shares: int = 100,
) -> FakeSnapshot:
    steth_balance = arm_shares * pooled // shares
    values = {
        "origin_arm:token0": raw(["address"], [WETH]),
        "origin_arm:token1": raw(["address"], [STETH]),
        "origin_arm:paused": raw(["bool"], [paused]),
        "origin_arm:traderate0": raw(["uint256"], [rate0]),
        "origin_arm:traderate1": raw(["uint256"], [rate1]),
        "origin_arm:withdrawsQueued": raw(["uint256"], [queued]),
        "origin_arm:withdrawsClaimed": raw(["uint256"], [claimed]),
        "origin_arm:weth.balanceOf(arm)": raw(["uint256"], [weth_balance]),
        "origin_arm:steth.balanceOf(arm)": raw(["uint256"], [steth_balance]),
        "origin_arm:steth.sharesOf(arm)": raw(["uint256"], [arm_shares]),
        "origin_arm:steth.totalPooledEther": raw(["uint256"], [pooled]),
        "origin_arm:steth.totalShares": raw(["uint256"], [shares]),
        "origin_arm:getReserves": raw(["uint256", "uint256"], [max(0, weth_balance - queued + claimed), steth_balance]),
        "origin_arm:implementation": raw(
            ["address"], [record().config["historical_observations"][block.hash]["implementation"]]
        ),
    }
    return FakeSnapshot(
        block,
        [CallResult(spec, True, values[spec.tag], "fixture") for spec in adapter.read_requests(record(), block)],
    )


def load(block: BlockRef = RESERVES, **kwargs: int | bool) -> OriginLidoArmState:
    adapter = OriginArmAdapter()
    return adapter.load_state(record(), fixture(adapter, block, **kwargs))


def test_source_read_shapes_are_hash_pinned_and_protocol_conformant() -> None:
    adapter = OriginArmAdapter()
    legacy = adapter.read_requests(record(), LEGACY)
    reserves = adapter.read_requests(record(), RESERVES)
    arm_data = [spec.data for spec in reserves if spec.to == ARM]
    assert arm_data == [
        SEL_TOKEN0,
        SEL_TOKEN1,
        SEL_PAUSED,
        SEL_TRADERATE0,
        SEL_TRADERATE1,
        SEL_WITHDRAWS_QUEUED,
        SEL_WITHDRAWS_CLAIMED,
        SEL_GET_RESERVES,
        SEL_IMPLEMENTATION,
    ]
    assert SEL_PAUSED not in [spec.data for spec in legacy]
    assert [spec.data for spec in legacy if spec.to == ARM][-2:] == [
        SEL_WITHDRAWS_CLAIMED,
        SEL_IMPLEMENTATION,
    ]
    assert SEL_GET_RESERVES not in [spec.data for spec in legacy]
    balance_arg = abi_encode(["address"], [ARM]).hex()
    assert [spec.data for spec in reserves if spec.to == WETH] == [SEL_BALANCE_OF + balance_arg]
    assert [spec.data for spec in reserves if spec.to == STETH] == [
        SEL_BALANCE_OF + balance_arg,
        SEL_SHARES_OF + balance_arg,
        SEL_TOTAL_POOLED_ETHER,
        SEL_TOTAL_SHARES,
    ]
    assert adapter.dependent_requests(record(), RESERVES, FakeSnapshot(RESERVES, [])) == []
    state = load()
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(state, PoolState)
    assert isinstance(fixture(adapter), Snapshot)


def test_source_rate_quote_and_state_follow_actual_arm_steth_shares() -> None:
    state = load()
    assert (state.reserve0, state.reserve1) == (40, 60)
    assert state.capacity_ids() == (
        f"origin_arm:{ARM}:weth_reserve",
        f"origin_arm:{ARM}:steth_shares",
    )

    # Source returns a requested nominal amount.  Lido transfers
    # floor(requested * totalShares / totalPooledEther) shares; carrying those
    # shares prevents repeated quotes from reusing the same ARM inventory.
    out, after_buy = state.swap(WETH, STETH, 10)
    assert out == 20
    assert after_buy.steth_shares == 34
    assert after_buy.reserve1 == 41
    assert after_buy.weth_balance == 110
    assert after_buy.reserve0 == 50

    out, after_sell = state.swap(STETH, WETH, 10)
    assert out == 10
    assert after_sell.weth_balance == 90
    assert after_sell.reserve0 == 30
    assert after_sell.steth_shares == 58
    assert after_sell.reserve1 == 70


def test_directional_reserves_queue_floor_and_inventory_limits() -> None:
    state = load(weth_balance=19_293_422_946_852_522_412,
                 queued=32_055_516_038_235_438_057_358,
                 claimed=32_036_225_596_181_652_304_392,
                 arm_shares=313_738_966_461_309, pooled=1, shares=1)
    assert state.reserve0 == 2_980_893_066_769_446
    assert state.reserve1 == 313_738_966_461_309
    with pytest.raises(Unsupported, match="reserve has 2980893066769446"):
        state.quote_exact_in(STETH, WETH, state.reserve0 + 1)
    with pytest.raises(Unsupported, match="reserve has 313738966461309"):
        state.quote_exact_in(WETH, STETH, state.reserve1 // 2 + 1)
    insolvent = load(weth_balance=5, queued=8, claimed=0)
    assert insolvent.reserve0 == 0
    with pytest.raises(Unsupported, match="even for zero output"):
        insolvent.quote_exact_in(STETH, WETH, 0)
    with pytest.raises(Unsupported, match="exact-input quote overflows"):
        load(rate0=MAX_UINT256).quote_exact_in(WETH, STETH, 2)


def test_pauses_and_trust_boundaries_fail_closed() -> None:
    adapter = OriginArmAdapter()
    with pytest.raises(Unsupported, match="is paused"):
        adapter.load_state(record(), fixture(adapter, paused=True))
    with pytest.raises(Unsupported, match="no qualified historical ABI"):
        adapter.read_requests(
            record(), BlockRef(1, 25896003, "0x" + "11" * 32, 0)
        )
    with pytest.raises(Unsupported, match="not the qualified"):
        adapter.read_requests(dataclasses.replace(record(), family="lido"), RESERVES)
    with pytest.raises(Unsupported, match="exact input"):
        load().quote_exact_in(WETH, STETH, True)  # type: ignore[arg-type]
    with pytest.raises(Unsupported, match="not Origin"):
        load().quote_exact_in(WETH, WETH, 1)
    with pytest.raises(FrozenInstanceError):
        load().weth_balance = 0  # type: ignore[misc]


def test_record_requires_per_hash_abi_and_implementation_observations() -> None:
    adapter = OriginArmAdapter()
    extended = dataclasses.replace(
        record(),
        config={
            "kind": "arm",
            "generation": "traderate (old) ABI",
            "historical_observations": record().config["historical_observations"],
        },
    )
    data = [spec.data for spec in adapter.read_requests(extended, RESERVES)]
    assert SEL_GET_RESERVES in data
    assert SEL_IMPLEMENTATION in data
    incomplete = dataclasses.replace(
        extended, config={**extended.config, "historical_observations": {RESERVES.hash: {}}}
    )
    with pytest.raises(Unsupported, match="no qualified historical ABI"):
        adapter.read_requests(incomplete, RESERVES)

    missing_implementation = dataclasses.replace(
        extended,
        config={
            **extended.config,
            "historical_observations": {
                RESERVES.hash: {
                    "number": RESERVES.number,
                    "get_reserves": True,
                    "paused_getter": True,
                }
            },
        },
    )
    with pytest.raises(Unsupported, match="implementation mapping is malformed"):
        adapter.read_requests(missing_implementation, RESERVES)


def test_load_rejects_missing_failed_malformed_and_inconsistent_reads() -> None:
    adapter = OriginArmAdapter()
    with pytest.raises(Unsupported, match="missing token0"):
        adapter.load_state(record(), FakeSnapshot(RESERVES, []))

    snap = fixture(adapter)
    paused = next(call for call in snap._calls.values() if call.spec.tag == "origin_arm:paused")
    snap._calls[(paused.spec.to, paused.spec.data)] = CallResult(paused.spec, False, "0x", "fixture")
    with pytest.raises(Unsupported, match="failed paused"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    token0 = next(call for call in snap._calls.values() if call.spec.tag == "origin_arm:token0")
    snap._calls[(token0.spec.to, token0.spec.data)] = CallResult(
        token0.spec, True, raw(["address"], [STETH]), "fixture"
    )
    with pytest.raises(Unsupported, match="immutable token identity"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    implementation = next(
        call for call in snap._calls.values() if call.spec.tag == "origin_arm:implementation"
    )
    snap._calls[(implementation.spec.to, implementation.spec.data)] = CallResult(
        implementation.spec,
        True,
        raw(["address"], ["0x3333333333333333333333333333333333333333"]),
        "fixture",
    )
    with pytest.raises(Unsupported, match="implementation disagrees"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    reserves = next(call for call in snap._calls.values() if call.spec.tag == "origin_arm:getReserves")
    snap._calls[(reserves.spec.to, reserves.spec.data)] = CallResult(
        reserves.spec, True, raw(["uint256", "uint256"], [41, 60]), "fixture"
    )
    with pytest.raises(Unsupported, match=r"getReserves\(\) disagrees"):
        adapter.load_state(record(), snap)

    snap = fixture(adapter)
    balance = next(call for call in snap._calls.values() if call.spec.tag == "origin_arm:steth.balanceOf(arm)")
    snap._calls[(balance.spec.to, balance.spec.data)] = CallResult(
        balance.spec, True, raw(["uint256"], [61]), "fixture"
    )
    with pytest.raises(Unsupported, match="balance disagrees with shares"):
        adapter.load_state(record(), snap)
