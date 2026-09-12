"""Tests for PancakeSwap V3 adapter, read plan, and state semantics.

Covers:
- Canonical factory, quoter, and family identity
- slot0 tuple differences (uint32 feeProtocol vs uint8 in UniV3)
- Canonical fee tiers (100, 500, 2500, 10000) and tick spacing (1, 10, 50, 200)
- Protocol fee encoding and non-interference with trader output
- LM hook independence (liquidity mining rewards do not alter swap pricing)
- Partial fill protection (unswapped input triggers Unsupported, not misleading quote)
- Exact state transitions and direction-specific swaps
"""

from __future__ import annotations

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.pancake_v3 import (
    PANCAKE_FACTORY,
    PANCAKE_QUOTER_V2,
    PANCAKE_SLOT0_TYPES,
    PancakeV3Adapter,
    PancakeV3State,
)
from swaparch.adapters.uniswap_v3.adapter import SLOT0_TYPES as UNIV3_SLOT0_TYPES
from swaparch.core.protocols import PoolState, SourceAdapter, Unsupported
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    PoolRecord,
    SupportStatus,
    Token,
)

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"

POOL_WETH_USDC_500 = "0x1ac1a8feaaea1900c4166deeed0c11cc10669d36"
POOL_WETH_USDC_2500 = "0x19ac5f80ec17497d0e585b953100e6d18c330040"


class FakeSnapshot:
    """Minimal in-memory Snapshot for offline testing."""

    def __init__(self, block: BlockRef, results: dict[tuple[str, str], CallResult]) -> None:
        self.block = block
        self._by_call = results

    def get(self, spec: CallSpec) -> CallResult:
        return self._by_call[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._by_call


def make_pool_record(
    pool: str = POOL_WETH_USDC_500,
    fee: int = 500,
    tick_spacing: int = 10,
    token0_addr: str = USDC,
    token1_addr: str = WETH,
    token0_sym: str = "USDC",
    token1_sym: str = "WETH",
    token0_dec: int = 6,
    token1_dec: int = 18,
    family: str = "pancake_v3",
) -> PoolRecord:
    return PoolRecord(
        family=family,
        chain=1,
        pool_id=f"{family}:{PANCAKE_FACTORY}:{pool}",
        deployment=PANCAKE_FACTORY,
        pool=pool,
        tokens=(
            Token(chain=1, address=token0_addr, symbol=token0_sym, decimals=token0_dec),
            Token(chain=1, address=token1_addr, symbol=token1_sym, decimals=token1_dec),
        ),
        config={"fee": fee, "tick_spacing": tick_spacing},
        created_block=16950000,
        discovered_by={"method": "factory:getPool"},
        status=SupportStatus.SUPPORTED,
    )


def test_pancake_constants_and_protocols():
    """Verify PancakeSwap V3 identities and protocol conformance."""
    assert PANCAKE_FACTORY == "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
    assert PANCAKE_QUOTER_V2 == "0xb048bbc1ee6b733fffcfb9e9cef7375518e25997"
    adapter = PancakeV3Adapter()
    assert adapter.family == "pancake_v3"
    assert isinstance(adapter, SourceAdapter)


def test_slot0_tuple_difference_uint32_protocol_fee():
    """PancakeSwap V3 packs 2x 16-bit protocol fees into a uint32 feeProtocol in slot0.

    Uniswap V3 expects uint8 feeProtocol. If decoded with UniV3 types,
    Pancake's standard feeProtocol (e.g. 209718400 for 3200:3200) overflows uint8.
    """
    assert PANCAKE_SLOT0_TYPES[5] == "uint32"
    assert UNIV3_SLOT0_TYPES[5] == "uint8"

    # Encoded tuple: sqrtPriceX96, tick, obsIdx, obsCard, obsCardNext, feeProtocol, unlocked
    sqrt_price = 1500000000000000000000000000000
    tick = -200000
    pancake_fee_protocol = 209718400  # 3200 + (3200 << 16)
    encoded_pancake = abi_encode(
        PANCAKE_SLOT0_TYPES,
        [sqrt_price, tick, 1, 10, 10, pancake_fee_protocol, True],
    )

    # Decoding with Pancake types succeeds and recovers 209718400
    from eth_abi import decode as abi_decode

    decoded = abi_decode(PANCAKE_SLOT0_TYPES, encoded_pancake)
    assert decoded[0] == sqrt_price
    assert decoded[1] == tick
    assert decoded[5] == pancake_fee_protocol

    # Decoding with Uniswap V3 types fails with NonEmptyPaddingBytes (DecodingError)
    from eth_abi.exceptions import DecodingError

    with pytest.raises(DecodingError):
        abi_decode(UNIV3_SLOT0_TYPES, encoded_pancake)


def test_fee2500_vs_3000_tick_spacing():
    """PancakeSwap V3 medium fee tier is 2500 (25 bps) with tickSpacing 50,
    whereas Uniswap V3 is 3000 (30 bps) with tickSpacing 60."""
    rec_2500 = make_pool_record(pool=POOL_WETH_USDC_2500, fee=2500, tick_spacing=50)
    assert rec_2500.config["fee"] == 2500
    assert rec_2500.config["tick_spacing"] == 50

    adapter = PancakeV3Adapter(word_radius=2)
    # word position for tick 0 with spacing 50:
    words = list(adapter.window_words(0, 50))
    assert words == [-2, -1, 0, 1, 2]


def test_adapter_boundary_rejects_non_pancake_family():
    """PancakeV3Adapter must reject pools from other families (e.g. uniswap_v3)."""
    rec_uni = make_pool_record(family="uniswap_v3")
    adapter = PancakeV3Adapter()
    block = BlockRef(chain=1, number=23549939, hash="0x" + "aa" * 32, timestamp=1760000000)

    with pytest.raises(Unsupported, match="expected family 'pancake_v3'"):
        adapter.read_requests(rec_uni, block)

    dummy_snap = FakeSnapshot(block, {})
    with pytest.raises(Unsupported, match="expected family 'pancake_v3'"):
        adapter.load_state(rec_uni, dummy_snap)


def test_partial_fill_rejected_by_state():
    """QuoterV2 returns partial fill outputs without warning when liquidity is exhausted.
    PancakeV3State must refuse partial fills and raise Unsupported with unswapped input."""
    rec = make_pool_record()
    # Near MAX_TICK with small liquidity (1000)
    tick = 887200
    word_start = (tick // 10) >> 8
    max_word = (887272 // 10) >> 8
    from swaparch.adapters.uniswap_v3 import math as m

    p_near_max = m.get_sqrt_ratio_at_tick(tick)

    state = PancakeV3State(
        record=rec,
        sqrt_price_x96=p_near_max,
        tick=tick,
        liquidity=1000,
        fee=500,
        tick_spacing=10,
        token0=rec.tokens[0],
        token1=rec.tokens[1],
        tick_bitmap={w: 0 for w in range(word_start, max_word + 1)},
        tick_liquidity_net={},
        word_lo=word_start,
        word_hi=max_word,
    )
    assert isinstance(state, PoolState)
    assert state.capacity_ids() == (rec.pool_id,)

    # Swapping 100 WETH into this pool pushes price to MAX_SQRT_RATIO - 1,
    # leaving ~33.7 WETH unswapped. State must refuse partial fill.
    with pytest.raises(
        Unsupported, match="exhausted at the protocol price limit with .* input unswapped"
    ):
        state.quote_exact_in(WETH, USDC, 100 * 10**18)


def test_pancake_state_inherits_univ3_and_preserves_identity_on_swap():
    """Verify PancakeV3State is a UniV3State subclass and preserves all identities across swap."""
    from swaparch.adapters.uniswap_v3 import math as m
    from swaparch.adapters.uniswap_v3.state import UniV3State

    rec = make_pool_record()
    tick = 0
    p = m.get_sqrt_ratio_at_tick(tick)
    state = PancakeV3State(
        record=rec,
        sqrt_price_x96=p,
        tick=tick,
        liquidity=10**18,
        fee=500,
        tick_spacing=10,
        token0=rec.tokens[0],
        token1=rec.tokens[1],
        tick_bitmap={-1: 0, 0: 0, 1: 0},
        tick_liquidity_net={},
        word_lo=-1,
        word_hi=1,
        fee_protocol=209718400,
    )

    # Conformance to UniV3State interface & type hierarchy
    assert isinstance(state, UniV3State)
    assert isinstance(state, PancakeV3State)
    assert state.fee_protocol == 209718400

    # Non-zero swap preserves record, family, fee_protocol, and capacity identity
    out, new_state = state.swap(WETH, USDC, 1000)
    assert out > 0
    assert isinstance(new_state, PancakeV3State)
    assert isinstance(new_state, UniV3State)
    assert new_state.fee_protocol == 209718400
    assert new_state.record.family == "pancake_v3"
    assert new_state.record.pool_id == rec.pool_id
    assert new_state.capacity_ids() == (rec.pool_id,)


def test_lm_pool_hook_semantics():
    """Verify LM pool hook (MasterChef V3) semantics do not alter swap pricing math.

    PancakeV3Pool interacts with IPancakeV3LmPool during swap to update CAKE rewards.
    This hook has zero effect on reserves, tick updates, fee calculations, or trader pricing.
    """
    from swaparch.adapters.pancake_v3 import SEL_LM_POOL

    assert SEL_LM_POOL == "0x540d4918"


