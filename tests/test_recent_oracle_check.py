"""Tests for recent_oracle_check: timestamp selection, normalization, separate feed ages, and fail-closed logic."""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

from eth_abi import encode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "recent_oracle_check", ROOT / "scripts/recent_oracle_check.py"
)
recent_oracle_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(recent_oracle_check)

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.oracles.reference_collector import (
    ONEINCH_OFFCHAIN_ORACLE,
    USDC_ADDRESS,
    WETH_ADDRESS,
    compute_arithmetic_mean_tick,
    decode_1inch_rate,
    get_quote_at_tick_canonical,
)

calculate_gaps = recent_oracle_check.calculate_gaps
decode_chainlink_pair = recent_oracle_check.decode_chainlink_pair
find_block_for_target = recent_oracle_check.find_block_for_target


class MockRpcClient:
    """Mock RPC client that returns blocks from a pre-defined synthetic timeline."""
    def __init__(self, blocks: dict[int, BlockRef]) -> None:
        self.blocks = blocks

    def get_block(self, number: int) -> BlockRef:
        if number not in self.blocks:
            raise ValueError(f"Unknown block {number}")
        return self.blocks[number]


def test_timestamp_selection_and_bounds_verification():
    """Verify interpolated/binary search finds greatest block <= target and verifies chosen <= target < next."""
    # Synthetic blocks: slot spacing is 12 seconds with an occasional missed slot
    # Block 100: ts 1000
    # Block 101: ts 1012
    # Block 102: ts 1024
    # Block 103: ts 1048 (missed slot: 24s gap)
    # Block 104: ts 1060
    # Block 105: ts 1072
    blocks = {
        100: BlockRef(chain=1, number=100, hash="0x100", timestamp=1000),
        101: BlockRef(chain=1, number=101, hash="0x101", timestamp=1012),
        102: BlockRef(chain=1, number=102, hash="0x102", timestamp=1024),
        103: BlockRef(chain=1, number=103, hash="0x103", timestamp=1048),
        104: BlockRef(chain=1, number=104, hash="0x104", timestamp=1060),
        105: BlockRef(chain=1, number=105, hash="0x105", timestamp=1072),
    }
    client = MockRpcClient(blocks)
    cache: dict[int, BlockRef] = dict(blocks)

    # Case 1: Exact hit on block timestamp
    chosen, chosen_next = find_block_for_target(client, target_ts=1012, low_bound=100, high_bound=105, cache=cache)
    assert chosen.number == 101
    assert chosen.timestamp == 1012
    assert chosen_next.number == 102
    assert chosen_next.timestamp == 1024
    assert chosen.timestamp <= 1012 < chosen_next.timestamp

    # Case 2: In-between regular blocks
    chosen, chosen_next = find_block_for_target(client, target_ts=1020, low_bound=100, high_bound=105, cache=cache)
    assert chosen.number == 101
    assert chosen_next.number == 102
    assert chosen.timestamp <= 1020 < chosen_next.timestamp

    # Case 3: Target falling in missed slot gap (1024 -> 1048)
    chosen, chosen_next = find_block_for_target(client, target_ts=1040, low_bound=100, high_bound=105, cache=cache)
    assert chosen.number == 102
    assert chosen_next.number == 103
    assert chosen.timestamp <= 1040 < chosen_next.timestamp

    # Case 4: Target at 1048
    chosen, chosen_next = find_block_for_target(client, target_ts=1048, low_bound=100, high_bound=105, cache=cache)
    assert chosen.number == 103
    assert chosen_next.number == 104
    assert chosen.timestamp <= 1048 < chosen_next.timestamp


def test_chainlink_normalization_and_distinct_feed_ages():
    """Verify Chainlink ETH/USD divided by USDC/USD calculation, distinct ETH vs USDC ages, and fail-closed checks."""
    block_timestamp = 1788985000
    eth_updated_at = 1788984000   # 1000s ago
    usdc_updated_at = 1788940000  # 45000s ago

    # ETH/USD = $2500.00 (8 decimals = 250000000000)
    # USDC/USD = $0.9998 (8 decimals = 99980000)
    eth_bytes = encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [1, 250000000000, eth_updated_at, eth_updated_at, 1],
    )
    usdc_bytes = encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [1, 99980000, usdc_updated_at, usdc_updated_at, 1],
    )

    spec_eth = CallSpec("0x" + "1" * 40, "0x")
    spec_usdc = CallSpec("0x" + "2" * 40, "0x")

    res_eth = CallResult(spec_eth, True, "0x" + eth_bytes.hex(), "eth_call")
    res_usdc = CallResult(spec_usdc, True, "0x" + usdc_bytes.hex(), "eth_call")

    dec = decode_chainlink_pair(res_eth, res_usdc, block_timestamp)
    assert dec["status"] == "ok"
    assert dec["eth_age_seconds"] == 1000
    assert dec["usdc_age_seconds"] == 45000
    assert dec["eth_age_seconds"] != dec["usdc_age_seconds"]  # strictly distinct

    expected_price = float(Decimal(250000000000) / Decimal(99980000))
    assert abs(dec["price"] - expected_price) < 1e-6
    assert abs(dec["price"] - 2500.5001) < 0.01

    # Fail-closed 1: Call reverted
    res_failed = CallResult(spec_eth, False, "0x", "eth_call")
    dec_fail = decode_chainlink_pair(res_failed, res_usdc, block_timestamp)
    assert dec_fail["status"] == "call_reverted"
    assert dec_fail["price"] is None

    # Fail-closed 2: Non-positive answer
    eth_nonpos_bytes = encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [1, 0, eth_updated_at, eth_updated_at, 1],
    )
    res_nonpos = CallResult(spec_eth, True, "0x" + eth_nonpos_bytes.hex(), "eth_call")
    dec_nonpos = decode_chainlink_pair(res_nonpos, res_usdc, block_timestamp)
    assert dec_nonpos["status"] == "non_positive_answer"
    assert dec_nonpos["price"] is None

    # Fail-closed 3: Missing updatedAt (timestamp = 0)
    eth_zero_bytes = encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [1, 250000000000, 0, 0, 1],
    )
    res_zero = CallResult(spec_eth, True, "0x" + eth_zero_bytes.hex(), "eth_call")
    dec_zero = decode_chainlink_pair(res_zero, res_usdc, block_timestamp)
    assert dec_zero["status"] == "missing_history"
    assert dec_zero["price"] is None


def test_1inch_and_univ3_twap_normalization():
    """Verify 1inch rate / 10^6 and UniV3 geometric mean tick quote at tick."""
    # 1. 1inch: 2470.123456 USDC per WETH -> rawRate = 2470123456
    spec = CallSpec(ONEINCH_OFFCHAIN_ORACLE, "0x")
    res_1i = CallResult(spec, True, "0x" + encode(["uint256"], [2470123456]).hex(), "eth_call")
    dec_1i = decode_1inch_rate(res_1i)
    assert dec_1i["status"] == "ok"
    assert abs(dec_1i["price"] - 2470.123456) < 1e-6

    # 2. UniV3 tick calculation
    # Negative tick floor division:
    assert compute_arithmetic_mean_tick(-61, 60) == -2
    assert compute_arithmetic_mean_tick(61, 60) == 1

    # Quote at tick 194010 (approx 3755.55 USDC per WETH)
    raw_quote = get_quote_at_tick_canonical(194010, base_amount=10**18, base_token=WETH_ADDRESS, quote_token=USDC_ADDRESS)
    price_u3 = float(raw_quote) / 10**6
    assert abs(price_u3 - 3755.554042) < 1e-5


def test_summary_gaps_calculation():
    """Verify gap bps calculations, thresholds >100bps, >500bps, and worst point detection."""
    synthetic_rows = [
        {
            "target_index": 0,
            "target_utc": "2026-09-09T00:00:00Z",
            "block_number": 25000000,
            "chainlink": {"price": 2500.0, "eth_age_seconds": 60, "usdc_age_seconds": 3600},
            "oneinch": {"price": 2510.0},        # +40 bps
            "univ3_twap_300": {"price": 2530.0}, # +120 bps (>100 bps)
            "univ3_twap_60": {"price": 2650.0},  # +600 bps (>500 bps)
        },
        {
            "target_index": 1,
            "target_utc": "2026-09-09T00:10:00Z",
            "block_number": 25000050,
            "chainlink": {"price": 2500.0, "eth_age_seconds": 120, "usdc_age_seconds": 4200},
            "oneinch": {"price": 2490.0},        # -40 bps
            "univ3_twap_300": {"price": 2480.0}, # -80 bps
            "univ3_twap_60": {"price": 2505.0},  # +20 bps
        },
    ]

    summaries = calculate_gaps(synthetic_rows)

    # 1inch: gaps [+40, -40], median = 0, count > 100 = 0
    oi = summaries["oneinch"]
    assert oi["valid_count"] == 2
    assert oi["min_gap_bps"] == -40.0
    assert oi["max_gap_bps"] == 40.0
    assert oi["median_gap_bps"] == 0.0
    assert oi["count_gt_100bps"] == 0
    assert oi["count_gt_500bps"] == 0

    # UniV3 300s: gaps [+120, -80], count > 100 = 1, worst = +120
    u3 = summaries["univ3_twap_300"]
    assert u3["count_gt_100bps"] == 1
    assert u3["count_gt_500bps"] == 0
    assert u3["worst_divergence"]["gap_bps"] == 120.0
    assert u3["worst_divergence"]["eth_age_seconds"] == 60
    assert u3["worst_divergence"]["usdc_age_seconds"] == 3600

    # UniV3 60s: gaps [+600, +20], count > 500 = 1, worst = +600
    u6 = summaries["univ3_twap_60"]
    assert u6["count_gt_500bps"] == 1
    assert u6["worst_divergence"]["gap_bps"] == 600.0
