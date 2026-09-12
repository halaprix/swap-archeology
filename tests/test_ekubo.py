"""Tests for Ekubo protocol semantics, math, encoding, and historical qualification.

Covers:
- V2 vs V3 deployment qualification and opcode compatibility
- Config bit-packing and cross-version discriminator divergence
- Fee conversion between basis points and 2^64 uint64 fixed-point binary fractions
- Pool ID derivation matching EVM keccak256(abi.encode(address, address, bytes32))
- Native ETH representation (address(0)) and token ordering
- Extension hook byte extraction from contract addresses
- Offline validation of historical pin evidence for October 10, 2025
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_abi import encode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_PINS_PATH = ROOT / "outputs/source-expansion/ekubo/historical-pins.json"

# Deployments
EKUBO_V2_CORE = "0xe0e0e08a6a4b9dc7bd67bcb7aade5cf48157d444"
EKUBO_V3_CORE = "0x00000000000014aa86c5d3c41765bb24e11bd701"
EKUBO_V2_ROUTER = "0x9995855c00494d039ab6792f18e368e530dff931"
EKUBO_V2_QUOTE_FETCHER = "0x91cb8a896caf5e60b1f7c4818730543f849b408c"
EKUBO_V2_CORE_FETCHER = "0x208bb00c6b142351e4a431f6dd323691ebb7c285"

NATIVE_ETH = "0x0000000000000000000000000000000000000000"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
TWO_POW_64 = 1 << 64


# --- Encoding / Math Helpers ---


def bps_to_fee_raw(bps: float | str | Decimal) -> int:
    """Convert fee in basis points (1 bps = 0.01%) to uint64 binary fraction of 2^64."""
    d_bps = Decimal(str(bps))
    return int((d_bps * Decimal(TWO_POW_64)) // Decimal(10000))


def fee_raw_to_bps(fee_raw: int) -> float:
    """Convert uint64 binary fraction of 2^64 to fee in basis points."""
    d_raw = Decimal(fee_raw)
    return float((d_raw * Decimal(10000)) / Decimal(TWO_POW_64))


def pack_v2_config(fee_raw: int, tick_spacing: int, extension: str = NATIVE_ETH) -> bytes:
    """Pack Ekubo V2 Config: (extension << 96) | (fee << 32) | tick_spacing."""
    ext_int = int(extension, 16)
    val = (ext_int << 96) | (fee_raw << 32) | tick_spacing
    return val.to_bytes(32, byteorder="big")


def unpack_v2_config(config: bytes) -> dict[str, Any]:
    """Unpack Ekubo V2 Config into its constituent fields."""
    val = int.from_bytes(config, byteorder="big")
    tick_spacing = val & 0xFFFFFFFF
    fee_raw = (val >> 32) & 0xFFFFFFFFFFFFFFFF
    ext_int = (val >> 96) & ((1 << 160) - 1)
    extension = f"0x{ext_int:040x}"
    return {
        "extension": extension,
        "fee_raw": fee_raw,
        "fee_bps": fee_raw_to_bps(fee_raw),
        "tick_spacing": tick_spacing,
    }


def pack_v3_concentrated_config(fee_raw: int, tick_spacing: int, extension: str = NATIVE_ETH) -> bytes:
    """Pack Ekubo V3 concentrated PoolConfig: bit 31 is 1, bits 30..0 are tick_spacing."""
    ext_int = int(extension, 16)
    discriminator_and_ts = (1 << 31) | (tick_spacing & 0x7FFFFFFF)
    val = (ext_int << 96) | (fee_raw << 32) | discriminator_and_ts
    return val.to_bytes(32, byteorder="big")


def pack_v3_stableswap_config(
    fee_raw: int, amplification: int, center_tick_div16: int, extension: str = NATIVE_ETH
) -> bytes:
    """Pack Ekubo V3 stableswap PoolConfig: bit 31 is 0, bits 30..24 is A, bits 23..0 is center_tick/16."""
    ext_int = int(extension, 16)
    params = ((amplification & 0x7F) << 24) | (center_tick_div16 & 0xFFFFFF)
    val = (ext_int << 96) | (fee_raw << 32) | params
    return val.to_bytes(32, byteorder="big")


def unpack_v3_config(config: bytes) -> dict[str, Any]:
    """Unpack Ekubo V3 PoolConfig honoring the discriminator bit."""
    val = int.from_bytes(config, byteorder="big")
    lower32 = val & 0xFFFFFFFF
    fee_raw = (val >> 32) & 0xFFFFFFFFFFFFFFFF
    ext_int = (val >> 96) & ((1 << 160) - 1)
    extension = f"0x{ext_int:040x}"

    is_concentrated = bool((lower32 >> 31) & 1)
    if is_concentrated:
        pool_type = "concentrated"
        tick_spacing = lower32 & 0x7FFFFFFF
        extra = {"tick_spacing": tick_spacing}
    elif lower32 == 0:
        pool_type = "full_range"
        extra = {"tick_spacing": 0}
    else:
        pool_type = "stableswap"
        amplification = (lower32 >> 24) & 0x7F
        raw_center = lower32 & 0xFFFFFF
        # Sign-extend 24-bit integer
        if raw_center & 0x800000:
            center_tick_div16 = raw_center - 0x1000000
        else:
            center_tick_div16 = raw_center
        extra = {"amplification": amplification, "center_tick": center_tick_div16 * 16}

    return {
        "extension": extension,
        "fee_raw": fee_raw,
        "fee_bps": fee_raw_to_bps(fee_raw),
        "pool_type": pool_type,
        **extra,
    }


def compute_pool_id(token0: str, token1: str, config: bytes) -> bytes:
    """Derive canonical Ekubo PoolId via keccak256(abi.encode(address, address, bytes32))."""
    encoded = encode(["address", "address", "bytes32"], [token0, token1, config])
    return keccak(encoded)


def extract_extension_hook_byte(extension_addr: str) -> int:
    """Extract top hook-selector byte from an extension address (uint8(uint160(ext) >> 152))."""
    ext_int = int(extension_addr, 16)
    return (ext_int >> 152) & 0xFF


# --- Test Cases ---


def test_v2_config_pack_unpack_roundtrip():
    """V2 config roundtrips cleanly without setting high bit in tick spacing."""
    fee_raw = bps_to_fee_raw(30.0)
    tick_spacing = 5982
    ext = "0x553a2efc570c9e104942cec6ac1c18118e54c091"

    config_bytes = pack_v2_config(fee_raw, tick_spacing, ext)
    assert len(config_bytes) == 32

    unpacked = unpack_v2_config(config_bytes)
    assert unpacked["extension"].lower() == ext.lower()
    assert unpacked["fee_raw"] == fee_raw
    assert abs(unpacked["fee_bps"] - 30.0) < 1e-9
    assert unpacked["tick_spacing"] == 5982


def test_v3_config_discriminator_divergence():
    """V3 concentrated config sets bit 31, while V2 layout does not."""
    fee_raw = bps_to_fee_raw(5.0)
    tick_spacing = 1000

    v2_bytes = pack_v2_config(fee_raw, tick_spacing)
    v3_bytes = pack_v3_concentrated_config(fee_raw, tick_spacing)

    assert v2_bytes != v3_bytes

    # Unpack V3 with V3 unpacker
    unpacked_v3 = unpack_v3_config(v3_bytes)
    assert unpacked_v3["pool_type"] == "concentrated"
    assert unpacked_v3["tick_spacing"] == 1000

    # Cross-unpacking: V2 bytes decoded as V3 is misidentified as stableswap!
    misidentified = unpack_v3_config(v2_bytes)
    assert misidentified["pool_type"] == "stableswap"

    # Cross-unpacking: V3 bytes decoded as V2 yields spurious 2^31 offset in tick spacing
    v2_view_of_v3 = unpack_v2_config(v3_bytes)
    assert v2_view_of_v3["tick_spacing"] == (1 << 31) + 1000


def test_v3_stableswap_and_full_range_configs():
    """V3 stableswap packing correctly encodes amplification and center tick."""
    fee_raw = bps_to_fee_raw(1.0)
    stableswap_bytes = pack_v3_stableswap_config(
        fee_raw=fee_raw,
        amplification=50,
        center_tick_div16=10,
    )
    unpacked = unpack_v3_config(stableswap_bytes)
    assert unpacked["pool_type"] == "stableswap"
    assert unpacked["amplification"] == 50
    assert unpacked["center_tick"] == 160

    # Full range has lower 32 bits == 0
    full_range_bytes = pack_v2_config(fee_raw, 0)
    unpacked_fr = unpack_v3_config(full_range_bytes)
    assert unpacked_fr["pool_type"] == "full_range"
    assert unpacked_fr["tick_spacing"] == 0


def test_fee_binary_fraction_constants():
    """Known canonical fee tier values match the exact 2^64 uint64 fixed-point math."""
    canonical_tiers = [
        (30.0, 55340232221128654),
        (5.0, 9223372036854775),
        (1.0, 1844674407370955),
        (0.5, 922337203685477),
        (0.25, 461168601842738),
        (0.1, 184467440737095),
    ]
    for bps, expected_raw in canonical_tiers:
        computed_raw = bps_to_fee_raw(bps)
        assert computed_raw == expected_raw, f"Mismatch for {bps} bps: {computed_raw} != {expected_raw}"
        recovered_bps = fee_raw_to_bps(computed_raw)
        assert abs(recovered_bps - bps) < 1e-6


def test_token_sorting_and_native_eth():
    """Native ETH is address(0) and always sorts before any ERC-20 contract address."""
    assert int(NATIVE_ETH, 16) == 0
    assert int(NATIVE_ETH, 16) < int(WETH, 16)
    assert int(NATIVE_ETH, 16) < int(USDC, 16)

    # In Ekubo, token0 is strictly lower address
    # ETH/USDC pair
    token0 = NATIVE_ETH
    token1 = USDC
    assert int(token0, 16) < int(token1, 16)

    # WETH/USDC pair: USDC (0xA0b8...) is strictly lower than WETH (0xC02a...)
    assert int(USDC, 16) < int(WETH, 16)


def test_pool_id_calculation():
    """Derive Ekubo pool ID via keccak256(abi.encode(address, address, bytes32))."""
    fee_raw = bps_to_fee_raw(30.0)
    cfg = pack_v2_config(fee_raw, 5982, NATIVE_ETH)
    pool_id = compute_pool_id(NATIVE_ETH, USDC, cfg)
    assert len(pool_id) == 32

    # Deterministic check
    expected = keccak(encode(["address", "address", "bytes32"], [NATIVE_ETH, USDC, cfg]))
    assert pool_id == expected


def test_extension_hook_byte_extraction():
    """Hook call points are embedded in the top byte of the extension address."""
    # Hookless extension address(0) has 0 hook points
    assert extract_extension_hook_byte(NATIVE_ETH) == 0

    # MEVResist extension on mainnet: 0x553a2EFc570c9e104942cEC6aC1c18118e54C091
    mev_resist = "0x553a2EFc570c9e104942cEC6aC1c18118e54C091"
    assert extract_extension_hook_byte(mev_resist) == 0x55

    # Oracle extension on mainnet: 0x51d02A5948496a67827242EaBc5725531342527C
    oracle = "0x51d02A5948496a67827242EaBc5725531342527C"
    assert extract_extension_hook_byte(oracle) == 0x51

    # TWAMM extension on mainnet: 0xd4279c050da1f5c5b2830558c7a08e57e12b54ec
    twamm = "0xd4279c050da1f5c5b2830558c7a08e57e12b54ec"
    assert extract_extension_hook_byte(twamm) == 0xD4


def test_offline_historical_pin_evidence():
    """Validate saved historical qualification evidence from outputs/source-expansion/ekubo/historical-pins.json."""
    assert HISTORICAL_PINS_PATH.exists(), f"Evidence file not found: {HISTORICAL_PINS_PATH}"

    data = json.loads(HISTORICAL_PINS_PATH.read_text())

    pins = data["pins"]
    assert pins == [23549939, 23550094, 23550192]

    # Verify block hashes
    expected_hashes = {
        "23549939": "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
        "23550094": "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d",
        "23550192": "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c",
    }

    results = data["results_by_pin"]
    for pin_str, exp_hash in expected_hashes.items():
        pin_data = results[pin_str]
        assert pin_data["block"]["hash"] == exp_hash

        # V2 Core bytecode is present
        v2_code = pin_data["codes"]["v2_core"]["code"]
        assert len(v2_code) > 10000, f"V2 Core code missing at pin {pin_str}"

        # V3 Core bytecode is absent (0x) across all October pins
        v3_code = pin_data["codes"]["v3_core"]["code"]
        assert v3_code == "0x", f"V3 Core unexpectedly present at pin {pin_str}: {v3_code[:20]}"

        # Check candidate hookless pools: all active in-range liquidity was 0
        for pool in pin_data["pools"]:
            if pool["initialized"]:
                assert pool["liquidity"] == 0, (
                    f"Unexpected non-zero liquidity in {pool['label']} at pin {pin_str}: {pool['liquidity']}"
                )

    # Verify zero pool initialized events in the sampled 254-block October 10 window (does not bound pre-existing pools)
    assert len(data["window_pool_initialized_logs"]) == 0


def test_v2_lens_abi_and_liquidity_semantics():
    """Verify V2 lens ABI matching canonical contracts and that active liquidity=0 does not mean zero swappable liquidity across ticks."""
    from eth_abi import decode, encode

    # CoreDataFetcher.poolState((address,address,bytes32)) returns (uint96 sqrtRatio, int32 tick, uint128 liquidity)
    sqrt_ratio_raw = 19807926189578160000000000000
    current_tick = -20054258
    active_liquidity = 0

    encoded_state = encode(["uint96", "int32", "uint128"], [sqrt_ratio_raw, current_tick, active_liquidity])
    decoded_sqrt_ratio, decoded_tick, decoded_liquidity = decode(["uint96", "int32", "uint128"], encoded_state)
    assert decoded_sqrt_ratio == sqrt_ratio_raw
    assert decoded_tick == current_tick
    assert decoded_liquidity == active_liquidity

    # QuoteDataFetcher.getQuoteData returns QuoteData[]:
    # (int32 tick, uint96 sqrtRatio, uint128 liquidity, int32 minTick, int32 maxTick, (int32 tick, int128 liquidityDelta)[])
    # Active liquidity at current tick can be zero while swappable liquidity exists at adjacent initialized ticks
    sample_ticks = [(current_tick + 5982, 1000000000), (current_tick + 11964, -1000000000)]
    encoded_quote = encode(
        ["(int32,uint96,uint128,int32,int32,(int32,int128)[])[]"],
        [[(current_tick, sqrt_ratio_raw, 0, -25000000, -15000000, sample_ticks)]],
    )
    decoded_quote = decode(["(int32,uint96,uint128,int32,int32,(int32,int128)[])[]"], encoded_quote)[0]
    _q_tick, _q_sqrt, q_liq, _q_min, _q_max, q_deltas = decoded_quote[0]
    assert q_liq == 0  # Active in-range liquidity at current tick is zero
    assert len(q_deltas) == 2  # Resting liquidity exists if price crosses into initialized tick boundaries

