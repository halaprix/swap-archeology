"""TickLens bulk reads retain the exact bitmap-defined V3 window."""

from eth_abi import encode as abi_encode

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallResult, PoolRecord, SupportStatus, Token

POOL = "0x1111111111111111111111111111111111111111"
WETH = "0x2222222222222222222222222222222222222222"
USDC = "0x3333333333333333333333333333333333333333"


class Snapshot:
    def __init__(self, block, calls):
        self.block = block
        self.calls = {(call.spec.to, call.spec.data): call for call in calls}

    def has(self, spec):
        return (spec.to, spec.data) in self.calls

    def get(self, spec):
        return self.calls[(spec.to, spec.data)]


def record():
    return PoolRecord(
        family="uniswap_v3", chain=1, pool_id="v3:test", deployment=POOL, pool=POOL,
        tokens=(Token(1, WETH, "WETH", 18), Token(1, USDC, "USDC", 6)),
        config={}, created_block=None, discovered_by={}, status=SupportStatus.SUPPORTED,
    )


def result(spec, raw):
    return CallResult(spec, True, "0x" + raw.hex(), "fixture")


def snapshot(adapter, rows=((20, -4, 5), (10, 2, 3))):
    block = BlockRef(1, 21_895_170, "0x01", 0)
    pool = record()
    static = adapter._static_specs(pool)
    calls = [
        result(static[0], abi_encode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], [2**96, 0, 0, 0, 0, 0, True])),
        result(static[1], abi_encode(["uint128"], [100])),
        result(static[2], abi_encode(["uint24"], [500])),
        result(static[3], abi_encode(["int24"], [10])),
        result(static[4], abi_encode(["address"], [WETH])),
        result(static[5], abi_encode(["address"], [USDC])),
        result(adapter.bitmap_spec(pool, 0), abi_encode(["uint256"], [6])),
        result(adapter.tick_lens_spec(pool, 0), abi_encode(["(int24,int128,uint128)[]"], [rows])),
    ]
    return pool, Snapshot(block, calls)


def test_bulk_tick_lens_replaces_individual_ticks_and_prefetches_current_window():
    adapter = UniswapV3Adapter(word_radius=0, bulk_ticks=True)
    pool, snap = snapshot(adapter)
    without_lens = Snapshot(snap.block, [call for call in snap.calls.values() if call.spec.tag != "univ3:tickLens:0"])
    assert [spec.tag for spec in adapter.dependent_requests(pool, snap.block, without_lens)] == [
        "univ3:tickLens:0"
    ]
    assert adapter.dependent_requests(pool, snap.block, snap) == []
    assert [spec.tag for spec in adapter.prefetch_requests(pool, snap.block, snap)] == [
        "univ3:slot0", "univ3:liquidity", "univ3:fee", "univ3:tickSpacing", "univ3:token0",
        "univ3:token1", "univ3:tickBitmap:0", "univ3:tickLens:0",
    ]
    assert adapter.load_state(pool, snap).tick_liquidity_net == {10: 2, 20: -4}


def test_bulk_tick_lens_rejects_a_response_missing_a_bitmap_tick():
    adapter = UniswapV3Adapter(word_radius=0, bulk_ticks=True)
    pool, snap = snapshot(adapter, rows=((10, 2, 3),))
    try:
        adapter.load_state(pool, snap)
    except Unsupported as exc:
        assert str(exc) == "incomplete TickLens response for word 0"
    else:  # pragma: no cover - the exact completeness guard is the point here
        raise AssertionError("accepted an incomplete TickLens response")
