"""Canonical collector and decoders for historical oracle references.

Sources:
1. 1inch OffchainOracle (spot price aggregator):
   - Contract: 0x00000000000D6FFc74A8feb35aF5827bf57f6786
   - Deployed at block 20535992 (active Oct 10 2025 across 23549939..23550192)
   - Function: getRate(address srcToken, address dstToken, bool useWrappers)
   - Decimals: rate * 10^(src_decimals - dst_decimals) / 10^18 -> for WETH(18)->USDC(6): rate / 10^6.
   - Nature: Liquidity-weighted arithmetic mean of spot prices across DEXes (not trade quote, not CEX).

2. Uniswap V3 observation TWAP:
   - Reference: https://uniswapv3book.com/milestone_5/price-oracle.html & canonical OracleLibrary.sol
   - Pool: 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640 (USDC/WETH fee 500)
   - Windows: 60s and 300s (with 300s default frontend comparison)
   - Function: observe(uint32[] secondsAgos)
   - Nature: Pool-specific geometric time-weighted average price.
   - Floor negative tick: integer division floors toward negative infinity.
   - Fallback policy: None; if observe reverts or insufficient history, return null price and explicit status.
"""

from __future__ import annotations

from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from swaparch.adapters.uniswap_v3.math import UINT128_MAX, get_sqrt_ratio_at_tick, mul_div
from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.rpc.multicall import Multicall3

# Canonical Addresses
ONEINCH_OFFCHAIN_ORACLE = "0x00000000000D6FFc74A8feb35aF5827bf57f6786"
UNISWAP_V3_USDC_WETH_500 = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Function Selectors
GET_RATE_SELECTOR = keccak(text="getRate(address,address,bool)")[:4]  # 0x802431fb
OBSERVE_SELECTOR = keccak(text="observe(uint32[])")[:4]              # 0x883bdbfd


def compute_arithmetic_mean_tick(tick_cumulatives_delta: int, seconds_ago: int) -> int:
    """Calculates time-weighted arithmetic mean tick over seconds_ago.

    Follows Uniswap V3 canonical OracleLibrary.sol:
        arithmeticMeanTick = int24(tickCumulativesDelta / secondsAgo);
        if (tickCumulativesDelta < 0 && (tickCumulativesDelta % secondsAgo != 0)) arithmeticMeanTick--;

    In Python, integer floor division `//` natively rounds toward negative infinity.
    """
    if seconds_ago <= 0:
        raise ValueError("seconds_ago must be positive")
    return tick_cumulatives_delta // seconds_ago


def get_quote_at_tick_canonical(
    tick: int,
    base_amount: int = 10**18,
    base_token: str = WETH_ADDRESS,
    quote_token: str = USDC_ADDRESS,
) -> int:
    """Calculates quote amount at tick matching canonical Uniswap V3 OracleLibrary.getQuoteAtTick.

    For baseToken = WETH (token1), quoteToken = USDC (token0):
    Because baseToken.lower() > quoteToken.lower():
        quoteAmount = FullMath.mulDiv(1 << 192, baseAmount, ratioX192)
    Returns quote amount in raw quote token units (e.g. 6 decimals for USDC).
    """
    sqrt_ratio_x96 = get_sqrt_ratio_at_tick(tick)
    base_lower = base_token.lower() < quote_token.lower()
    if sqrt_ratio_x96 <= UINT128_MAX:
        ratio_x192 = sqrt_ratio_x96 * sqrt_ratio_x96
        if base_lower:
            return mul_div(ratio_x192, base_amount, 1 << 192)
        else:
            return mul_div(1 << 192, base_amount, ratio_x192)
    else:
        ratio_x128 = mul_div(sqrt_ratio_x96, sqrt_ratio_x96, 1 << 64)
        if base_lower:
            return mul_div(ratio_x128, base_amount, 1 << 128)
        else:
            return mul_div(1 << 128, base_amount, ratio_x128)


def encode_1inch_get_rate(
    src_token: str = WETH_ADDRESS,
    dst_token: str = USDC_ADDRESS,
    use_wrappers: bool = False,
) -> str:
    """Encodes getRate(address,address,bool) call data for 1inch OffchainOracle."""
    payload = GET_RATE_SELECTOR + encode(["address", "address", "bool"], [src_token, dst_token, use_wrappers])
    return "0x" + payload.hex()


def encode_univ3_observe(seconds_ago: int) -> str:
    """Encodes observe([secondsAgo, 0]) call data for Uniswap V3 pool."""
    payload = OBSERVE_SELECTOR + encode(["uint32[]"], [[seconds_ago, 0]])
    return "0x" + payload.hex()


def decode_1inch_rate(
    call_result: CallResult,
    src_decimals: int = 18,
    dst_decimals: int = 6,
) -> dict[str, Any]:
    """Decodes 1inch OffchainOracle getRate call result."""
    if not call_result.success:
        return {
            "success": False,
            "status": "reverted",
            "rawRate": None,
            "price": None,
            "reason": f"1inch call reverted: {call_result.raw}",
        }
    try:
        raw_bytes = bytes.fromhex(call_result.raw[2:])
        (raw_rate,) = decode(["uint256"], raw_bytes)
        if raw_rate == 0:
            return {
                "success": True,
                "status": "zero_rate",
                "rawRate": 0,
                "price": None,
                "reason": "1inch returned zero rate (no liquidity or available path)",
            }
        norm_factor = 10 ** (18 + dst_decimals - src_decimals)
        price = float(raw_rate) / float(norm_factor)
        return {
            "success": True,
            "status": "ok",
            "rawRate": raw_rate,
            "price": price,
            "reason": None,
        }
    except (DecodingError, ValueError, IndexError) as exc:
        return {
            "success": False,
            "status": "decoding_error",
            "rawRate": None,
            "price": None,
            "reason": f"decode failure: {exc}",
        }


def decode_univ3_twap(
    call_result: CallResult,
    window_seconds: int,
    base_amount: int = 10**18,
    base_token: str = WETH_ADDRESS,
    quote_token: str = USDC_ADDRESS,
    quote_decimals: int = 6,
) -> dict[str, Any]:
    """Decodes Uniswap V3 observe([window_seconds, 0]) call result."""
    if not call_result.success:
        return {
            "success": False,
            "status": "reverted",
            "tickCumulatives": None,
            "deltaTick": None,
            "arithmeticMeanTick": None,
            "quoteAmountRaw": None,
            "price": None,
            "reason": f"observe call reverted (insufficient observation history or uninitialized): {call_result.raw}",
        }
    try:
        raw_bytes = bytes.fromhex(call_result.raw[2:])
        tick_cumulatives, seconds_per_liquidity = decode(["int56[]", "uint160[]"], raw_bytes)
        if len(tick_cumulatives) != 2 or len(seconds_per_liquidity) != 2:
            return {
                "success": False,
                "status": "decoding_error",
                "tickCumulatives": None,
                "deltaTick": None,
                "arithmeticMeanTick": None,
                "quoteAmountRaw": None,
                "price": None,
                "reason": (
                    f"observe returned invalid array lengths: "
                    f"tickCumulatives={len(tick_cumulatives)}, secondsPerLiquidity={len(seconds_per_liquidity)} (expected exactly 2)"
                ),
            }
        delta_tick = tick_cumulatives[1] - tick_cumulatives[0]
        mean_tick = compute_arithmetic_mean_tick(delta_tick, window_seconds)
        quote_raw = get_quote_at_tick_canonical(
            mean_tick,
            base_amount=base_amount,
            base_token=base_token,
            quote_token=quote_token,
        )
        price = float(quote_raw) / float(10**quote_decimals)
        return {
            "success": True,
            "status": "ok",
            "tickCumulatives": list(tick_cumulatives),
            "deltaTick": delta_tick,
            "arithmeticMeanTick": mean_tick,
            "quoteAmountRaw": quote_raw,
            "price": price,
            "reason": None,
        }
    except (DecodingError, ValueError, IndexError) as exc:
        return {
            "success": False,
            "status": "decoding_error",
            "tickCumulatives": None,
            "deltaTick": None,
            "arithmeticMeanTick": None,
            "quoteAmountRaw": None,
            "price": None,
            "reason": f"decode failure: {exc}",
        }


def build_block_call_specs() -> list[CallSpec]:
    """Returns the 3 canonical call specs for 1inch spot and UniV3 TWAP 60/300."""
    return [
        CallSpec(ONEINCH_OFFCHAIN_ORACLE, encode_1inch_get_rate()),
        CallSpec(UNISWAP_V3_USDC_WETH_500, encode_univ3_observe(60)),
        CallSpec(UNISWAP_V3_USDC_WETH_500, encode_univ3_observe(300)),
    ]


def collect_block_references(
    multicall: Multicall3,
    block: BlockRef,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Queries and decodes references for a single block.

    Returns:
      (sidecar_row_values, raw_response_row)
    """
    specs = build_block_call_specs()
    res_1inch, res_60, res_300 = multicall.call(specs, block)

    dec_1inch = decode_1inch_rate(res_1inch)
    dec_60 = decode_univ3_twap(res_60, 60)
    dec_300 = decode_univ3_twap(res_300, 300)

    sidecar_values: dict[str, Any] = {
        "oneinch_spot": {
            "price": round(dec_1inch["price"], 6) if dec_1inch["price"] is not None else None,
            "status": dec_1inch["status"],
        },
        "uniswap_v3_twap_60": {
            "price": round(dec_60["price"], 6) if dec_60["price"] is not None else None,
            "status": dec_60["status"],
        },
        "uniswap_v3_twap_300": {
            "price": round(dec_300["price"], 6) if dec_300["price"] is not None else None,
            "status": dec_300["status"],
        },
    }
    if dec_1inch.get("reason"):
        sidecar_values["oneinch_spot"]["reason"] = dec_1inch["reason"]
    if dec_60.get("reason"):
        sidecar_values["uniswap_v3_twap_60"]["reason"] = dec_60["reason"]
    if dec_300.get("reason"):
        sidecar_values["uniswap_v3_twap_300"]["reason"] = dec_300["reason"]

    raw_row: dict[str, Any] = {
        "block": block.number,
        "blockHash": block.hash,
        "oneinch_spot": {
            "success": res_1inch.success,
            "via": res_1inch.via,
            "raw": res_1inch.raw,
            "rawRate": dec_1inch["rawRate"],
            "normalizedPrice": dec_1inch["price"],
            "status": dec_1inch["status"],
            "reason": dec_1inch["reason"],
        },
        "uniswap_v3_twap_60": {
            "success": res_60.success,
            "via": res_60.via,
            "raw": res_60.raw,
            "tickCumulatives": dec_60["tickCumulatives"],
            "deltaTick": dec_60["deltaTick"],
            "arithmeticMeanTick": dec_60["arithmeticMeanTick"],
            "quoteAmountRaw": dec_60["quoteAmountRaw"],
            "normalizedPrice": dec_60["price"],
            "status": dec_60["status"],
            "reason": dec_60["reason"],
        },
        "uniswap_v3_twap_300": {
            "success": res_300.success,
            "via": res_300.via,
            "raw": res_300.raw,
            "tickCumulatives": dec_300["tickCumulatives"],
            "deltaTick": dec_300["deltaTick"],
            "arithmeticMeanTick": dec_300["arithmeticMeanTick"],
            "quoteAmountRaw": dec_300["quoteAmountRaw"],
            "normalizedPrice": dec_300["price"],
            "status": dec_300["status"],
            "reason": dec_300["reason"],
        },
    }

    return sidecar_values, raw_row
