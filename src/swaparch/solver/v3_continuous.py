"""Continuous, loaded-domain support for one immutable Uniswap V3 state.

The model follows the adapter's bitmap walk exactly as far as its loaded words
permit.  It is a numerical candidate guide: a continuous amount is never an
executable quote and coverage ending at a loaded-word edge is not global pool
containment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from swaparch.adapters.uniswap_v3 import math as m
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import Unsupported

_Q96 = Decimal(1 << 96)
_ONE_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class LoadedInterval:
    """One non-zero-liquidity price interval from the current state outward."""

    start_sqrt_x96: int
    end_sqrt_x96: int
    liquidity: int
    boundary_tick: int
    initialized_boundary: bool


@dataclass(frozen=True)
class ContinuousSupport:
    """A price-optimal point within the locally loaded V3 domain."""

    gross_input: Decimal
    output: Decimal
    objective: Decimal
    end_sqrt_x96: int
    boundary_tick: int
    stop: str


@dataclass(frozen=True)
class LoadedDomain:
    intervals: tuple[LoadedInterval, ...]
    stop: str


def loaded_domain(state: UniV3State, zero_for_one: bool) -> LoadedDomain:
    """Return contiguous intervals proven by the loaded bitmap words.

    This deliberately stops before the first unloaded bitmap word. Zero-L
    spans move price to a *known initialized tick* at zero input/output, as
    `UniswapV3Pool.swap` does, then apply that tick's liquidity-net update.
    """

    sqrt_price = state.sqrt_price_x96
    tick = state.tick
    liquidity = state.liquidity
    intervals: list[LoadedInterval] = []
    for _ in range(100_000):
        limit = m.MIN_SQRT_RATIO + 1 if zero_for_one else m.MAX_SQRT_RATIO - 1
        if sqrt_price == limit:
            return LoadedDomain(tuple(intervals), "protocol_price_limit")
        try:
            tick_next, initialized = state._next_initialized_tick(tick, zero_for_one)
        except Unsupported as exc:
            return LoadedDomain(tuple(intervals), f"loaded_domain_edge: {exc}")
        tick_next = max(m.MIN_TICK, min(m.MAX_TICK, tick_next))
        sqrt_next = m.get_sqrt_ratio_at_tick(tick_next)
        clipped = (zero_for_one and sqrt_next < limit) or (not zero_for_one and sqrt_next > limit)
        target = limit if clipped else sqrt_next

        # At an initialized boundary the adapter crosses without consuming
        # input, then applies liquidityNet with the direction-specific sign.
        if target != sqrt_price:
            if (zero_for_one and target > sqrt_price) or (not zero_for_one and target < sqrt_price):
                return LoadedDomain(tuple(intervals), "invalid_bitmap_direction")
            if liquidity > 0:
                intervals.append(LoadedInterval(sqrt_price, target, liquidity, tick_next, initialized))
            sqrt_price = target

        # The unconstrained router's limit is inside the TickMath endpoints.
        # Do not cross a tick that lies past it.
        if clipped:
            return LoadedDomain(tuple(intervals), "protocol_price_limit")

        if initialized:
            try:
                liquidity_net = state._liquidity_net(tick_next)
                liquidity = m.add_delta(liquidity, -liquidity_net if zero_for_one else liquidity_net)
            except Unsupported as exc:
                return LoadedDomain(tuple(intervals), f"loaded_domain_edge: {exc}")
        tick = tick_next - 1 if zero_for_one else tick_next
    return LoadedDomain(tuple(intervals), "walk_limit")


def price_optimal_support(
    state: UniV3State,
    zero_for_one: bool,
    input_value_per_raw: Decimal,
    output_value_per_raw: Decimal,
    domain: LoadedDomain | None = None,
) -> tuple[ContinuousSupport | None, LoadedDomain]:
    """Find the local price-optimal exact-input direction using loaded ticks.

    The fee is applied to each interval's net input: gross input is
    ``net / gamma`` where ``gamma = (1e6 - fee) / 1e6``.  The price stationary
    point is evaluated with ``Decimal`` and never converted through float.
    """

    domain = domain or loaded_domain(state, zero_for_one)
    with localcontext() as context:
        context.prec = 96
        if input_value_per_raw <= 0 or output_value_per_raw <= 0:
            return None, domain
        gamma = (_ONE_MILLION - Decimal(state.fee)) / _ONE_MILLION
        if gamma <= 0:
            return None, domain

        # The unique marginal-equality price is independent of L. Traversing
        # in price order therefore stops in the interval containing it, or at
        # the last loaded boundary before it.
        if zero_for_one:
            stationary = (input_value_per_raw / (gamma * output_value_per_raw)).sqrt()
        else:
            stationary = (gamma * output_value_per_raw / input_value_per_raw).sqrt()

        cumulative_net = Decimal(0)
        cumulative_output = Decimal(0)
        best: ContinuousSupport | None = None

        for interval in domain.intervals:
            start = Decimal(interval.start_sqrt_x96) / _Q96
            end = Decimal(interval.end_sqrt_x96) / _Q96
            liquidity = Decimal(interval.liquidity)
            lower, upper = (end, start) if zero_for_one else (start, end)
            candidate_price = min(upper, max(lower, stationary))

            if zero_for_one:
                net = liquidity * (Decimal(1) / candidate_price - Decimal(1) / start)
                output = liquidity * (start - candidate_price)
            else:
                net = liquidity * (candidate_price - start)
                output = liquidity * (Decimal(1) / start - Decimal(1) / candidate_price)
            gross = (cumulative_net + net) / gamma
            total_output = cumulative_output + output
            objective = total_output * output_value_per_raw - gross * input_value_per_raw
            if gross > 0 and objective > 0:
                stop = "marginal_equality" if lower <= stationary <= upper else (
                    f"loaded_boundary_tick:{interval.boundary_tick}"
                )
                candidate = ContinuousSupport(
                    gross, total_output, objective,
                    int(candidate_price * _Q96), interval.boundary_tick, stop,
                )
                if best is None or candidate.objective > best.objective:
                    best = candidate

            if lower <= stationary <= upper:
                break
            if zero_for_one:
                interval_net = liquidity * (Decimal(1) / end - Decimal(1) / start)
                interval_output = liquidity * (start - end)
            else:
                interval_net = liquidity * (end - start)
                interval_output = liquidity * (Decimal(1) / start - Decimal(1) / end)
            cumulative_net += interval_net
            cumulative_output += interval_output

            if (zero_for_one and stationary > end) or (not zero_for_one and stationary < end):
                break
        return best, domain
