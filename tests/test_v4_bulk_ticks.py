"""Offline checks for V4 sparse PoolManager tick reads."""

from eth_abi import encode

from swaparch.adapters.uniswap_v3 import math as v3
from swaparch.adapters.uniswap_v4.adapter import (
    POOL_MANAGER,
    ZERO_HOOK,
    UniswapV4Adapter,
    pool_key_id,
    tick_storage_slot,
)
from swaparch.core.types import BlockRef, CallResult, PoolRecord, SupportStatus, Token

BLOCK = BlockRef(1, 25_896_003, "0x00", 0)
ETH = "0x0000000000000000000000000000000000000000"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def record() -> PoolRecord:
    pool = pool_key_id(ETH, USDC, 3000, 60, ZERO_HOOK)
    return PoolRecord(
        family="uniswap_v4",
        chain=1,
        pool_id=f"uniswap_v4:{POOL_MANAGER}:{pool}",
        deployment=POOL_MANAGER,
        pool=pool,
        tokens=(Token(1, ETH, "ETH", 18), Token(1, USDC, "USDC", 6)),
        config={
            "currency0": ETH,
            "currency1": USDC,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": ZERO_HOOK,
        },
        created_block=21_688_545,
        discovered_by={"method": "fixture"},
        status=SupportStatus.SUPPORTED,
    )


class Snapshot:
    def __init__(self, calls):
        self.block = BLOCK
        self.calls = {
            (call.to, call.data): CallResult(call, True, raw, "fixture") for call, raw in calls
        }

    def get(self, spec):
        return self.calls[(spec.to, spec.data)]

    def has(self, spec):
        return (spec.to, spec.data) in self.calls


def test_bulk_tick_slots_match_canonical_mapping_formula():
    pool_id = bytes.fromhex(record().pool[2:])
    # Independent spelling of StateLibrary: keccak(int256(tick) ++ (keccak(poolId ++ 6) + 4)).
    from eth_utils import keccak

    state_slot = keccak(pool_id + (6).to_bytes(32, "big"))
    expected = keccak(
        (-60).to_bytes(32, "big", signed=True)
        + (int.from_bytes(state_slot, "big") + 4).to_bytes(32, "big")
    )
    assert tick_storage_slot(pool_id, -60) == expected


def test_bulk_tick_response_decodes_negative_int128_without_losing_word_coverage():
    adapter, rec = UniswapV4Adapter(word_radius=1, bulk_ticks=True), record()
    slot0 = adapter.slot0_spec(rec)
    liquidity = adapter.liquidity_spec(rec)
    bitmaps = [adapter.bitmap_spec(rec, word) for word in (-1, 0, 1)]
    tick_values = [-60, 0, 60]
    bulk = adapter.bulk_tick_spec(rec, tick_values)
    packed = [
        (123 << 0) | (10**17 << 128),
        (456 << 0),
        (789 << 0) | (((1 << 128) - 10**17) << 128),
    ]
    snapshot = Snapshot(
        [
            (
                slot0,
                "0x"
                + encode(
                    ["uint160", "int24", "uint24", "uint24"],
                    [v3.get_sqrt_ratio_at_tick(0), 0, 0, 3000],
                ).hex(),
            ),
            (liquidity, "0x" + encode(["uint128"], [10**18]).hex()),
            *[
                (spec, "0x" + encode(["uint256"], [value]).hex())
                for spec, value in zip(bitmaps, (1 << 255, 3, 0), strict=True)
            ],
            (
                bulk,
                "0x"
                + encode(["bytes32[]"], [[value.to_bytes(32, "big") for value in packed]]).hex(),
            ),
        ]
    )

    assert adapter.dependent_requests(rec, BLOCK, snapshot) == []
    state = adapter.load_state(rec, snapshot)
    assert state.tick_liquidity_net == {-60: 10**17, 0: 0, 60: -(10**17)}
    assert bulk.to == POOL_MANAGER
    assert adapter.prefetch_requests(rec, BLOCK, snapshot)[-1] == bulk
