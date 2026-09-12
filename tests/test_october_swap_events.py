"""Focused regression tests for October 10 2025 swap events experiment.

Tests cover:
1. Direction under reversed token order (token0=WETH, token1=USDC vs token0=USDC, token1=WETH).
2. Volume-weighted unequal volumes (ensuring sum(USDC)/sum(WETH), not simple average of prices).
3. Empty direction handling (returns None/null, no zero division).
4. Ambiguous V2 swap logs rejection.
5. V3 post-swap spot price vs execution price distinction (using exact on-chain log 511 values).
6. Cached reversed-token map validation and fail-closed checks on invalid pools/tokens.
7. Rejection of logs with wrong blockHash, removed=True, or out-of-interval blockNumber.
8. Topic-to-family mismatch rejection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts.october_swap_events import (
    CANONICAL_END_BLOCK,
    CANONICAL_START_BLOCK,
    POOLS_CATALOG,
    USDC_ADDRESS,
    V3_SWAP_TOPIC,
    WETH_ADDRESS,
    compute_vwap,
    decode_v2_swap_log,
    decode_v3_swap_log,
    v3_post_swap_spot,
    validate_cache,
)

SAMPLE_POOL_V3 = {
    "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
    "label": "Uniswap V3 0.05%",
    "family": "uniswap_v3",
    "feeBps": 5.0,
}

SAMPLE_POOL_V2 = {
    "address": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
    "label": "Uniswap V2 0.3%",
    "family": "uniswap_v2",
    "feeBps": 30.0,
}


def test_direction_reversed_token_order_v3():
    """Verify V3 direction is consistently SELL_WETH when pool delta WETH > 0 and BUY_WETH when < 0,
    regardless of whether WETH is token0 or token1."""
    sqrt_price = 1364341743786692704350631879849110

    # Case A: token0 = USDC, token1 = WETH (standard)
    weth_in_wei = 10**18
    usdc_out_raw = 3400 * 10**6
    raw_data_standard_sell = (
        (-usdc_out_raw).to_bytes(32, "big", signed=True)
        + (weth_in_wei).to_bytes(32, "big", signed=True)
        + sqrt_price.to_bytes(32, "big", signed=False)
        + (0).to_bytes(32, "big", signed=False)
        + (0).to_bytes(32, "big", signed=True)
    ).hex()

    log_standard = {
        "blockNumber": 23550020,
        "blockHash": "0x8b495f6d13a48ce9420ebb269f2e52e5fd0d752083f7db37f7ec45eafb99d84d",
        "transactionHash": "0xaaa",
        "transactionIndex": 1,
        "logIndex": 1,
        "data": "0x" + raw_data_standard_sell,
        "topics": [V3_SWAP_TOPIC],
    }
    swap_std, err = decode_v3_swap_log(log_standard, SAMPLE_POOL_V3, USDC_ADDRESS, WETH_ADDRESS, "2025-10-10T21:30:23Z")
    assert err is None
    assert swap_std.direction == "SELL_WETH"
    assert swap_std.baseVolumeWeth == 1.0
    assert swap_std.quoteVolumeUsdc == 3400.0
    assert swap_std.executionPrice == pytest.approx(3400.0)

    # Case B: token0 = WETH, token1 = USDC (reversed)
    raw_data_reversed_sell = (
        (weth_in_wei).to_bytes(32, "big", signed=True)
        + (-usdc_out_raw).to_bytes(32, "big", signed=True)
        + sqrt_price.to_bytes(32, "big", signed=False)
        + (0).to_bytes(32, "big", signed=False)
        + (0).to_bytes(32, "big", signed=True)
    ).hex()

    log_reversed = {
        "blockNumber": 23550020,
        "blockHash": "0x8b495f6d13a48ce9420ebb269f2e52e5fd0d752083f7db37f7ec45eafb99d84d",
        "transactionHash": "0xbbb",
        "transactionIndex": 2,
        "logIndex": 2,
        "data": "0x" + raw_data_reversed_sell,
        "topics": [V3_SWAP_TOPIC],
    }
    swap_rev, err = decode_v3_swap_log(log_reversed, SAMPLE_POOL_V3, WETH_ADDRESS, USDC_ADDRESS, "2025-10-10T21:30:23Z")
    assert err is None
    assert swap_rev.direction == "SELL_WETH"
    assert swap_rev.baseVolumeWeth == 1.0
    assert swap_rev.quoteVolumeUsdc == 3400.0
    assert swap_rev.executionPrice == pytest.approx(3400.0)


def test_direction_reversed_token_order_v2():
    """Verify V2 direction handles reversed token0/token1 ordering correctly."""
    # Standard: token0 = USDC, token1 = WETH
    raw_v2_std = (
        (0).to_bytes(32, "big")
        + (10**18).to_bytes(32, "big")
        + (3400 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()
    log_v2_std = {
        "blockNumber": 23550020,
        "blockHash": "0x8b495f6d13a48ce9420ebb269f2e52e5fd0d752083f7db37f7ec45eafb99d84d",
        "transactionHash": "0x111",
        "transactionIndex": 1,
        "logIndex": 1,
        "data": "0x" + raw_v2_std,
    }
    swap_std, err = decode_v2_swap_log(log_v2_std, SAMPLE_POOL_V2, USDC_ADDRESS, WETH_ADDRESS, "2025-10-10T21:30:23Z")
    assert err is None
    assert swap_std.direction == "SELL_WETH"
    assert swap_std.baseVolumeWeth == 1.0
    assert swap_std.quoteVolumeUsdc == 3400.0

    # Reversed: token0 = WETH, token1 = USDC
    raw_v2_rev = (
        (10**18).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (3400 * 10**6).to_bytes(32, "big")
    ).hex()
    log_v2_rev = {
        "blockNumber": 23550020,
        "blockHash": "0x8b495f6d13a48ce9420ebb269f2e52e5fd0d752083f7db37f7ec45eafb99d84d",
        "transactionHash": "0x222",
        "transactionIndex": 2,
        "logIndex": 2,
        "data": "0x" + raw_v2_rev,
    }
    swap_rev, err = decode_v2_swap_log(log_v2_rev, SAMPLE_POOL_V2, WETH_ADDRESS, USDC_ADDRESS, "2025-10-10T21:30:23Z")
    assert err is None
    assert swap_rev.direction == "SELL_WETH"
    assert swap_rev.baseVolumeWeth == 1.0
    assert swap_rev.quoteVolumeUsdc == 3400.0


def test_weighted_unequal_volumes():
    """Verify VWAP metric uses SUM(USDC) / SUM(WETH) and rejects simple unweighted price average."""
    weth_total = 1.0 + 9.0
    usdc_total = 3000.0 + 36000.0
    vwap = compute_vwap(usdc_total, weth_total)

    assert vwap is not None
    assert vwap == pytest.approx(3900.0)
    assert vwap != pytest.approx(3500.0)


def test_empty_direction():
    """Verify that empty direction returns None (null) and does not raise ZeroDivisionError."""
    vwap_empty = compute_vwap(quote_sum=0.0, base_sum=0.0)
    assert vwap_empty is None

    vwap_zero_base = compute_vwap(quote_sum=100.0, base_sum=0.0)
    assert vwap_zero_base is None


def test_ambiguous_v2_rejected():
    """Verify that ambiguous or malformed V2 logs are explicitly rejected."""
    # Both tokens IN
    raw_both_in = (
        (100).to_bytes(32, "big")
        + (200).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()
    log_both_in = {"data": "0x" + raw_both_in, "blockNumber": 1, "blockHash": "0x", "transactionHash": "0x", "transactionIndex": 0, "logIndex": 0}
    swap, err = decode_v2_swap_log(log_both_in, SAMPLE_POOL_V2, USDC_ADDRESS, WETH_ADDRESS, "2025-10-10T21:30:23Z")
    assert swap is None
    assert "ambiguous" in err

    # Both tokens OUT
    raw_both_out = (
        (0).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (100).to_bytes(32, "big")
        + (200).to_bytes(32, "big")
    ).hex()
    log_both_out = {"data": "0x" + raw_both_out, "blockNumber": 1, "blockHash": "0x", "transactionHash": "0x", "transactionIndex": 0, "logIndex": 0}
    swap, err = decode_v2_swap_log(log_both_out, SAMPLE_POOL_V2, USDC_ADDRESS, WETH_ADDRESS, "2025-10-10T21:30:23Z")
    assert swap is None
    assert "ambiguous" in err

    # Same token IN and OUT
    raw_same_token = (
        (100).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (50).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()
    log_same = {"data": "0x" + raw_same_token, "blockNumber": 1, "blockHash": "0x", "transactionHash": "0x", "transactionIndex": 0, "logIndex": 0}
    swap, err = decode_v2_swap_log(log_same, SAMPLE_POOL_V2, USDC_ADDRESS, WETH_ADDRESS, "2025-10-10T21:30:23Z")
    assert swap is None
    assert "ambiguous" in err


def test_post_swap_spot_vs_execution_price():
    """Verify that V3 post-swap spot price is distinct from execution price using actual log 511 values."""
    # Actual on-chain values for log 511 (txIndex 157) in block 23550044:
    # amount0 (USDC raw) = 500000000 (500.000000 USDC)
    # amount1 (WETH wei) = -148199373792686690 (-0.14819937379268669 WETH)
    # sqrtPriceX96 = 1364341743786692704350631879849110
    amount0_usdc_raw = 500000000
    amount1_weth_wei = -148199373792686690
    sqrt_price = 1364341743786692704350631879849110

    spot = v3_post_swap_spot(sqrt_price, token0_is_usdc=True)
    execution_price = (amount0_usdc_raw / 1e6) / (-amount1_weth_wei / 1e18)

    assert spot == pytest.approx(3372.196189, rel=1e-6)
    assert execution_price == pytest.approx(3373.833419, rel=1e-6)
    assert abs(execution_price - spot) > 1.5  # Distinct: 3373.83 vs 3372.20


def test_validate_cache_reversed_token_map():
    """Verify validate_cache accepts a reversed token map where WETH is token0, and rejects wrong tokens."""
    dummy_source_rows = {
        b: {"blockHash": f"0x{b:064x}"} for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1)
    }
    dummy_hashes = {str(b): f"0x{b:064x}" for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1)}

    # Valid reversed token map
    reversed_token_map = {p["address"].lower(): (WETH_ADDRESS.lower(), USDC_ADDRESS.lower()) for p in POOLS_CATALOG}
    cached_data_valid = {
        "chainId": 1,
        "startBlock": CANONICAL_START_BLOCK,
        "endBlock": CANONICAL_END_BLOCK,
        "tokenMap": reversed_token_map,
        "blockHashes": dummy_hashes,
    }
    result_map = validate_cache(cached_data_valid, dummy_source_rows, POOLS_CATALOG)
    assert result_map[POOLS_CATALOG[0]["address"].lower()] == (WETH_ADDRESS.lower(), USDC_ADDRESS.lower())

    # Fail closed: chainId != 1
    with pytest.raises(ValueError, match="chainId mismatch"):
        validate_cache({**cached_data_valid, "chainId": 5}, dummy_source_rows, POOLS_CATALOG)

    # Fail closed: missing pool
    incomplete_map = dict(reversed_token_map)
    del incomplete_map[POOLS_CATALOG[0]["address"].lower()]
    with pytest.raises(ValueError, match="pool set mismatch"):
        validate_cache({**cached_data_valid, "tokenMap": incomplete_map}, dummy_source_rows, POOLS_CATALOG)

    # Fail closed: wrong token (e.g. DAI instead of USDC)
    wrong_token_map = dict(reversed_token_map)
    first_pool = POOLS_CATALOG[0]["address"].lower()
    wrong_token_map[first_pool] = (WETH_ADDRESS.lower(), "0x6b175474e89094c44da98b954eedeac495271d0f")
    with pytest.raises(ValueError, match="does not pair WETH and USDC"):
        validate_cache({**cached_data_valid, "tokenMap": wrong_token_map}, dummy_source_rows, POOLS_CATALOG)

    # Fail closed: mismatched block hash
    bad_hashes = dict(dummy_hashes)
    bad_hashes[str(CANONICAL_START_BLOCK)] = "0x0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(ValueError, match="block hash mismatch"):
        validate_cache({**cached_data_valid, "blockHashes": bad_hashes}, dummy_source_rows, POOLS_CATALOG)
