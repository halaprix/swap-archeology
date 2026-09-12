"""Immutable, bounded V4 state for hookless static-fee pools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ...core.protocols import Unsupported
from ...core.types import Address, PoolRecord, Token, norm_address
from ..uniswap_v3 import math as v3
from . import math as m

GAS_ESTIMATE = 120_000


def _freeze(values: Mapping[int, int]) -> Mapping[int, int]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class UniV4State:
    record: PoolRecord
    sqrt_price_x96: int
    tick: int
    liquidity: int
    protocol_fee: int
    lp_fee: int
    tick_spacing: int
    token0: Token
    token1: Token
    tick_bitmap: Mapping[int, int] = field(default_factory=dict)
    tick_liquidity_net: Mapping[int, int] = field(default_factory=dict)
    word_lo: int = 0
    word_hi: int = 0
    gas: int = GAS_ESTIMATE

    def __post_init__(self) -> None:
        missing = set(range(self.word_lo, self.word_hi + 1)) - self.tick_bitmap.keys()
        if missing:
            raise Unsupported(f"incomplete V4 bitmap coverage: missing words {sorted(missing)}")
        object.__setattr__(self, "tick_bitmap", _freeze(self.tick_bitmap))
        object.__setattr__(self, "tick_liquidity_net", _freeze(self.tick_liquidity_net))

    def tokens(self) -> tuple[Token, ...]:
        return self.token0, self.token1

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return self.gas

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        return self.swap(token_in, token_out, amount_in)[0]

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, UniV4State]:
        zero_for_one = self._direction(token_in, token_out)
        if amount_in < 0:
            raise Unsupported(f"negative exact input {amount_in}")
        if amount_in > v3.UINT128_MAX:
            raise Unsupported(f"V4 exact input {amount_in} exceeds uint128 quote domain")
        if amount_in == 0:
            return 0, self
        amount0, amount1, state = self._swap(zero_for_one, amount_in)
        return -(amount1 if zero_for_one else amount0), state

    def _direction(self, token_in: Address, token_out: Address) -> bool:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if (token_in, token_out) == (self.token0.address, self.token1.address):
            return True
        if (token_in, token_out) == (self.token1.address, self.token0.address):
            return False
        raise Unsupported(f"pair {token_in}->{token_out} is not {self.record.pool_id}")

    def _word(self, word: int) -> int:
        if not self.word_lo <= word <= self.word_hi:
            raise Unsupported(f"insufficient V4 tick coverage: needed word {word}, loaded [{self.word_lo},{self.word_hi}]")
        return self.tick_bitmap[word]

    def _next_tick(self, tick: int, lte: bool) -> tuple[int, bool]:
        compressed = v3.compress(tick, self.tick_spacing)
        word = v3.position(compressed if lte else compressed + 1)[0]
        return v3.next_initialized_tick_within_one_word({word: self._word(word)}, tick, self.tick_spacing, lte)

    def _net(self, tick: int) -> int:
        try:
            return self.tick_liquidity_net[tick]
        except KeyError:
            raise Unsupported(f"initialized V4 tick {tick} has no loaded liquidityNet") from None

    def _swap(self, zero_for_one: bool, specified: int) -> tuple[int, int, UniV4State]:
        limit = v3.MIN_SQRT_RATIO + 1 if zero_for_one else v3.MAX_SQRT_RATIO - 1
        if (zero_for_one and limit >= self.sqrt_price_x96) or (not zero_for_one and limit <= self.sqrt_price_x96):
            raise Unsupported("V4 pool price is at its protocol boundary")
        remaining, calculated = specified, 0
        sqrt_price, tick, liquidity = self.sqrt_price_x96, self.tick, self.liquidity
        fee = m.swap_fee(self.protocol_fee, self.lp_fee, zero_for_one)
        for _ in range(100_000):
            if remaining == 0 or sqrt_price == limit:
                break
            start = sqrt_price
            tick_next, initialized = self._next_tick(tick, zero_for_one)
            tick_next = max(v3.MIN_TICK, min(v3.MAX_TICK, tick_next))
            next_sqrt = v3.get_sqrt_ratio_at_tick(tick_next)
            target = max(next_sqrt, limit) if zero_for_one else min(next_sqrt, limit)
            sqrt_price, amount_in, amount_out, fee_amount = m.compute_swap_step(
                sqrt_price, target, liquidity, remaining, fee
            )
            remaining -= amount_in + fee_amount
            calculated += amount_out
            if sqrt_price == next_sqrt:
                if initialized:
                    net = self._net(tick_next)
                    liquidity = v3.add_delta(liquidity, -net if zero_for_one else net)
                tick = tick_next - 1 if zero_for_one else tick_next
            elif sqrt_price != start:
                tick = v3.get_tick_at_sqrt_ratio(sqrt_price)
        else:  # pragma: no cover - defensive bound
            raise Unsupported("V4 swap walk did not terminate")
        if remaining:
            raise Unsupported(f"V4 pool exhausted with {remaining} of {specified} input unswapped")
        amount0, amount1 = (specified, -calculated) if zero_for_one else (-calculated, specified)
        return amount0, amount1, UniV4State(
            self.record, sqrt_price, tick, liquidity, self.protocol_fee, self.lp_fee,
            self.tick_spacing, self.token0, self.token1, self.tick_bitmap,
            self.tick_liquidity_net, self.word_lo, self.word_hi, self.gas,
        )
