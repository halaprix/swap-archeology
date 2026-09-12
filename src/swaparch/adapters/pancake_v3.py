"""PancakeSwap V3 source adapter: canonical contracts, exact integer math, per-block state, read plan.

Verifies and implements canonical PancakeSwap V3 semantics on Ethereum mainnet:
- Factory: 0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865
- QuoterV2: 0xb048bbc1ee6b733fffcfb9e9cef7375518e25997
- PoolDeployer: 0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9
- SwapRouter: 0x1b81d678ffb9c0263b24a97847620c99d213eb14
- TickLens: 0x9a489505a00ce272eaa5e07dba6491314cae3796

Key semantic differences from Uniswap V3:
1. slot0 tuple: 6th element `feeProtocol` is `uint32` (packs two 16-bit values, token0 and
   token1 protocol fee in hundredths of a bip / fee denominator 10,000, e.g. 209718400).
   In Uniswap V3, `feeProtocol` is `uint8`. Decoding Pancake slot0 with UniV3 types fails
   with ValueOutOfBounds / DecodingError.
2. Canonical fee tiers & tick spacings:
   - fee 100: tickSpacing 1 (0.01%)
   - fee 500: tickSpacing 10 (0.05%)
   - fee 2500: tickSpacing 50 (0.25%, differs from UniV3 fee 3000 / tickSpacing 60)
   - fee 10000: tickSpacing 200 (1.00%)
3. Protocol fee:
   Taken as a fraction `feeProtocol / 10000` of swap feeAmount and diverted from LP fee
   growth to protocol treasury. Does NOT alter trader input consumption or output amount.
4. Liquidity mining hooks (lmPool):
   PancakeV3Pool interacts with IPancakeV3LmPool for MasterChef V3 rewards during swap.
   This affects only reward accounting, not pool reserves, ticks, pricing, or output.
5. Quoter partial fills:
   QuoterV2 catches revert in callback and returns amountReceived without verifying
   full input fill. Thin pools return deceptive partial quotes (e.g. 2972 units out for 1 WETH).
   PancakeV3State refuses partial fills and raises `Unsupported` when remaining != 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address
from .uniswap_v3 import math as m
from .uniswap_v3.adapter import (
    SEL_FEE,
    SEL_LIQUIDITY,
    SEL_SLOT0,
    SEL_TICK_BITMAP,
    SEL_TICK_SPACING,
    SEL_TICKS,
    SEL_TOKEN0,
    SEL_TOKEN1,
    TICKS_TYPES,
    _contiguous_range,
    _selector,
    _ticks_in_word,
)
from .uniswap_v3.state import GAS_ESTIMATE, UniV3State

#: Canonical deployments on Ethereum mainnet
PANCAKE_FACTORY = "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
PANCAKE_POOL_DEPLOYER = "0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9"
PANCAKE_QUOTER_V2 = "0xb048bbc1ee6b733fffcfb9e9cef7375518e25997"
PANCAKE_SWAP_ROUTER = "0x1b81d678ffb9c0263b24a97847620c99d213eb14"
PANCAKE_TICK_LENS = "0x9a489505a00ce272eaa5e07dba6491314cae3796"

SEL_LM_POOL = _selector("lmPool()")

#: Note: feeProtocol is uint32 (unlike UniV3 which is uint8)
PANCAKE_SLOT0_TYPES = ["uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"]

TAG_SLOT0 = "pancake_v3:slot0"
TAG_LIQUIDITY = "pancake_v3:liquidity"
TAG_FEE = "pancake_v3:fee"
TAG_TICK_SPACING = "pancake_v3:tickSpacing"
TAG_TOKEN0 = "pancake_v3:token0"
TAG_TOKEN1 = "pancake_v3:token1"


def tag_bitmap(word_pos: int) -> str:
    return f"pancake_v3:tickBitmap:{word_pos}"


def tag_tick(tick: int) -> str:
    return f"pancake_v3:ticks:{tick}"


DEFAULT_WORD_RADIUS = 8

PANCAKE_FEE_SPACINGS = {
    100: 1,
    500: 10,
    2500: 50,
    10000: 200,
}


@dataclass(frozen=True)
class PancakeV3State(UniV3State):
    """One PancakeSwap V3 pool, pinned to one block, quoting purely in memory.

    Inherits UniV3State's exact tick-bitmap traversal, compute_swap_step,
    and liquidity delta accounting while preserving Pancake-specific identity,
    family checks, and feeProtocol metadata.
    """

    fee_protocol: int = 0

    def __post_init__(self) -> None:
        if self.record.family != "pancake_v3":
            raise Unsupported(
                f"expected family 'pancake_v3', got {self.record.family!r} for {self.record.pool_id}"
            )
        super().__post_init__()

    def _swap(self, zero_for_one: bool, amount_specified: int) -> tuple[int, int, PancakeV3State]:
        amount0, amount1, new_base = super()._swap(zero_for_one, amount_specified)
        new_state = replace(
            self,
            sqrt_price_x96=new_base.sqrt_price_x96,
            tick=new_base.tick,
            liquidity=new_base.liquidity,
            fee=new_base.fee,
            tick_spacing=new_base.tick_spacing,
            token0=new_base.token0,
            token1=new_base.token1,
            tick_bitmap=new_base.tick_bitmap,
            tick_liquidity_net=new_base.tick_liquidity_net,
            word_lo=new_base.word_lo,
            word_hi=new_base.word_hi,
            gas=new_base.gas,
        )
        return amount0, amount1, new_state

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, PancakeV3State]:
        amount_out, new = super().swap(token_in, token_out, amount_in)
        return amount_out, new  # type: ignore[return-value]


class PancakeV3Adapter:
    """Implements ``core.protocols.SourceAdapter`` for PancakeSwap V3."""

    family = "pancake_v3"

    def __init__(self, word_radius: int = DEFAULT_WORD_RADIUS) -> None:
        if word_radius < 0:
            raise ValueError("word_radius must be >= 0")
        self.word_radius = word_radius

    def _verify_family(self, pool: PoolRecord) -> None:
        if pool.family != self.family:
            raise Unsupported(
                f"expected family {self.family!r}, got {pool.family!r} for {pool.pool_id}"
            )

    def _pool_address(self, pool: PoolRecord) -> str:
        self._verify_family(pool)
        return norm_address(pool.pool)

    def _static_specs(self, pool: PoolRecord) -> list[CallSpec]:
        to = self._pool_address(pool)
        return [
            CallSpec(to=to, data=SEL_SLOT0, tag=TAG_SLOT0),
            CallSpec(to=to, data=SEL_LIQUIDITY, tag=TAG_LIQUIDITY),
            CallSpec(to=to, data=SEL_FEE, tag=TAG_FEE),
            CallSpec(to=to, data=SEL_TICK_SPACING, tag=TAG_TICK_SPACING),
            CallSpec(to=to, data=SEL_TOKEN0, tag=TAG_TOKEN0),
            CallSpec(to=to, data=SEL_TOKEN1, tag=TAG_TOKEN1),
        ]

    def bitmap_spec(self, pool: PoolRecord, word_pos: int) -> CallSpec:
        data = SEL_TICK_BITMAP + abi_encode(["int16"], [word_pos]).hex()
        return CallSpec(to=self._pool_address(pool), data=data, tag=tag_bitmap(word_pos))

    def tick_spec(self, pool: PoolRecord, tick: int) -> CallSpec:
        data = SEL_TICKS + abi_encode(["int24"], [tick]).hex()
        return CallSpec(to=self._pool_address(pool), data=data, tag=tag_tick(tick))

    def window_words(self, tick: int, tick_spacing: int) -> range:
        centre = m.word_pos_for_tick(tick, tick_spacing)
        return range(centre - self.word_radius, centre + self.word_radius + 1)

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._verify_family(pool)
        specs = self._static_specs(pool)
        hint = pool.config.get("tick_hint")
        spacing = pool.config.get("tick_spacing") or pool.config.get("tickSpacing")
        if hint is not None and spacing:
            specs += [self.bitmap_spec(pool, w) for w in self.window_words(int(hint), int(spacing))]
        return specs

    def prefetch_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        self._verify_family(pool)
        specs = self._static_specs(pool)
        slot0_raw = self._decoded(
            snapshot, CallSpec(self._pool_address(pool), SEL_SLOT0, TAG_SLOT0)
        )
        spacing_raw = self._decoded(
            snapshot, CallSpec(self._pool_address(pool), SEL_TICK_SPACING, TAG_TICK_SPACING)
        )
        if slot0_raw is None or spacing_raw is None:
            return specs
        tick = abi_decode(PANCAKE_SLOT0_TYPES, slot0_raw)[1]
        tick_spacing = abi_decode(["int24"], spacing_raw)[0]
        words = list(self.window_words(tick, tick_spacing))
        specs.extend(self.bitmap_spec(pool, word) for word in words)
        return specs

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        self._verify_family(pool)
        slot0 = self._decoded(snapshot, CallSpec(self._pool_address(pool), SEL_SLOT0, TAG_SLOT0))
        spacing_raw = self._decoded(
            snapshot, CallSpec(self._pool_address(pool), SEL_TICK_SPACING, TAG_TICK_SPACING)
        )
        if slot0 is None or spacing_raw is None:
            return []
        tick = abi_decode(PANCAKE_SLOT0_TYPES, slot0)[1]
        tick_spacing = abi_decode(["int24"], spacing_raw)[0]

        words = list(self.window_words(tick, tick_spacing))
        missing = [
            self.bitmap_spec(pool, w) for w in words if not snapshot.has(self.bitmap_spec(pool, w))
        ]
        if missing:
            return missing

        wanted: list[CallSpec] = []
        for word_pos in words:
            raw = self._decoded(snapshot, self.bitmap_spec(pool, word_pos))
            if raw is None:
                raise Unsupported(
                    f"failed tickBitmap word {word_pos} for {pool.pool_id} at {snapshot.block.number}"
                )
            word = abi_decode(["uint256"], raw)[0]
            ticks = tuple(_ticks_in_word(word_pos, word, tick_spacing))
            for tick_value in ticks:
                spec = self.tick_spec(pool, tick_value)
                if not snapshot.has(spec):
                    wanted.append(spec)
        return wanted

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> PancakeV3State:
        self._verify_family(pool)
        to = self._pool_address(pool)

        def need(spec: CallSpec, what: str) -> bytes:
            raw = self._decoded(snapshot, spec)
            if raw is None:
                raise Unsupported(f"missing {what} for {pool.pool_id} at {snapshot.block.number}")
            return raw

        slot0 = abi_decode(PANCAKE_SLOT0_TYPES, need(CallSpec(to, SEL_SLOT0, TAG_SLOT0), "slot0()"))
        liquidity = abi_decode(
            ["uint128"], need(CallSpec(to, SEL_LIQUIDITY, TAG_LIQUIDITY), "liquidity()")
        )[0]
        fee = abi_decode(["uint24"], need(CallSpec(to, SEL_FEE, TAG_FEE), "fee()"))[0]
        tick_spacing = abi_decode(
            ["int24"], need(CallSpec(to, SEL_TICK_SPACING, TAG_TICK_SPACING), "tickSpacing()")
        )[0]
        token0_addr = norm_address(
            abi_decode(["address"], need(CallSpec(to, SEL_TOKEN0, TAG_TOKEN0), "token0()"))[0]
        )
        token1_addr = norm_address(
            abi_decode(["address"], need(CallSpec(to, SEL_TOKEN1, TAG_TOKEN1), "token1()"))[0]
        )

        token0 = _match_token(pool, token0_addr)
        token1 = _match_token(pool, token1_addr)

        sqrt_price_x96, tick = slot0[0], slot0[1]
        fee_protocol = slot0[5]

        bitmap: dict[int, int] = {}
        for word_pos in self.window_words(tick, tick_spacing):
            raw = self._decoded(snapshot, self.bitmap_spec(pool, word_pos))
            if raw is None:
                continue
            bitmap[word_pos] = abi_decode(["uint256"], raw)[0]
        if not bitmap:
            raise Unsupported(
                f"no tickBitmap words loaded for {pool.pool_id} at {snapshot.block.number}"
            )
        word_lo, word_hi = _contiguous_range(bitmap, m.word_pos_for_tick(tick, tick_spacing))

        liquidity_net: dict[int, int] = {}
        for word_pos in range(word_lo, word_hi + 1):
            ticks = tuple(_ticks_in_word(word_pos, bitmap[word_pos], tick_spacing))
            for tick_value in ticks:
                raw = self._decoded(snapshot, self.tick_spec(pool, tick_value))
                if raw is None:
                    raise Unsupported(
                        f"missing tick data for initialized tick {tick_value} in word {word_pos} "
                        f"for {pool.pool_id} at {snapshot.block.number}"
                    )
                liquidity_net[tick_value] = abi_decode(TICKS_TYPES, raw)[1]

        return PancakeV3State(
            record=pool,
            sqrt_price_x96=sqrt_price_x96,
            tick=tick,
            liquidity=liquidity,
            fee=fee,
            tick_spacing=tick_spacing,
            token0=token0,
            token1=token1,
            tick_bitmap={w: bitmap[w] for w in range(word_lo, word_hi + 1)},
            tick_liquidity_net=liquidity_net,
            fee_protocol=fee_protocol,
            word_lo=word_lo,
            word_hi=word_hi,
            gas=GAS_ESTIMATE,
        )

    def provenance(self, pool: PoolRecord, snapshot: Snapshot) -> Mapping[str, Any]:
        state = self.load_state(pool, snapshot)
        return {
            "family": self.family,
            "pool": state.record.pool,
            "block": snapshot.block.number,
            "block_hash": snapshot.block.hash,
            "word_radius_requested": self.word_radius,
            "words_loaded": [state.word_lo, state.word_hi],
            "tick_spacing": state.tick_spacing,
            "tick_range_covered": [
                state.word_lo * 256 * state.tick_spacing,
                ((state.word_hi + 1) * 256 - 1) * state.tick_spacing,
            ],
            "initialized_ticks_loaded": len(state.tick_liquidity_net),
            "fee_protocol": state.fee_protocol,
        }

    @staticmethod
    def _decoded(snapshot: Snapshot, spec: CallSpec) -> bytes | None:
        if not snapshot.has(spec):
            return None
        result = snapshot.get(spec)
        if not result.success:
            return None
        return bytes.fromhex(result.raw[2:])


def _match_token(pool: PoolRecord, address: str) -> Token:
    for token in pool.tokens:
        if token.address == address:
            return token
    raise Unsupported(
        f"pool {pool.pool_id} reports token {address} which is not in the discovered "
        f"record ({[t.address for t in pool.tokens]})"
    )
