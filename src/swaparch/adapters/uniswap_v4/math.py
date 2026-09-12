"""V4-only fee and swap-step semantics.

The concentrated-liquidity, tick and sqrt-price helpers are imported from the
V3 port because v4-core keeps those algorithms unchanged.  ``compute_swap_step``
is deliberately separate: V4 represents exact input as a negative amount and
does not recompute ``amountIn`` after a partial step.
"""

from __future__ import annotations

from ..uniswap_v3 import math as v3

PIPS = 1_000_000
DYNAMIC_FEE_FLAG = 0x800000


def directional_protocol_fee(protocol_fee: int, zero_for_one: bool) -> int:
    """Return v4's packed directional protocol fee (low 12 bits for 0->1)."""
    return protocol_fee & 0xFFF if zero_for_one else protocol_fee >> 12


def swap_fee(protocol_fee: int, lp_fee: int, zero_for_one: bool) -> int:
    """``ProtocolFeeLibrary.calculateSwapFee`` for the swap direction."""
    protocol = directional_protocol_fee(protocol_fee, zero_for_one)
    if not 0 <= protocol <= 1000 or not 0 <= lp_fee <= PIPS:
        raise v3.EvmRevert("invalid v4 fee")
    return protocol + lp_fee - (protocol * lp_fee // PIPS)


def compute_swap_step(
    sqrt_price_current_x96: int,
    sqrt_price_target_x96: int,
    liquidity: int,
    amount_remaining: int,
    fee_pips: int,
) -> tuple[int, int, int, int]:
    """Port of v4-core ``SwapMath.computeSwapStep`` for exact input.

    ``amount_remaining`` is a positive remaining input in this Python adapter;
    v4 Solidity passes its negation.  The output preserves V4's partial-step
    rounding, which differs from the V3 implementation by a wei in some cases.
    """
    if amount_remaining < 0 or not 0 <= fee_pips <= PIPS:
        raise v3.EvmRevert("invalid v4 exact-input step")
    zero_for_one = sqrt_price_current_x96 >= sqrt_price_target_x96
    less_fee = v3.mul_div(amount_remaining, PIPS - fee_pips, PIPS)
    amount_in = (
        v3.get_amount0_delta(sqrt_price_target_x96, sqrt_price_current_x96, liquidity, True)
        if zero_for_one
        else v3.get_amount1_delta(sqrt_price_current_x96, sqrt_price_target_x96, liquidity, True)
    )
    if less_fee >= amount_in:
        next_price = sqrt_price_target_x96
        fee_amount = amount_in if fee_pips == PIPS else v3.mul_div_rounding_up(
            amount_in, fee_pips, PIPS - fee_pips
        )
    else:
        amount_in = less_fee
        next_price = v3.get_next_sqrt_price_from_input(
            sqrt_price_current_x96, liquidity, amount_in, zero_for_one
        )
        fee_amount = amount_remaining - amount_in
    amount_out = (
        v3.get_amount1_delta(next_price, sqrt_price_current_x96, liquidity, False)
        if zero_for_one
        else v3.get_amount0_delta(sqrt_price_current_x96, next_price, liquidity, False)
    )
    return next_price, amount_in, amount_out, fee_amount
