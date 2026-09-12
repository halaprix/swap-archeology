"""Offline checks for the ported Uniswap V3 core math.

Two kinds of evidence:

1. Fixed vectors from the Uniswap v3-core constants/test-suite (TickMath
   boundaries, FullMath edge cases).
2. A replay of 11 480 real ``Swap`` events saved under ``evidence/`` (never
   modified): every consecutive same-block pair with unchanged liquidity is
   reproduced bit-exactly by one of the three call shapes a pool exposes.

No RPC, no floats.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import uniswap_v3_csv_check as R

from swaparch.adapters.uniswap_v3 import math as m

CSV_DIR = ROOT / "evidence" / "crash-rescue-simulation" / "saved-data"
Q96 = 1 << 96


# --------------------------------------------------------------------- TickMath


def test_tick_math_boundary_vectors():
    # v3-core TickMath constants and TickMath.spec.ts boundary expectations
    assert m.MIN_TICK == -887272
    assert m.MAX_TICK == 887272
    assert m.get_sqrt_ratio_at_tick(m.MIN_TICK) == 4295128739
    assert m.get_sqrt_ratio_at_tick(m.MIN_TICK) == m.MIN_SQRT_RATIO
    assert m.get_sqrt_ratio_at_tick(m.MIN_TICK + 1) == 4295343490
    assert (
        m.get_sqrt_ratio_at_tick(m.MAX_TICK)
        == 1461446703485210103287273052203988822378723970342
    )
    assert m.get_sqrt_ratio_at_tick(m.MAX_TICK) == m.MAX_SQRT_RATIO
    assert m.get_sqrt_ratio_at_tick(0) == Q96 == 79228162514264337593543950336
    assert m.get_sqrt_ratio_at_tick(1) == 79232123823359799118286999568
    assert m.get_sqrt_ratio_at_tick(-1) == 79224201403219477170569942574


def test_tick_math_reverts_outside_range():
    with pytest.raises(m.EvmRevert):
        m.get_sqrt_ratio_at_tick(m.MAX_TICK + 1)
    with pytest.raises(m.EvmRevert):
        m.get_sqrt_ratio_at_tick(m.MIN_TICK - 1)
    with pytest.raises(m.EvmRevert):
        m.get_tick_at_sqrt_ratio(m.MIN_SQRT_RATIO - 1)
    with pytest.raises(m.EvmRevert):
        m.get_tick_at_sqrt_ratio(m.MAX_SQRT_RATIO)  # exclusive upper bound


def test_get_tick_at_sqrt_ratio_boundaries():
    assert m.get_tick_at_sqrt_ratio(m.MIN_SQRT_RATIO) == m.MIN_TICK
    assert m.get_tick_at_sqrt_ratio(m.MAX_SQRT_RATIO - 1) == m.MAX_TICK - 1


def test_tick_math_round_trip_and_monotonicity():
    ticks = list(range(m.MIN_TICK, m.MAX_TICK + 1, 4441))  # deterministic sweep
    ticks += [m.MIN_TICK, m.MIN_TICK + 1, -1, 0, 1, m.MAX_TICK - 1, m.MAX_TICK]
    previous = None
    for tick in sorted(set(ticks)):
        ratio = m.get_sqrt_ratio_at_tick(tick)
        if previous is not None:
            assert ratio > previous
        previous = ratio
        if tick < m.MAX_TICK:
            assert m.get_tick_at_sqrt_ratio(ratio) == tick


def test_tick_and_price_agree_on_every_saved_swap_event():
    """Independent on-chain evidence: 11 480 real (tick, sqrtPriceX96) pairs.

    ``getSqrtRatioAtTick(tick) <= sqrtPriceX96`` must always hold.  The strict
    upper bound ``< getSqrtRatioAtTick(tick + 1)`` holds except when the swap
    stopped exactly on a crossed tick, where ``Pool.swap`` stores
    ``tickNext - 1`` while the price is ``getSqrtRatioAtTick(tickNext)``.
    """
    total = strict = 0
    for path in sorted(CSV_DIR.glob("swaps_crash*_weth_usdc_500.csv")):
        with path.open() as fh:
            for row in csv.DictReader(fh):
                tick, price = int(row["tick"]), int(row["sqrtPriceX96"])
                total += 1
                assert m.get_sqrt_ratio_at_tick(tick) <= price
                assert price < m.get_sqrt_ratio_at_tick(tick + 2)
                if price < m.get_sqrt_ratio_at_tick(tick + 1):
                    strict += 1
    assert total == 11480
    assert strict == 11479  # the single exception is one exact tick-cross stop


# --------------------------------------------------------------------- FullMath


def test_full_math_vectors():
    assert m.mul_div(m.UINT256_MAX, m.UINT256_MAX, m.UINT256_MAX) == m.UINT256_MAX
    assert m.mul_div(Q96, 5, 3) == (Q96 * 5) // 3
    assert m.mul_div_rounding_up(Q96, 5, 3) == (Q96 * 5) // 3 + 1
    assert m.mul_div_rounding_up(6, 2, 3) == 4  # exact division does not round up
    with pytest.raises(m.EvmRevert):
        m.mul_div(1, 1, 0)
    with pytest.raises(m.EvmRevert):
        m.mul_div(m.UINT256_MAX, m.UINT256_MAX, 1)  # result does not fit uint256
    with pytest.raises(m.EvmRevert):
        m.mul_div_rounding_up(m.UINT256_MAX, m.UINT256_MAX, m.UINT256_MAX - 1)


def test_bit_math():
    assert m.most_significant_bit(1) == 0
    assert m.most_significant_bit(2) == 1
    assert m.most_significant_bit(m.UINT256_MAX) == 255
    assert m.least_significant_bit(1) == 0
    assert m.least_significant_bit(1 << 200) == 200
    assert m.least_significant_bit(m.UINT256_MAX) == 0
    with pytest.raises(m.EvmRevert):
        m.most_significant_bit(0)


# ----------------------------------------------------------------- SqrtPriceMath


def test_amount_deltas_rounding_directions():
    a, b, liquidity = m.get_sqrt_ratio_at_tick(0), m.get_sqrt_ratio_at_tick(100), 10**18
    down0 = m.get_amount0_delta(a, b, liquidity, False)
    up0 = m.get_amount0_delta(a, b, liquidity, True)
    down1 = m.get_amount1_delta(a, b, liquidity, False)
    up1 = m.get_amount1_delta(a, b, liquidity, True)
    assert 0 < down0 <= up0 <= down0 + 1
    assert 0 < down1 <= up1 <= down1 + 1
    # argument order must not matter
    assert m.get_amount0_delta(b, a, liquidity, True) == up0
    assert m.get_amount1_delta(b, a, liquidity, False) == down1
    # the signed overloads pick the rounding direction from the sign
    assert m.get_amount0_delta_signed(a, b, liquidity) == up0
    assert m.get_amount0_delta_signed(a, b, -liquidity) == -down0
    assert m.get_amount1_delta_signed(a, b, liquidity) == up1
    assert m.get_amount1_delta_signed(a, b, -liquidity) == -down1


def test_next_sqrt_price_directions():
    price, liquidity = Q96, 10**18
    assert m.get_next_sqrt_price_from_input(price, liquidity, 0, True) == price
    assert m.get_next_sqrt_price_from_input(price, liquidity, 0, False) == price
    # token0 in pushes the price down, token1 in pushes it up
    assert m.get_next_sqrt_price_from_input(price, liquidity, 10**17, True) < price
    assert m.get_next_sqrt_price_from_input(price, liquidity, 10**17, False) > price
    # taking token1 out pushes the price down, taking token0 out pushes it up
    assert m.get_next_sqrt_price_from_output(price, liquidity, 10**17, True) < price
    assert m.get_next_sqrt_price_from_output(price, liquidity, 10**17, False) > price
    with pytest.raises(m.EvmRevert):
        m.get_next_sqrt_price_from_input(0, liquidity, 1, True)
    with pytest.raises(m.EvmRevert):
        m.get_next_sqrt_price_from_input(price, 0, 1, True)


def test_amount0_fallback_rejects_checked_add_overflow():
    with pytest.raises(m.EvmRevert, match="sqrtP-amount0 overflow"):
        m.get_next_sqrt_price_from_input(Q96, 1, m.UINT256_MAX, True)


def test_add_delta():
    assert m.add_delta(1, 0) == 1
    assert m.add_delta(1, -1) == 0
    assert m.add_delta(1, 1) == 2
    with pytest.raises(m.EvmRevert):
        m.add_delta(0, -1)  # "LS"
    with pytest.raises(m.EvmRevert):
        m.add_delta(m.UINT128_MAX, 1)  # "LA"


# ------------------------------------------------------------------ TickBitmap


def test_bitmap_position_and_compression():
    assert m.position(0) == (0, 0)
    assert m.position(255) == (0, 255)
    assert m.position(256) == (1, 0)
    assert m.position(-1) == (-1, 255)
    assert m.position(-256) == (-1, 0)
    assert m.position(-257) == (-2, 255)
    # Solidity truncates toward zero then decrements negatives that do not divide
    assert m.compress(0, 10) == 0
    assert m.compress(9, 10) == 0
    assert m.compress(-1, 10) == -1
    assert m.compress(-10, 10) == -1
    assert m.compress(-11, 10) == -2
    assert m.word_pos_for_tick(198508, 10) == 77


def test_next_initialized_tick_within_one_word():
    spacing = 10
    # tick 200 -> compressed 20 -> word 0, bit 20; set bits 10 and 30
    bitmap = {0: (1 << 10) | (1 << 30)}
    assert m.next_initialized_tick_within_one_word(bitmap, 200, spacing, True) == (100, True)
    assert m.next_initialized_tick_within_one_word(bitmap, 200, spacing, False) == (300, True)
    # exactly on an initialized tick: lte finds itself, gt looks strictly higher
    assert m.next_initialized_tick_within_one_word(bitmap, 100, spacing, True) == (100, True)
    assert m.next_initialized_tick_within_one_word(bitmap, 100, spacing, False) == (300, True)
    # empty word: both directions walk to the word boundary and report uninitialized
    assert m.next_initialized_tick_within_one_word({0: 0}, 200, spacing, True) == (0, False)
    assert m.next_initialized_tick_within_one_word({0: 0}, 200, spacing, False) == (2550, False)
    with pytest.raises(KeyError):
        m.next_initialized_tick_within_one_word({}, 200, spacing, True)


# ------------------------------------------------------ SwapMath vs real swaps


def test_compute_swap_step_reproduces_every_saved_swap_event():
    """Replay of the copied `Swap` event CSVs (see ``scripts/uniswap_v3_csv_check.py``).

    Every consecutive same-block pair with unchanged liquidity must be
    reproduced exactly by one of the three call shapes; ``other`` must be empty.
    """
    total = {"pairs": 0, "exact_in": 0, "exact_out": 0, "limit": 0, "other": 0}
    for path in sorted(CSV_DIR.glob("swaps_crash*_weth_usdc_500.csv")):
        rows = R.read_rows(path)
        for prev, cur in R.candidate_pairs(rows):
            total["pairs"] += 1
            total[R.classify(prev, cur)] += 1

    assert total["other"] == 0, "a real swap the ported math cannot reproduce"
    assert total == {
        "pairs": 4203,
        "exact_in": 3222,
        "exact_out": 899,
        "limit": 82,
        "other": 0,
    }


def test_recorded_replay_summary_matches():
    """The saved evidence file must agree with what the code produces now."""
    saved = json.loads(
        (ROOT / "data" / "adapters-evidence" / "uniswap_v3" / "swap-event-replay.json").read_text()
    )
    assert saved["total"]["other"] == 0
    assert saved["total"]["pairs"] == 4203
    assert saved["fee_pips"] == 500


def test_compute_swap_step_fee_is_charged_per_step():
    """A step that reaches its target charges ceil(amountIn * fee / (1e6 - fee))."""
    current = m.get_sqrt_ratio_at_tick(0)
    target = m.get_sqrt_ratio_at_tick(-10)
    liquidity = 10**20
    nxt, amount_in, amount_out, fee = m.compute_swap_step(current, target, liquidity, 10**30, 500)
    assert nxt == target  # huge input, so the step is capped by the target
    assert fee == m.mul_div_rounding_up(amount_in, 500, 1_000_000 - 500)
    assert amount_in == m.get_amount0_delta(target, current, liquidity, True)
    assert amount_out == m.get_amount1_delta(target, current, liquidity, False)


def test_compute_swap_step_caps_exact_output_upstream_vector():
    # Fixed vector from v3-core SwapMath.spec.ts.
    assert m.compute_swap_step(
        417332158212080721273783715441582,
        1452870262520218020823638996,
        159344665391607089467575320103,
        -1,
        1,
    ) == (417332158212080721273783715441581, 1, 1, 1)


def test_compute_swap_step_partial_step_absorbs_the_remainder_as_fee():
    current = m.get_sqrt_ratio_at_tick(0)
    target = m.get_sqrt_ratio_at_tick(-887000)  # far away, never reached
    liquidity = 10**20
    amount = 10**18
    nxt, amount_in, _out, fee = m.compute_swap_step(
        current, target, liquidity, amount, 500
    )
    assert nxt != target
    assert amount_in + fee == amount  # the whole input is consumed
    assert fee == amount - amount_in
