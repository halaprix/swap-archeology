"""Exact integer ports of the Uniswap V3 core math libraries.

Every function here reproduces the Solidity semantics of
``@uniswap/v3-core@1.0.0`` bit for bit: unsigned 256-bit wrap-around where the
original used ``unchecked``/assembly, checked reverts where the original used
``require``, and identical rounding directions.  There are no floats anywhere.

Sources ported (v3-core ``contracts/libraries/``):

* ``FullMath.sol``      -> :func:`mul_div`, :func:`mul_div_rounding_up`
* ``TickMath.sol``      -> :func:`get_sqrt_ratio_at_tick`, :func:`get_tick_at_sqrt_ratio`
* ``SqrtPriceMath.sol`` -> :func:`get_next_sqrt_price_from_input` / ``_output``,
  :func:`get_amount0_delta`, :func:`get_amount1_delta`
* ``SwapMath.sol``      -> :func:`compute_swap_step`
* ``LiquidityMath.sol`` -> :func:`add_delta`
* ``TickBitmap.sol``    -> :func:`next_initialized_tick_within_one_word`, :func:`position`
* ``BitMath.sol``       -> :func:`most_significant_bit`, :func:`least_significant_bit`

A Solidity ``require`` that would revert raises :class:`EvmRevert`; callers in
``state.py`` translate that into ``core.protocols.Unsupported`` where it is a
modelling limit rather than a bug.
"""

from __future__ import annotations

from collections.abc import Mapping

# --------------------------------------------------------------------------
# machine word constants
# --------------------------------------------------------------------------

UINT256_MAX = (1 << 256) - 1
UINT160_MAX = (1 << 160) - 1
UINT128_MAX = (1 << 128) - 1
INT256_MIN = -(1 << 255)
INT256_MAX = (1 << 255) - 1
Q96 = 1 << 96
Q128 = 1 << 128

# TickMath
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342


class EvmRevert(Exception):
    """A condition that would make the Solidity original revert."""


def _require(cond: bool, reason: str) -> None:
    if not cond:
        raise EvmRevert(reason)


def _as_uint256(x: int) -> int:
    """Wrap like Solidity ``unchecked`` arithmetic on ``uint256``."""
    return x & UINT256_MAX


def _to_int256(x: int) -> int:
    """SafeCast.toInt256 - reverts above 2**255-1."""
    _require(x <= INT256_MAX, "toInt256 overflow")
    return x


def _to_uint160(x: int) -> int:
    _require(0 <= x <= UINT160_MAX, "toUint160 overflow")
    return x


# --------------------------------------------------------------------------
# FullMath
# --------------------------------------------------------------------------


def mul_div(a: int, b: int, denominator: int) -> int:
    """``FullMath.mulDiv`` - floor((a*b)/denominator) at full 512-bit precision.

    Reverts when ``denominator == 0`` or the result does not fit in uint256.
    """
    _require(0 <= a <= UINT256_MAX, "mulDiv: a not uint256")
    _require(0 <= b <= UINT256_MAX, "mulDiv: b not uint256")
    _require(denominator > 0, "mulDiv: denominator == 0")
    result = (a * b) // denominator
    _require(result <= UINT256_MAX, "mulDiv: overflow")
    return result


def mul_div_rounding_up(a: int, b: int, denominator: int) -> int:
    """``FullMath.mulDivRoundingUp`` - ceil((a*b)/denominator)."""
    result = mul_div(a, b, denominator)
    if (a * b) % denominator > 0:
        _require(result < UINT256_MAX, "mulDivRoundingUp: overflow")
        result += 1
    return result


def _div_rounding_up(x: int, y: int) -> int:
    """``UnsafeMath.divRoundingUp`` (v3-core inlines this in SqrtPriceMath)."""
    _require(y > 0, "divRoundingUp: y == 0")
    return x // y + (1 if x % y > 0 else 0)


# --------------------------------------------------------------------------
# BitMath
# --------------------------------------------------------------------------


def most_significant_bit(x: int) -> int:
    _require(x > 0, "BitMath.mostSignificantBit: x == 0")
    return x.bit_length() - 1


def least_significant_bit(x: int) -> int:
    _require(x > 0, "BitMath.leastSignificantBit: x == 0")
    return (x & -x).bit_length() - 1


# --------------------------------------------------------------------------
# TickMath
# --------------------------------------------------------------------------

# The magic constants below are the ones hard-coded in TickMath.sol; each is
# 2**128 / 1.0001**(2**i / 2) rounded, applied when bit i of |tick| is set.
_TICK_MAGIC: tuple[tuple[int, int], ...] = (
    (0x2, 0xFFF97272373D413259A46990580E213A),
    (0x4, 0xFFF2E50F5F656932EF12357CF3C7FDCC),
    (0x8, 0xFFE5CACA7E10E4E61C3624EAA0941CD0),
    (0x10, 0xFFCB9843D60F6159C9DB58835C926644),
    (0x20, 0xFF973B41FA98C081472E6896DFB254C0),
    (0x40, 0xFF2EA16466C96A3843EC78B326B52861),
    (0x80, 0xFE5DEE046A99A2A811C461F1969C3053),
    (0x100, 0xFCBE86C7900A88AEDCFFC83B479AA3A4),
    (0x200, 0xF987A7253AC413176F2B074CF7815E54),
    (0x400, 0xF3392B0822B70005940C7A398E4B70F3),
    (0x800, 0xE7159475A2C29B7443B29C7FA6E889D9),
    (0x1000, 0xD097F3BDFD2022B8845AD8F792AA5825),
    (0x2000, 0xA9F746462D870FDF8A65DC1F90E061E5),
    (0x4000, 0x70D869A156D2A1B890BB3DF62BAF32F7),
    (0x8000, 0x31BE135F97D08FD981231505542FCFA6),
    (0x10000, 0x9AA508B5B7A84E1C677DE54F3E99BC9),
    (0x20000, 0x5D6AF8DEDB81196699C329225EE604),
    (0x40000, 0x2216E584F5FA1EA926041BEDFE98),
    (0x80000, 0x48A170391F7DC42444E8FA2),
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """``TickMath.getSqrtRatioAtTick`` - sqrt(1.0001**tick) * 2**96, Q64.96."""
    abs_tick = -tick if tick < 0 else tick
    _require(abs_tick <= MAX_TICK, "T")

    ratio = (
        0xFFFCB933BD6FAD37AA2D162D1A594001
        if (abs_tick & 0x1)
        else 0x100000000000000000000000000000000
    )
    for bit, magic in _TICK_MAGIC:
        if abs_tick & bit:
            ratio = (ratio * magic) >> 128

    if tick > 0:
        ratio = UINT256_MAX // ratio

    # Q128.128 -> Q64.96, rounding up (the Solidity ternary on the low 32 bits).
    return (ratio >> 32) + (1 if (ratio % (1 << 32)) != 0 else 0)


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    """``TickMath.getTickAtSqrtRatio`` - greatest tick with ratio <= input."""
    _require(MIN_SQRT_RATIO <= sqrt_price_x96 < MAX_SQRT_RATIO, "R")

    ratio = sqrt_price_x96 << 32

    r = ratio
    msb = 0
    for threshold, shift in (
        (0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF, 7),
        (0xFFFFFFFFFFFFFFFF, 6),
        (0xFFFFFFFF, 5),
        (0xFFFF, 4),
        (0xFF, 3),
        (0xF, 2),
        (0x3, 1),
        (0x1, 0),
    ):
        f = (1 << shift) if r > threshold else 0
        msb |= f
        r >>= f

    if msb >= 128:
        r = ratio >> (msb - 127)
    else:
        r = ratio << (127 - msb)

    log_2 = (msb - 128) << 64  # signed
    for i in range(63, 49, -1):  # 14 unrolled iterations in the original
        r = (r * r) >> 127
        f = r >> 128
        log_2 |= f << i
        r >>= f

    log_sqrt10001 = log_2 * 255738958999603826347141  # Q22.128 number

    # arithmetic (floor) shift right by 128 - Python's // matches int256 >>
    tick_low = (log_sqrt10001 - 3402992956809132418596140100660247210) >> 128
    tick_hi = (log_sqrt10001 + 291339464771989622907027621153398088495) >> 128

    if tick_low == tick_hi:
        return tick_low
    return tick_hi if get_sqrt_ratio_at_tick(tick_hi) <= sqrt_price_x96 else tick_low


# --------------------------------------------------------------------------
# SqrtPriceMath
# --------------------------------------------------------------------------


def get_next_sqrt_price_from_amount0_rounding_up(
    sqrt_px96: int, liquidity: int, amount: int, add: bool
) -> int:
    """``SqrtPriceMath.getNextSqrtPriceFromAmount0RoundingUp``."""
    if amount == 0:
        return sqrt_px96
    numerator1 = liquidity << 96

    if add:
        product = _as_uint256(amount * sqrt_px96)  # `unchecked` in Solidity
        if product // amount == sqrt_px96:
            denominator = _as_uint256(numerator1 + product)
            if denominator >= numerator1:
                return _to_uint160(mul_div_rounding_up(numerator1, sqrt_px96, denominator))
        denominator = (numerator1 // sqrt_px96) + amount
        _require(denominator <= UINT256_MAX, "sqrtP-amount0 overflow")
        return _to_uint160(_div_rounding_up(numerator1, denominator))

    product = _as_uint256(amount * sqrt_px96)
    _require(product // amount == sqrt_px96 and numerator1 > product, "sqrtP-amount0 underflow")
    denominator = numerator1 - product
    return _to_uint160(mul_div_rounding_up(numerator1, sqrt_px96, denominator))


def get_next_sqrt_price_from_amount1_rounding_down(
    sqrt_px96: int, liquidity: int, amount: int, add: bool
) -> int:
    """``SqrtPriceMath.getNextSqrtPriceFromAmount1RoundingDown``."""
    if add:
        if amount <= UINT160_MAX:
            quotient = (amount << 96) // liquidity
        else:
            quotient = mul_div(amount, Q96, liquidity)
        return _to_uint160(sqrt_px96 + quotient)

    if amount <= UINT160_MAX:
        quotient = _div_rounding_up(amount << 96, liquidity)
    else:
        quotient = mul_div_rounding_up(amount, Q96, liquidity)
    _require(sqrt_px96 > quotient, "sqrtP-amount1 underflow")
    return sqrt_px96 - quotient  # always fits uint160


def get_next_sqrt_price_from_input(
    sqrt_px96: int, liquidity: int, amount_in: int, zero_for_one: bool
) -> int:
    """``SqrtPriceMath.getNextSqrtPriceFromInput``."""
    _require(sqrt_px96 > 0, "sqrtPX96 == 0")
    _require(liquidity > 0, "liquidity == 0")
    if zero_for_one:
        return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_in, True)
    return get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount_in, True)


def get_next_sqrt_price_from_output(
    sqrt_px96: int, liquidity: int, amount_out: int, zero_for_one: bool
) -> int:
    """``SqrtPriceMath.getNextSqrtPriceFromOutput``."""
    _require(sqrt_px96 > 0, "sqrtPX96 == 0")
    _require(liquidity > 0, "liquidity == 0")
    if zero_for_one:
        return get_next_sqrt_price_from_amount1_rounding_down(
            sqrt_px96, liquidity, amount_out, False
        )
    return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_out, False)


def get_amount0_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    """``SqrtPriceMath.getAmount0Delta`` (unsigned, explicit rounding flag)."""
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96

    numerator1 = liquidity << 96
    numerator2 = sqrt_ratio_b_x96 - sqrt_ratio_a_x96
    _require(sqrt_ratio_a_x96 > 0, "getAmount0Delta: sqrtRatioA == 0")

    if round_up:
        return _div_rounding_up(
            mul_div_rounding_up(numerator1, numerator2, sqrt_ratio_b_x96), sqrt_ratio_a_x96
        )
    return mul_div(numerator1, numerator2, sqrt_ratio_b_x96) // sqrt_ratio_a_x96


def get_amount1_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    """``SqrtPriceMath.getAmount1Delta`` (unsigned, explicit rounding flag)."""
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96
    if round_up:
        return mul_div_rounding_up(liquidity, sqrt_ratio_b_x96 - sqrt_ratio_a_x96, Q96)
    return mul_div(liquidity, sqrt_ratio_b_x96 - sqrt_ratio_a_x96, Q96)


def get_amount0_delta_signed(sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int) -> int:
    """Signed overload: negative liquidity rounds down, positive rounds up."""
    if liquidity < 0:
        return -get_amount0_delta(sqrt_ratio_a_x96, sqrt_ratio_b_x96, -liquidity, False)
    return _to_int256(get_amount0_delta(sqrt_ratio_a_x96, sqrt_ratio_b_x96, liquidity, True))


def get_amount1_delta_signed(sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int) -> int:
    if liquidity < 0:
        return -get_amount1_delta(sqrt_ratio_a_x96, sqrt_ratio_b_x96, -liquidity, False)
    return _to_int256(get_amount1_delta(sqrt_ratio_a_x96, sqrt_ratio_b_x96, liquidity, True))


# --------------------------------------------------------------------------
# SwapMath
# --------------------------------------------------------------------------


def compute_swap_step(
    sqrt_ratio_current_x96: int,
    sqrt_ratio_target_x96: int,
    liquidity: int,
    amount_remaining: int,
    fee_pips: int,
) -> tuple[int, int, int, int]:
    """``SwapMath.computeSwapStep``.

    Returns ``(sqrtRatioNextX96, amountIn, amountOut, feeAmount)``.  ``amount_remaining``
    is signed: positive = exact input, negative = exact output.
    """
    zero_for_one = sqrt_ratio_current_x96 >= sqrt_ratio_target_x96
    exact_in = amount_remaining >= 0

    amount_in = 0
    amount_out = 0

    if exact_in:
        amount_remaining_less_fee = mul_div(amount_remaining, 1_000_000 - fee_pips, 1_000_000)
        if zero_for_one:
            amount_in = get_amount0_delta(
                sqrt_ratio_target_x96, sqrt_ratio_current_x96, liquidity, True
            )
        else:
            amount_in = get_amount1_delta(
                sqrt_ratio_current_x96, sqrt_ratio_target_x96, liquidity, True
            )
        if amount_remaining_less_fee >= amount_in:
            sqrt_ratio_next_x96 = sqrt_ratio_target_x96
        else:
            sqrt_ratio_next_x96 = get_next_sqrt_price_from_input(
                sqrt_ratio_current_x96, liquidity, amount_remaining_less_fee, zero_for_one
            )
    else:
        if zero_for_one:
            amount_out = get_amount1_delta(
                sqrt_ratio_target_x96, sqrt_ratio_current_x96, liquidity, False
            )
        else:
            amount_out = get_amount0_delta(
                sqrt_ratio_current_x96, sqrt_ratio_target_x96, liquidity, False
            )
        if (-amount_remaining) >= amount_out:
            sqrt_ratio_next_x96 = sqrt_ratio_target_x96
        else:
            sqrt_ratio_next_x96 = get_next_sqrt_price_from_output(
                sqrt_ratio_current_x96, liquidity, -amount_remaining, zero_for_one
            )

    is_max = sqrt_ratio_target_x96 == sqrt_ratio_next_x96

    if zero_for_one:
        if not (is_max and exact_in):
            amount_in = get_amount0_delta(
                sqrt_ratio_next_x96, sqrt_ratio_current_x96, liquidity, True
            )
        if not (is_max and not exact_in):
            amount_out = get_amount1_delta(
                sqrt_ratio_next_x96, sqrt_ratio_current_x96, liquidity, False
            )
    else:
        if not (is_max and exact_in):
            amount_in = get_amount1_delta(
                sqrt_ratio_current_x96, sqrt_ratio_next_x96, liquidity, True
            )
        if not (is_max and not exact_in):
            amount_out = get_amount0_delta(
                sqrt_ratio_current_x96, sqrt_ratio_next_x96, liquidity, False
            )

    # cap the output amount to not exceed the remaining output amount
    if (not exact_in) and amount_out > (-amount_remaining):
        amount_out = -amount_remaining

    if exact_in and sqrt_ratio_next_x96 != sqrt_ratio_target_x96:
        # we didn't reach the target, so take the remainder of the maximum input as fee
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = mul_div_rounding_up(amount_in, fee_pips, 1_000_000 - fee_pips)

    return sqrt_ratio_next_x96, amount_in, amount_out, fee_amount


# --------------------------------------------------------------------------
# LiquidityMath
# --------------------------------------------------------------------------


def add_delta(x: int, y: int) -> int:
    """``LiquidityMath.addDelta`` - uint128 + int128 with the original reverts."""
    if y < 0:
        z = x + y
        _require(z >= 0, "LS")  # `require(z < x)` after unchecked subtraction
        return z
    z = x + y
    _require(z <= UINT128_MAX, "LA")
    return z


# --------------------------------------------------------------------------
# TickBitmap
# --------------------------------------------------------------------------


def position(tick: int) -> tuple[int, int]:
    """``TickBitmap.position`` - (wordPos int16, bitPos uint8).

    Python's ``>>`` on negative ints is already an arithmetic shift and ``&``
    behaves as if the value had an infinite two's-complement representation, so
    both expressions match the Solidity ``int24 >> 8`` / ``uint8(tick % 256)``.
    """
    return tick >> 8, tick & 0xFF


def compress(tick: int, tick_spacing: int) -> int:
    """The ``compressed`` value from ``TickBitmap.nextInitializedTickWithinOneWord``.

    Solidity's ``int24 / int24`` truncates toward zero, then the original
    decrements for negative ticks that do not divide evenly.  The net effect is
    floor division, which is what Python's ``//`` already does.
    """
    return tick // tick_spacing


def next_initialized_tick_within_one_word(
    bitmap: Mapping[int, int], tick: int, tick_spacing: int, lte: bool
) -> tuple[int, bool]:
    """``TickBitmap.nextInitializedTickWithinOneWord``.

    ``bitmap`` maps ``wordPos -> uint256``.  A missing word raises
    :class:`KeyError`; ``state.py`` turns that into ``Unsupported`` so a walk can
    never silently treat unloaded liquidity as empty.
    """
    compressed = compress(tick, tick_spacing)

    if lte:
        word_pos, bit_pos = position(compressed)
        mask = (1 << bit_pos) - 1 + (1 << bit_pos)
        masked = bitmap[word_pos] & mask
        initialized = masked != 0
        if initialized:
            nxt = (compressed - (bit_pos - most_significant_bit(masked))) * tick_spacing
        else:
            nxt = (compressed - bit_pos) * tick_spacing
        return nxt, initialized

    word_pos, bit_pos = position(compressed + 1)
    mask = ~((1 << bit_pos) - 1) & UINT256_MAX
    masked = bitmap[word_pos] & mask
    initialized = masked != 0
    if initialized:
        nxt = (compressed + 1 + (least_significant_bit(masked) - bit_pos)) * tick_spacing
    else:
        nxt = (compressed + 1 + (255 - bit_pos)) * tick_spacing
    return nxt, initialized


def word_pos_for_tick(tick: int, tick_spacing: int) -> int:
    """The bitmap word a given tick's compressed index lives in."""
    return position(compress(tick, tick_spacing))[0]
