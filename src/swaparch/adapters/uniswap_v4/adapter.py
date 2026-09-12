"""Snapshot reads and state construction for Uniswap V4's singleton manager.

Only hookless, static-fee pools are admitted.  V4 pools have no pool contract:
every read targets StateView and uses the immutable PoolKey-derived PoolId.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from ...core.protocols import Snapshot, Unsupported
from ...core.types import NATIVE_ETH, BlockRef, CallSpec, PoolRecord, Token, norm_address
from ..uniswap_v3 import math as v3
from . import math as m
from .state import UniV4State

STATE_VIEW = "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
ZERO_HOOK = "0x0000000000000000000000000000000000000000"
DEFAULT_WORD_RADIUS = 8


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_SLOT0 = _selector("getSlot0(bytes32)")
SEL_LIQUIDITY = _selector("getLiquidity(bytes32)")
SEL_TICK_BITMAP = _selector("getTickBitmap(bytes32,int16)")
SEL_TICK_LIQUIDITY = _selector("getTickLiquidity(bytes32,int24)")
SEL_EXTSLOAD = _selector("extsload(bytes32[])")
assert (SEL_SLOT0, SEL_LIQUIDITY, SEL_TICK_BITMAP, SEL_TICK_LIQUIDITY, SEL_EXTSLOAD) == (
    "0xc815641c", "0xfa6793d5", "0x1c7ccb4c", "0xcaedab54",
    "0xdbd035ff",
), "StateView selector table drifted"

TAG_SLOT0 = "univ4:slot0"
TAG_LIQUIDITY = "univ4:liquidity"


def tag_bitmap(word: int) -> str:
    return f"univ4:tickBitmap:{word}"


def tag_tick(tick: int) -> str:
    return f"univ4:tickLiquidity:{tick}"


TAG_BULK_TICKS = "univ4:tickLiquidityBulk"


def tick_storage_slot(pool_id: bytes, tick: int) -> bytes:
    """`StateLibrary._getTickInfoSlot` for PoolManager's sparse `extsload`."""
    state_slot = keccak(pool_id + (6).to_bytes(32, "big"))
    return keccak(tick.to_bytes(32, "big", signed=True) + (int.from_bytes(state_slot, "big") + 4).to_bytes(32, "big"))


def pool_key_id(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    """``PoolIdLibrary.toId``: keccak256(abi.encode(PoolKey))."""
    raw = abi_encode(
        ["address", "address", "uint24", "int24", "address"],
        [norm_address(currency0), norm_address(currency1), fee, tick_spacing, norm_address(hooks)],
    )
    return "0x" + keccak(raw).hex()


class UniswapV4Adapter:
    family = "uniswap_v4"

    def __init__(self, word_radius: int = DEFAULT_WORD_RADIUS, bulk_ticks: bool = False) -> None:
        if word_radius < 0:
            raise ValueError("word_radius must be >= 0")
        self.word_radius = word_radius
        self.bulk_ticks = bulk_ticks

    def _key(self, pool: PoolRecord) -> tuple[str, str, int, int, str]:
        config = pool.config
        try:
            currency0 = norm_address(str(config["currency0"]))
            currency1 = norm_address(str(config["currency1"]))
            fee, spacing = int(config["fee"]), int(config["tick_spacing"])
            hooks = norm_address(str(config["hooks"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise Unsupported(f"V4 pool {pool.pool_id} lacks a complete PoolKey") from exc
        if currency0 >= currency1:
            raise Unsupported(f"V4 PoolKey currencies are not numerically sorted for {pool.pool_id}")
        if not 0 < spacing <= 32_767:
            raise Unsupported(f"invalid V4 tick spacing {spacing}")
        if not 0 <= fee <= 0xFFFFFF:
            raise Unsupported(f"invalid V4 fee {fee}")
        return currency0, currency1, fee, spacing, hooks

    def _pool_id(self, pool: PoolRecord) -> str:
        key = self._key(pool)
        derived = pool_key_id(*key)
        observed = str(pool.pool).lower()
        if observed != derived:
            raise Unsupported(f"V4 PoolId mismatch for {pool.pool_id}: expected {derived}, got {observed}")
        return derived

    def _supported_key(self, pool: PoolRecord) -> tuple[str, str, int, int, str]:
        key = self._key(pool)
        if key[4] != ZERO_HOOK:
            raise Unsupported(f"V4 pool {pool.pool_id} has nonzero hook {key[4]}")
        if key[2] == m.DYNAMIC_FEE_FLAG:
            raise Unsupported(f"V4 pool {pool.pool_id} has a dynamic LP fee")
        if key[2] > m.PIPS:
            raise Unsupported(f"V4 pool {pool.pool_id} has invalid static LP fee {key[2]}")
        self._pool_id(pool)
        return key

    def _data(self, selector: str, types: list[str], values: list[Any]) -> str:
        return selector + abi_encode(types, values).hex()

    def _id_bytes(self, pool: PoolRecord) -> bytes:
        return bytes.fromhex(self._pool_id(pool)[2:])

    def slot0_spec(self, pool: PoolRecord) -> CallSpec:
        return CallSpec(STATE_VIEW, self._data(SEL_SLOT0, ["bytes32"], [self._id_bytes(pool)]), TAG_SLOT0)

    def liquidity_spec(self, pool: PoolRecord) -> CallSpec:
        return CallSpec(STATE_VIEW, self._data(SEL_LIQUIDITY, ["bytes32"], [self._id_bytes(pool)]), TAG_LIQUIDITY)

    def bitmap_spec(self, pool: PoolRecord, word: int) -> CallSpec:
        return CallSpec(STATE_VIEW, self._data(SEL_TICK_BITMAP, ["bytes32", "int16"], [self._id_bytes(pool), word]), tag_bitmap(word))

    def tick_spec(self, pool: PoolRecord, tick: int) -> CallSpec:
        return CallSpec(STATE_VIEW, self._data(SEL_TICK_LIQUIDITY, ["bytes32", "int24"], [self._id_bytes(pool), tick]), tag_tick(tick))

    def bulk_tick_spec(self, pool: PoolRecord, ticks: Iterable[int]) -> CallSpec:
        slots = [tick_storage_slot(self._id_bytes(pool), tick) for tick in ticks]
        return CallSpec(POOL_MANAGER, self._data(SEL_EXTSLOAD, ["bytes32[]"], [slots]), TAG_BULK_TICKS)

    def window_words(self, tick: int, spacing: int) -> range:
        centre = v3.word_pos_for_tick(tick, spacing)
        return range(centre - self.word_radius, centre + self.word_radius + 1)

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._supported_key(pool)
        specs = [self.slot0_spec(pool), self.liquidity_spec(pool)]
        hint, spacing = pool.config.get("tick_hint"), self._key(pool)[3]
        if hint is not None:
            specs.extend(self.bitmap_spec(pool, word) for word in self.window_words(int(hint), spacing))
        return specs

    def prefetch_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        """Current-window read plan, including cached specs, for the next block."""
        self._supported_key(pool)
        specs = [self.slot0_spec(pool), self.liquidity_spec(pool)]
        slot0_raw = self._decoded(snapshot, self.slot0_spec(pool))
        if slot0_raw is None:
            return specs
        try:
            tick = abi_decode(["uint160", "int24", "uint24", "uint24"], slot0_raw)[1]
        except Exception as exc:
            raise Unsupported(f"malformed V4 getSlot0 for {pool.pool_id}") from exc
        spacing = self._key(pool)[3]
        words = list(self.window_words(tick, spacing))
        bitmap_specs = [self.bitmap_spec(pool, word) for word in words]
        specs.extend(bitmap_specs)
        if any(self._decoded(snapshot, spec) is None for spec in bitmap_specs):
            return specs
        ticks = self._initialized_ticks(pool, words, spacing, snapshot)
        if self.bulk_ticks:
            return specs + [self.bulk_tick_spec(pool, ticks)]
        return specs + [self.tick_spec(pool, tick) for tick in ticks]

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        self._supported_key(pool)
        slot0_raw = self._decoded(snapshot, self.slot0_spec(pool))
        if slot0_raw is None:
            return []
        try:
            tick = abi_decode(["uint160", "int24", "uint24", "uint24"], slot0_raw)[1]
        except Exception as exc:
            raise Unsupported(f"malformed V4 getSlot0 for {pool.pool_id}") from exc
        spacing = self._key(pool)[3]
        words = list(self.window_words(tick, spacing))
        missing = [self.bitmap_spec(pool, word) for word in words if not snapshot.has(self.bitmap_spec(pool, word))]
        if missing:
            return missing
        ticks = self._initialized_ticks(pool, words, spacing, snapshot)
        if self.bulk_ticks:
            spec = self.bulk_tick_spec(pool, ticks)
            return [] if snapshot.has(spec) else [spec]
        return [spec for tick in ticks if not snapshot.has(spec := self.tick_spec(pool, tick))]

    def _initialized_ticks(self, pool: PoolRecord, words: Iterable[int], spacing: int, snapshot: Snapshot) -> list[int]:
        ticks: list[int] = []
        for word in words:
            raw = self._decoded(snapshot, self.bitmap_spec(pool, word))
            if raw is None:
                raise Unsupported(f"failed V4 tick bitmap word {word} for {pool.pool_id}")
            try:
                bitmap = abi_decode(["uint256"], raw)[0]
            except Exception as exc:
                raise Unsupported(f"malformed V4 tick bitmap word {word} for {pool.pool_id}") from exc
            ticks.extend(_ticks_in_word(word, bitmap, spacing))
        return ticks

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> UniV4State:
        currency0, currency1, configured_fee, spacing, _ = self._supported_key(pool)

        def need(spec: CallSpec, label: str) -> bytes:
            raw = self._decoded(snapshot, spec)
            if raw is None:
                raise Unsupported(f"missing V4 {label} for {pool.pool_id} at {snapshot.block.number}")
            return raw

        try:
            sqrt_price, tick, protocol_fee, lp_fee = abi_decode(
                ["uint160", "int24", "uint24", "uint24"], need(self.slot0_spec(pool), "getSlot0")
            )
        except Exception as exc:
            raise Unsupported(f"malformed V4 getSlot0 for {pool.pool_id}") from exc
        if not v3.MIN_TICK <= tick <= v3.MAX_TICK or not v3.MIN_SQRT_RATIO <= sqrt_price < v3.MAX_SQRT_RATIO:
            raise Unsupported(f"V4 getSlot0 is outside TickMath bounds for {pool.pool_id}")
        if lp_fee != configured_fee:
            raise Unsupported(f"V4 hookless static pool {pool.pool_id} reports lpFee {lp_fee}, expected {configured_fee}")
        try:
            liquidity = abi_decode(["uint128"], need(self.liquidity_spec(pool), "getLiquidity"))[0]
        except Exception as exc:
            raise Unsupported(f"malformed V4 getLiquidity for {pool.pool_id}") from exc
        token0, token1 = _match_token(pool, currency0), _match_token(pool, currency1)

        bitmap: dict[int, int] = {}
        for word in self.window_words(tick, spacing):
            raw = self._decoded(snapshot, self.bitmap_spec(pool, word))
            if raw is not None:
                try:
                    bitmap[word] = abi_decode(["uint256"], raw)[0]
                except Exception as exc:
                    raise Unsupported(f"malformed V4 tick bitmap word {word} for {pool.pool_id}") from exc
        if not bitmap:
            raise Unsupported(f"no V4 tick bitmap words loaded for {pool.pool_id}")
        lo, hi = _contiguous_range(bitmap, v3.word_pos_for_tick(tick, spacing))
        nets: dict[int, int] = {}
        ticks = [
            initialized_tick
            for word in range(lo, hi + 1)
            for initialized_tick in _ticks_in_word(word, bitmap[word], spacing)
        ]
        bulk_raw = self._decoded(snapshot, self.bulk_tick_spec(pool, ticks)) if self.bulk_ticks else None
        if self.bulk_ticks and bulk_raw is not None:
            try:
                words = abi_decode(["bytes32[]"], bulk_raw)[0]
            except Exception as exc:
                raise Unsupported(f"malformed V4 PoolManager extsload for {pool.pool_id}") from exc
            if len(words) != len(ticks):
                raise Unsupported(f"V4 PoolManager extsload length mismatch for {pool.pool_id}")
            for initialized_tick, value in zip(ticks, words, strict=True):
                nets[initialized_tick] = int.from_bytes(value, "big", signed=False) >> 128
                if nets[initialized_tick] >= 1 << 127:
                    nets[initialized_tick] -= 1 << 128
        elif self.bulk_ticks:
            raise Unsupported(f"missing V4 PoolManager extsload for {pool.pool_id} at {snapshot.block.number}")
        else:
            for initialized_tick in ticks:
                raw = self._decoded(snapshot, self.tick_spec(pool, initialized_tick))
                if raw is not None:
                    try:
                        nets[initialized_tick] = abi_decode(["uint128", "int128"], raw)[1]
                    except Exception as exc:
                        raise Unsupported(f"malformed V4 tick {initialized_tick} for {pool.pool_id}") from exc
        return UniV4State(pool, sqrt_price, tick, liquidity, protocol_fee, lp_fee, spacing, token0, token1,
                          {word: bitmap[word] for word in range(lo, hi + 1)}, nets, lo, hi)

    def provenance(self, pool: PoolRecord, snapshot: Snapshot) -> Mapping[str, Any]:
        state = self.load_state(pool, snapshot)
        return {"family": self.family, "pool": pool.pool, "block": snapshot.block.number,
                "block_hash": snapshot.block.hash, "state_view": STATE_VIEW,
                "words_loaded": [state.word_lo, state.word_hi], "tick_spacing": state.tick_spacing,
                "protocol_fee": state.protocol_fee, "lp_fee": state.lp_fee,
                "initialized_ticks_loaded": len(state.tick_liquidity_net)}

    @staticmethod
    def _decoded(snapshot: Snapshot, spec: CallSpec) -> bytes | None:
        if not snapshot.has(spec):
            return None
        result = snapshot.get(spec)
        if not result.success:
            return None
        try:
            return bytes.fromhex(result.raw[2:])
        except ValueError as exc:
            raise Unsupported(f"malformed hexadecimal StateView response for {spec.tag}") from exc


def _ticks_in_word(word: int, bitmap: int, spacing: int) -> Iterable[int]:
    return tuple(((word << 8) + bit) * spacing for bit in range(256) if (bitmap >> bit) & 1)


def _contiguous_range(bitmap: Mapping[int, int], centre: int) -> tuple[int, int]:
    if centre not in bitmap:
        raise Unsupported(f"V4 current bitmap word {centre} was not loaded")
    lo = hi = centre
    while lo - 1 in bitmap:
        lo -= 1
    while hi + 1 in bitmap:
        hi += 1
    return lo, hi


def _match_token(pool: PoolRecord, currency: str) -> Token:
    for token in pool.tokens:
        if token.address == currency:
            return token
    kind = "native ETH" if currency == NATIVE_ETH else currency
    raise Unsupported(f"V4 PoolKey currency {kind} is absent from discovered record {pool.pool_id}")
