"""``SourceAdapter`` for Uniswap V3 pools.

Read plan
---------
Phase 1 (:meth:`UniswapV3Adapter.read_requests`) - six independent reads that
need nothing but the pool address::

    slot0()        0x3850c7bd  -> (uint160,int24,uint16,uint16,uint16,uint8,bool)
    liquidity()    0x1a686502  -> uint128
    fee()          0xddca3f43  -> uint24
    tickSpacing()  0xd0c93a7c  -> int24
    token0()       0x0dfe1681  -> address
    token1()       0xd21220a7  -> address

Phase 2 (:meth:`dependent_requests`, first pass) - the tick bitmap window::

    tickBitmap(int16)  0x5339c296 -> uint256

centred on the word holding ``slot0().tick``, ``word_radius`` words either side
(default 8 -> 17 words).  The window is deliberately part of the snapshot's
provenance: a quote is only trustworthy inside it, and :class:`UniV3State`
raises ``Unsupported`` rather than extrapolating past it.

Phase 3 (:meth:`dependent_requests`, second pass) - one read per initialized
tick found in those words, or (when explicitly enabled) one TickLens read per
populated word::

    ticks(int24)   0xf30dba93 -> (uint128 liquidityGross,int128 liquidityNet,...)

The third pass returns ``[]``, which is the acquirer's stop condition.

The centre word cannot be known before ``slot0`` is read, so the bitmap reads
are genuinely dependent.  A warm re-run can collapse phase 1 and 2 into a single
Multicall by putting the previously observed tick into
``PoolRecord.config["tick_hint"]``; :meth:`read_requests` then emits the window
immediately and :meth:`dependent_requests` re-centres it only if the hint was
wrong.

Selectors are recomputed from their signatures at import time and asserted
against the literals above, so a typo cannot survive.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from ...core.protocols import Snapshot, Unsupported
from ...core.types import BlockRef, CallSpec, PoolRecord, Token, norm_address
from . import math as m
from .state import UniV3State


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_SLOT0 = _selector("slot0()")
SEL_LIQUIDITY = _selector("liquidity()")
SEL_FEE = _selector("fee()")
SEL_TICK_SPACING = _selector("tickSpacing()")
SEL_TOKEN0 = _selector("token0()")
SEL_TOKEN1 = _selector("token1()")
SEL_TICK_BITMAP = _selector("tickBitmap(int16)")
SEL_TICKS = _selector("ticks(int24)")
SEL_TICK_LENS = _selector("getPopulatedTicksInWord(address,int16)")

# Documented, self-checked selector table (see module docstring).
assert (SEL_SLOT0, SEL_LIQUIDITY, SEL_FEE, SEL_TICK_SPACING, SEL_TOKEN0, SEL_TOKEN1,
        SEL_TICK_BITMAP, SEL_TICKS, SEL_TICK_LENS) == (
    "0x3850c7bd", "0x1a686502", "0xddca3f43", "0xd0c93a7c",
    "0x0dfe1681", "0xd21220a7", "0x5339c296", "0xf30dba93", "0x351fb478",
), "Uniswap V3 selector table drifted"

SLOT0_TYPES = ["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"]
TICKS_TYPES = [
    "uint128",  # liquidityGross
    "int128",  # liquidityNet
    "uint256",  # feeGrowthOutside0X128
    "uint256",  # feeGrowthOutside1X128
    "int56",  # tickCumulativeOutside
    "uint160",  # secondsPerLiquidityOutsideX128
    "uint32",  # secondsOutside
    "bool",  # initialized
]

TAG_SLOT0 = "univ3:slot0"
TAG_LIQUIDITY = "univ3:liquidity"
TAG_FEE = "univ3:fee"
TAG_TICK_SPACING = "univ3:tickSpacing"
TAG_TOKEN0 = "univ3:token0"
TAG_TOKEN1 = "univ3:token1"

# Mainnet TickLens as listed by the Uniswap SDK deployment map.  21895170 is
# the earliest block observed by this project with code at this address; older
# historical pins deliberately retain the individual pool reads.
TICK_LENS_MAINNET = "0xbfd8137f7d1516d3ea5ca83523914859ec47f573"
TICK_LENS_MAINNET_OBSERVED_FROM_BLOCK = 21_895_170


def tag_bitmap(word_pos: int) -> str:
    return f"univ3:tickBitmap:{word_pos}"


def tag_tick(tick: int) -> str:
    return f"univ3:ticks:{tick}"


def tag_tick_lens(word_pos: int) -> str:
    return f"univ3:tickLens:{word_pos}"


DEFAULT_WORD_RADIUS = 8


class UniswapV3Adapter:
    """Implements ``core.protocols.SourceAdapter`` for Uniswap V3."""

    family = "uniswap_v3"

    def __init__(self, word_radius: int = DEFAULT_WORD_RADIUS, bulk_ticks: bool = False) -> None:
        if word_radius < 0:
            raise ValueError("word_radius must be >= 0")
        self.word_radius = word_radius
        self.bulk_ticks = bulk_ticks

    # ------------------------------------------------------------- call specs

    def _pool_address(self, pool: PoolRecord) -> str:
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

    def tick_lens_spec(self, pool: PoolRecord, word_pos: int) -> CallSpec:
        data = SEL_TICK_LENS + abi_encode(["address", "int16"], [self._pool_address(pool), word_pos]).hex()
        return CallSpec(to=TICK_LENS_MAINNET, data=data, tag=tag_tick_lens(word_pos))

    def _uses_tick_lens(self, block: BlockRef) -> bool:
        return self.bulk_ticks and block.chain == 1 and block.number >= TICK_LENS_MAINNET_OBSERVED_FROM_BLOCK

    def window_words(self, tick: int, tick_spacing: int) -> range:
        """Inclusive-exclusive word range that ``read/dependent_requests`` cover."""
        centre = m.word_pos_for_tick(tick, tick_spacing)
        return range(centre - self.word_radius, centre + self.word_radius + 1)

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        specs = self._static_specs(pool)
        hint = pool.config.get("tick_hint")
        spacing = pool.config.get("tick_spacing") or pool.config.get("tickSpacing")
        if hint is not None and spacing:
            specs += [
                self.bitmap_spec(pool, w) for w in self.window_words(int(hint), int(spacing))
            ]
        return specs

    def prefetch_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        """Requests for the prior snapshot's current window, including cached calls.

        A collector can use this to seed the next block without retaining an old
        tick hint or an old window.  Missing prior bitmap data leaves that word's
        dependent TickLens call to the normal acquisition loop.
        """
        specs = self._static_specs(pool)
        slot0_raw = self._decoded(snapshot, CallSpec(self._pool_address(pool), SEL_SLOT0, TAG_SLOT0))
        spacing_raw = self._decoded(
            snapshot, CallSpec(self._pool_address(pool), SEL_TICK_SPACING, TAG_TICK_SPACING)
        )
        if slot0_raw is None or spacing_raw is None:
            return specs
        tick = abi_decode(SLOT0_TYPES, slot0_raw)[1]
        tick_spacing = abi_decode(["int24"], spacing_raw)[0]
        words = list(self.window_words(tick, tick_spacing))
        specs.extend(self.bitmap_spec(pool, word) for word in words)
        if self._uses_tick_lens(block):
            for word in words:
                raw = self._decoded(snapshot, self.bitmap_spec(pool, word))
                if raw is not None and abi_decode(["uint256"], raw)[0]:
                    specs.append(self.tick_lens_spec(pool, word))
        return specs

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        slot0 = self._decoded(snapshot, CallSpec(self._pool_address(pool), SEL_SLOT0, TAG_SLOT0))
        spacing_raw = self._decoded(
            snapshot, CallSpec(self._pool_address(pool), SEL_TICK_SPACING, TAG_TICK_SPACING)
        )
        if slot0 is None or spacing_raw is None:
            return []  # phase 1 has not landed yet
        tick = abi_decode(SLOT0_TYPES, slot0)[1]
        tick_spacing = abi_decode(["int24"], spacing_raw)[0]

        words = list(self.window_words(tick, tick_spacing))
        missing = [self.bitmap_spec(pool, w) for w in words if not snapshot.has(
            self.bitmap_spec(pool, w))]
        if missing:
            return missing

        wanted: list[CallSpec] = []
        for word_pos in words:
            raw = self._decoded(snapshot, self.bitmap_spec(pool, word_pos))
            if raw is None:
                raise Unsupported(
                    f"failed tickBitmap word {word_pos} for {pool.pool_id} "
                    f"at {snapshot.block.number}"
                )
            word = abi_decode(["uint256"], raw)[0]
            ticks = tuple(_ticks_in_word(word_pos, word, tick_spacing))
            if self._uses_tick_lens(block) and not self._has_individual_ticks(pool, snapshot, ticks):
                spec = self.tick_lens_spec(pool, word_pos)
                if not snapshot.has(spec):
                    wanted.append(spec)
                continue
            for tick_value in ticks:
                spec = self.tick_spec(pool, tick_value)
                if not snapshot.has(spec):
                    wanted.append(spec)
        return wanted

    # ------------------------------------------------------------ state build

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> UniV3State:
        to = self._pool_address(pool)

        def need(spec: CallSpec, what: str) -> bytes:
            raw = self._decoded(snapshot, spec)
            if raw is None:
                raise Unsupported(f"missing {what} for {pool.pool_id} at {snapshot.block.number}")
            return raw

        slot0 = abi_decode(SLOT0_TYPES, need(CallSpec(to, SEL_SLOT0, TAG_SLOT0), "slot0()"))
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

        token0 = _match_token(pool, token0_addr, snapshot.block.chain)
        token1 = _match_token(pool, token1_addr, snapshot.block.chain)

        sqrt_price_x96, tick = slot0[0], slot0[1]

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
            lens_raw = self._decoded(snapshot, self.tick_lens_spec(pool, word_pos))
            if lens_raw is not None:
                liquidity_net.update(_decode_tick_lens_word(lens_raw, ticks, word_pos))
                continue
            for tick_value in ticks:
                raw = self._decoded(snapshot, self.tick_spec(pool, tick_value))
                if raw is None:
                    raise Unsupported(
                        f"missing tick data for initialized tick {tick_value} in word {word_pos} "
                        f"for {pool.pool_id} at {snapshot.block.number}"
                    )
                liquidity_net[tick_value] = abi_decode(TICKS_TYPES, raw)[1]

        return UniV3State(
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
            word_lo=word_lo,
            word_hi=word_hi,
        )

    def provenance(self, pool: PoolRecord, snapshot: Snapshot) -> Mapping[str, Any]:
        """Window actually loaded - belongs in the snapshot's provenance record."""
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
        }

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _decoded(snapshot: Snapshot, spec: CallSpec) -> bytes | None:
        if not snapshot.has(spec):
            return None
        result = snapshot.get(spec)
        if not result.success:
            return None
        return bytes.fromhex(result.raw[2:])

    def _has_individual_ticks(
        self, pool: PoolRecord, snapshot: Snapshot, ticks: Iterable[int]
    ) -> bool:
        return all(snapshot.has(self.tick_spec(pool, tick)) for tick in ticks)


def _ticks_in_word(word_pos: int, word: int, tick_spacing: int) -> Iterable[int]:
    if word == 0:
        return ()
    return tuple(
        ((word_pos << 8) + bit) * tick_spacing for bit in range(256) if (word >> bit) & 1
    )


def _decode_tick_lens_word(raw: bytes, expected_ticks: tuple[int, ...], word_pos: int) -> dict[int, int]:
    """Decode and cross-check a TickLens word against the pool's bitmap."""
    try:
        rows = abi_decode(["(int24,int128,uint128)[]"], raw)[0]
    except Exception as exc:
        raise Unsupported(f"invalid TickLens response for word {word_pos}") from exc
    nets = {int(tick): int(liquidity_net) for tick, liquidity_net, _gross in rows}
    if len(nets) != len(rows) or set(nets) != set(expected_ticks):
        raise Unsupported(f"incomplete TickLens response for word {word_pos}")
    return nets


def _contiguous_range(bitmap: Mapping[int, int], centre: int) -> tuple[int, int]:
    """Largest contiguous word range around ``centre`` present in ``bitmap``."""
    if centre not in bitmap:
        raise Unsupported(f"tickBitmap word {centre} (the current word) was not loaded")
    lo = hi = centre
    while lo - 1 in bitmap:
        lo -= 1
    while hi + 1 in bitmap:
        hi += 1
    return lo, hi


def _match_token(pool: PoolRecord, address: str, chain: int) -> Token:
    for token in pool.tokens:
        if token.address == address:
            return token
    raise Unsupported(
        f"pool {pool.pool_id} reports token {address} which is not in the discovered "
        f"record ({[t.address for t in pool.tokens]})"
    )
