"""Immutable per-block Uniswap V3 pool state with an exact ``Pool.swap`` walk.

``UniV3State`` satisfies ``core.protocols.PoolState``.  Its :meth:`quote_exact_in`
and :meth:`swap` reproduce ``UniswapV3Pool.swap`` step for step against locally
loaded state: the same tick-bitmap traversal, the same per-step
``SwapMath.computeSwapStep`` (so the fee is charged per step, not once on the
total), the same ``LiquidityMath.addDelta`` on every crossed initialized tick and
the same ``MIN_SQRT_RATIO + 1`` / ``MAX_SQRT_RATIO - 1`` price limit that
``SwapRouter``/``SwapRouter02`` pass for an unconstrained single-hop swap.

Tick coverage is finite and explicit.  Only the bitmap words in
``[word_lo, word_hi]`` are loaded, so a walk that would need a word outside that
range raises ``Unsupported`` instead of silently treating unloaded liquidity as
empty.  The same applies to an initialized tick whose ``liquidityNet`` was not
fetched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ...core.protocols import Unsupported
from ...core.types import Address, PoolRecord, Token, norm_address
from . import math as m

#: Constant, documented gas estimate for one V3 single-hop exact-input swap.
#: This is *not* a measured per-trade number: a real swap's gas depends on how
#: many initialized ticks it crosses (~20k for the pool call plus roughly 20k
#: per crossed tick, plus the router and token transfers).  It is a flat label
#: for gas-aware route ranking, never a settlement claim.
GAS_ESTIMATE = 120_000


def _freeze(d: Mapping[int, int]) -> Mapping[int, int]:
    return MappingProxyType(dict(d))


@dataclass(frozen=True)
class UniV3State:
    """One Uniswap V3 pool, pinned to one block, quoting purely in memory."""

    record: PoolRecord
    sqrt_price_x96: int
    tick: int
    liquidity: int
    fee: int  # fee in hundredths of a bip (pips), e.g. 500 = 0.05%
    tick_spacing: int
    token0: Token
    token1: Token
    #: wordPos -> uint256 bitmap word, for every word in ``[word_lo, word_hi]``
    tick_bitmap: Mapping[int, int] = field(default_factory=dict)
    #: tick -> liquidityNet (int128), for every tick set in the loaded words
    tick_liquidity_net: Mapping[int, int] = field(default_factory=dict)
    word_lo: int = 0
    word_hi: int = 0
    gas: int = GAS_ESTIMATE

    def __post_init__(self) -> None:
        missing_words = set(range(self.word_lo, self.word_hi + 1)) - self.tick_bitmap.keys()
        if missing_words:
            raise Unsupported(
                f"incomplete tickBitmap coverage: missing words {sorted(missing_words)} "
                f"in advertised range [{self.word_lo},{self.word_hi}]"
            )
        object.__setattr__(self, "tick_bitmap", _freeze(self.tick_bitmap))
        object.__setattr__(self, "tick_liquidity_net", _freeze(self.tick_liquidity_net))

    # ---------------------------------------------------------------- protocol

    def tokens(self) -> tuple[Token, ...]:
        return (self.token0, self.token1)

    def capacity_ids(self) -> tuple[str, ...]:
        """A V3 pool shares nothing with any other venue; its own id is the only
        capacity it draws on."""
        return (self.record.pool_id,)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)  # validates the pair
        return self.gas

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, UniV3State]:
        zero_for_one = self._direction(token_in, token_out)
        if amount_in < 0:
            raise Unsupported(f"negative exact input {amount_in}")
        if amount_in == 0:
            return 0, self
        amount0, amount1, new = self._swap(zero_for_one, amount_in)
        amount_out = -(amount1 if zero_for_one else amount0)
        return amount_out, new

    # ------------------------------------------------------------- internals

    def _direction(self, token_in: Address, token_out: Address) -> bool:
        a_in = norm_address(token_in)
        a_out = norm_address(token_out)
        t0, t1 = self.token0.address, self.token1.address
        if a_in == t0 and a_out == t1:
            return True
        if a_in == t1 and a_out == t0:
            return False
        raise Unsupported(
            f"pair {a_in}->{a_out} is not this pool's pair ({t0}/{t1}) "
            f"for {self.record.pool_id}"
        )

    def _word(self, word_pos: int) -> int:
        if not (self.word_lo <= word_pos <= self.word_hi):
            raise Unsupported(
                f"insufficient tick coverage: needed word {word_pos}, "
                f"loaded [{self.word_lo},{self.word_hi}]"
            )
        return self.tick_bitmap[word_pos]

    def _next_initialized_tick(self, tick: int, lte: bool) -> tuple[int, bool]:
        compressed = m.compress(tick, self.tick_spacing)
        word_pos = m.position(compressed if lte else compressed + 1)[0]
        word = self._word(word_pos)
        return m.next_initialized_tick_within_one_word(
            {word_pos: word}, tick, self.tick_spacing, lte
        )

    def _liquidity_net(self, tick: int) -> int:
        try:
            return self.tick_liquidity_net[tick]
        except KeyError:
            raise Unsupported(
                f"insufficient tick coverage: initialized tick {tick} has no loaded "
                f"liquidityNet (loaded words [{self.word_lo},{self.word_hi}])"
            ) from None

    def _swap(self, zero_for_one: bool, amount_specified: int) -> tuple[int, int, UniV3State]:
        """Port of ``UniswapV3Pool.swap`` for an unconstrained exact-input swap.

        Returns ``(amount0, amount1, new_state)`` with the pool's own sign
        convention: positive = paid to the pool, negative = paid out by it.
        """
        sqrt_price_limit_x96 = (
            m.MIN_SQRT_RATIO + 1 if zero_for_one else m.MAX_SQRT_RATIO - 1
        )
        if zero_for_one:
            if not (sqrt_price_limit_x96 < self.sqrt_price_x96):
                raise Unsupported("SPL: pool price already at the minimum sqrt ratio")
        else:
            if not (sqrt_price_limit_x96 > self.sqrt_price_x96):
                raise Unsupported("SPL: pool price already at the maximum sqrt ratio")

        exact_input = amount_specified > 0
        remaining = amount_specified
        calculated = 0
        sqrt_price = self.sqrt_price_x96
        tick = self.tick
        liquidity = self.liquidity

        steps = 0
        while remaining != 0 and sqrt_price != sqrt_price_limit_x96:
            steps += 1
            if steps > 100_000:  # pragma: no cover - defensive, the walk is bounded
                raise Unsupported("swap walk did not terminate")
            step_start = sqrt_price
            tick_next, initialized = self._next_initialized_tick(tick, zero_for_one)
            if tick_next < m.MIN_TICK:
                tick_next = m.MIN_TICK
            elif tick_next > m.MAX_TICK:
                tick_next = m.MAX_TICK
            sqrt_price_next = m.get_sqrt_ratio_at_tick(tick_next)

            if zero_for_one:
                beyond = sqrt_price_next < sqrt_price_limit_x96
            else:
                beyond = sqrt_price_next > sqrt_price_limit_x96
            target = sqrt_price_limit_x96 if beyond else sqrt_price_next

            sqrt_price, amount_in, amount_out, fee_amount = m.compute_swap_step(
                sqrt_price, target, liquidity, remaining, self.fee
            )

            if exact_input:
                remaining -= amount_in + fee_amount
                calculated -= amount_out
            else:  # pragma: no cover - exact-output is not exposed yet
                remaining += amount_out
                calculated += amount_in + fee_amount

            if sqrt_price == sqrt_price_next:
                if initialized:
                    liquidity_net = self._liquidity_net(tick_next)
                    if zero_for_one:
                        liquidity_net = -liquidity_net
                    liquidity = m.add_delta(liquidity, liquidity_net)
                tick = tick_next - 1 if zero_for_one else tick_next
            elif sqrt_price != step_start:
                tick = m.get_tick_at_sqrt_ratio(sqrt_price)

        if remaining != 0:
            # The walk stopped on the MIN/MAX price limit with input left over.
            # A real swap would simply return a partial fill; for routing that is
            # a capacity limit we must not hide behind a number.
            raise Unsupported(
                f"pool {self.record.pool_id} exhausted at the protocol price limit with "
                f"{remaining} of {amount_specified} input unswapped"
            )

        if zero_for_one == exact_input:
            amount0 = amount_specified - remaining
            amount1 = calculated
        else:
            amount0 = calculated
            amount1 = amount_specified - remaining

        new = UniV3State(
            record=self.record,
            sqrt_price_x96=sqrt_price,
            tick=tick,
            liquidity=liquidity,
            fee=self.fee,
            tick_spacing=self.tick_spacing,
            token0=self.token0,
            token1=self.token1,
            tick_bitmap=self.tick_bitmap,
            tick_liquidity_net=self.tick_liquidity_net,
            word_lo=self.word_lo,
            word_hi=self.word_hi,
            gas=self.gas,
        )
        return amount0, amount1, new
