#!/usr/bin/env python3
"""Bounded historical swap-event experiment for October 10 2025 crash window.

Collects and decodes historical swap events from six direct WETH/USDC pools
across the 50 canonical blocks (23550020..23550069) spanning 21:30:23Z to 21:40:23Z.
Reuses RpcClient internally and never reads .env directly.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from eth_abi import decode as abi_decode
from eth_utils import keccak

from swaparch.rpc.client import RpcClient

# Scope constants
CANONICAL_START_BLOCK = 23550020
CANONICAL_END_BLOCK = 23550069
CANONICAL_START_UTC = "2025-10-10T21:30:23Z"
CANONICAL_END_UTC = "2025-10-10T21:40:23Z"
WORST_GAP_BLOCK = 23550044

# Canonical tokens
WETH_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH_DECIMALS = 18
USDC_DECIMALS = 6

# Topics
V3_SWAP_TOPIC = "0x" + keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
V2_SWAP_TOPIC = "0x" + keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()

# Six direct pools from catalog
POOLS_CATALOG = [
    {
        "id": "uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        "family": "uniswap_v3",
        "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        "feeBps": 5.0,
        "feeTier": 500,
        "label": "Uniswap V3 0.05%",
    },
    {
        "id": "uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
        "family": "uniswap_v3",
        "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
        "feeBps": 30.0,
        "feeTier": 3000,
        "label": "Uniswap V3 0.3%",
    },
    {
        "id": "uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:0xe0554a476a092703abdb3ef35c80e0d76d32939f",
        "family": "uniswap_v3",
        "address": "0xe0554a476a092703abdb3ef35c80e0d76d32939f",
        "feeBps": 1.0,
        "feeTier": 100,
        "label": "Uniswap V3 0.01%",
    },
    {
        "id": "uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:0x7bea39867e4169dbe237d55c8242a8f2fcdcc387",
        "family": "uniswap_v3",
        "address": "0x7bea39867e4169dbe237d55c8242a8f2fcdcc387",
        "feeBps": 100.0,
        "feeTier": 10000,
        "label": "Uniswap V3 1.0%",
    },
    {
        "id": "pancake_v3:0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865:0x1ac1a8feaaea1900c4166deeed0c11cc10669d36",
        "family": "pancake_v3",
        "address": "0x1ac1a8feaaea1900c4166deeed0c11cc10669d36",
        "feeBps": 5.0,
        "feeTier": 500,
        "label": "PancakeSwap V3 0.05%",
    },
    {
        "id": "uniswap_v2:0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f:0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
        "family": "uniswap_v2",
        "address": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
        "feeBps": 30.0,
        "feeTier": 3000,
        "label": "Uniswap V2 0.3%",
    },
]

OUTPUT_DIR = ROOT / "outputs/october-swap-events"
FRONTEND_DIR = ROOT / "frontend/public/october-swap-events"


def atomic_write_json(path: Path, payload: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=indent).encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False, prefix=f".{path.name}.tmp.") as f:
        tmp_name = f.name
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_name, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False, prefix=f".{path.name}.tmp.") as f:
        tmp_name = f.name
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_name, path)


def v3_post_swap_spot(sqrt_price_x96: int, token0_is_usdc: bool = True) -> float:
    """Computes post-swap spot price in USDC per 1 WETH from sqrtPriceX96."""
    if sqrt_price_x96 <= 0:
        raise ValueError(f"Invalid non-positive sqrtPriceX96: {sqrt_price_x96}")
    if token0_is_usdc:
        # token0 = USDC (6), token1 = WETH (18)
        # raw ratio token1/token0 = sqrtPriceX96^2 / 2^192
        # price USDC/WETH = (token0/1e6) / (token1/1e18) = (token0/token1) * 1e12 = 1e12 * 2^192 / sqrtPriceX96^2
        return (2**192 / (sqrt_price_x96**2)) * 10**12
    else:
        # token0 = WETH (18), token1 = USDC (6)
        # raw ratio token1/token0 = USDC_raw / WETH_wei = sqrtPriceX96^2 / 2^192
        # price USDC/WETH = (USDC_raw / 1e6) / (WETH_wei / 1e18) = (sqrtPriceX96^2 / 2^192) * 1e12
        return (sqrt_price_x96**2 / 2**192) * 10**12


@dataclass
class DecodedSwap:
    blockNumber: int
    blockHash: str
    timestamp: str
    transactionHash: str
    transactionIndex: int
    logIndex: int
    poolAddress: str
    poolLabel: str
    family: str
    feeBps: float
    direction: str  # "SELL_WETH" or "BUY_WETH"
    baseVolumeWei: str
    quoteVolumeRaw: str
    baseVolumeWeth: float
    quoteVolumeUsdc: float
    executionPrice: float
    postSwapSpot: float | None
    sqrtPriceX96: str | None
    intrablockIndex: int = 0


def decode_v3_swap_log(
    log: dict[str, Any],
    pool_meta: dict[str, Any],
    token0_addr: str,
    token1_addr: str,
    timestamp: str,
) -> tuple[DecodedSwap | None, str | None]:
    """Decodes a Uniswap V3 or PancakeSwap V3 Swap event log."""
    data_hex = log["data"].removeprefix("0x")
    raw_bytes = bytes.fromhex(data_hex)
    if len(raw_bytes) < 160:
        return None, f"invalid V3 data length: {len(raw_bytes)}"

    amount0 = int.from_bytes(raw_bytes[0:32], "big", signed=True)
    amount1 = int.from_bytes(raw_bytes[32:64], "big", signed=True)
    sqrt_price_x96 = int.from_bytes(raw_bytes[64:96], "big", signed=False)

    # Check tokens
    t0 = token0_addr.lower()
    t1 = token1_addr.lower()
    weth = WETH_ADDRESS.lower()
    usdc = USDC_ADDRESS.lower()

    if {t0, t1} != {weth, usdc}:
        return None, f"wrong tokens in pool: token0={t0}, token1={t1}"

    token0_is_usdc = (t0 == usdc)

    # Determine pool deltas for WETH and USDC
    # V3 amounts: positive => pool received tokens; negative => pool sent tokens
    if token0_is_usdc:
        usdc_delta = amount0
        weth_delta = amount1
    else:
        usdc_delta = amount1
        weth_delta = amount0

    # Trader perspective:
    # If pool delta WETH > 0: trader sent WETH to pool => SELL WETH, received USDC (usdc_delta < 0)
    # If pool delta WETH < 0: trader received WETH from pool => BUY WETH, sent USDC (usdc_delta > 0)
    if weth_delta > 0 and usdc_delta < 0:
        direction = "SELL_WETH"
        base_volume_wei = weth_delta
        quote_volume_raw = -usdc_delta
    elif weth_delta < 0 and usdc_delta > 0:
        direction = "BUY_WETH"
        base_volume_wei = -weth_delta
        quote_volume_raw = usdc_delta
    else:
        return None, f"ambiguous/nonpositive V3 amounts: weth_delta={weth_delta}, usdc_delta={usdc_delta}"

    if base_volume_wei <= 0 or quote_volume_raw <= 0:
        return None, f"nonpositive volume: base_wei={base_volume_wei}, quote_raw={quote_volume_raw}"

    base_weth = base_volume_wei / (10**WETH_DECIMALS)
    quote_usdc = quote_volume_raw / (10**USDC_DECIMALS)
    price = quote_usdc / base_weth

    spot = v3_post_swap_spot(sqrt_price_x96, token0_is_usdc=token0_is_usdc)

    swap = DecodedSwap(
        blockNumber=int(log["blockNumber"], 16) if isinstance(log["blockNumber"], str) else log["blockNumber"],
        blockHash=log["blockHash"].lower(),
        timestamp=timestamp,
        transactionHash=log["transactionHash"].lower(),
        transactionIndex=int(log["transactionIndex"], 16) if isinstance(log["transactionIndex"], str) else log["transactionIndex"],
        logIndex=int(log["logIndex"], 16) if isinstance(log["logIndex"], str) else log["logIndex"],
        poolAddress=pool_meta["address"].lower(),
        poolLabel=pool_meta["label"],
        family=pool_meta["family"],
        feeBps=pool_meta["feeBps"],
        direction=direction,
        baseVolumeWei=str(base_volume_wei),
        quoteVolumeRaw=str(quote_volume_raw),
        baseVolumeWeth=base_weth,
        quoteVolumeUsdc=quote_usdc,
        executionPrice=price,
        postSwapSpot=spot,
        sqrtPriceX96=str(sqrt_price_x96),
    )
    return swap, None


def decode_v2_swap_log(
    log: dict[str, Any],
    pool_meta: dict[str, Any],
    token0_addr: str,
    token1_addr: str,
    timestamp: str,
) -> tuple[DecodedSwap | None, str | None]:
    """Decodes a Uniswap V2 Swap event log."""
    data_hex = log["data"].removeprefix("0x")
    raw_bytes = bytes.fromhex(data_hex)
    if len(raw_bytes) < 128:
        return None, f"invalid V2 data length: {len(raw_bytes)}"

    amount0_in = int.from_bytes(raw_bytes[0:32], "big", signed=False)
    amount1_in = int.from_bytes(raw_bytes[32:64], "big", signed=False)
    amount0_out = int.from_bytes(raw_bytes[64:96], "big", signed=False)
    amount1_out = int.from_bytes(raw_bytes[96:128], "big", signed=False)

    t0 = token0_addr.lower()
    t1 = token1_addr.lower()
    weth = WETH_ADDRESS.lower()
    usdc = USDC_ADDRESS.lower()

    if {t0, t1} != {weth, usdc}:
        return None, f"wrong tokens in pool: token0={t0}, token1={t1}"

    token0_is_usdc = (t0 == usdc)

    # Check standard swap pattern: exactly one token is IN, exactly the other token is OUT
    if token0_is_usdc:
        usdc_in, usdc_out = amount0_in, amount0_out
        weth_in, weth_out = amount1_in, amount1_out
    else:
        usdc_in, usdc_out = amount1_in, amount1_out
        weth_in, weth_out = amount0_in, amount0_out

    # Valid patterns:
    # 1) Trader sells WETH: weth_in > 0, usdc_out > 0, weth_out == 0, usdc_in == 0
    # 2) Trader buys WETH: usdc_in > 0, weth_out > 0, usdc_out == 0, weth_in == 0
    if weth_in > 0 and usdc_out > 0 and weth_out == 0 and usdc_in == 0:
        direction = "SELL_WETH"
        base_volume_wei = weth_in
        quote_volume_raw = usdc_out
    elif usdc_in > 0 and weth_out > 0 and usdc_out == 0 and weth_in == 0:
        direction = "BUY_WETH"
        base_volume_wei = weth_out
        quote_volume_raw = usdc_in
    else:
        return None, (
            f"ambiguous V2 swap values: amount0In={amount0_in}, amount1In={amount1_in}, "
            f"amount0Out={amount0_out}, amount1Out={amount1_out}"
        )

    if base_volume_wei <= 0 or quote_volume_raw <= 0:
        return None, f"nonpositive V2 volume: base={base_volume_wei}, quote={quote_volume_raw}"

    base_weth = base_volume_wei / (10**WETH_DECIMALS)
    quote_usdc = quote_volume_raw / (10**USDC_DECIMALS)
    price = quote_usdc / base_weth

    swap = DecodedSwap(
        blockNumber=int(log["blockNumber"], 16) if isinstance(log["blockNumber"], str) else log["blockNumber"],
        blockHash=log["blockHash"].lower(),
        timestamp=timestamp,
        transactionHash=log["transactionHash"].lower(),
        transactionIndex=int(log["transactionIndex"], 16) if isinstance(log["transactionIndex"], str) else log["transactionIndex"],
        logIndex=int(log["logIndex"], 16) if isinstance(log["logIndex"], str) else log["logIndex"],
        poolAddress=pool_meta["address"].lower(),
        poolLabel=pool_meta["label"],
        family=pool_meta["family"],
        feeBps=pool_meta["feeBps"],
        direction=direction,
        baseVolumeWei=str(base_volume_wei),
        quoteVolumeRaw=str(quote_volume_raw),
        baseVolumeWeth=base_weth,
        quoteVolumeUsdc=quote_usdc,
        executionPrice=price,
        postSwapSpot=None,
        sqrtPriceX96=None,
    )
    return swap, None


def compute_vwap(quote_sum: float, base_sum: float) -> float | None:
    """Computes VWAP = SUM(USDC) / SUM(WETH). Empty denominator returns None."""
    if base_sum <= 0:
        return None
    return quote_sum / base_sum


def verify_pool_tokens(client: RpcClient, pools: list[dict[str, Any]], block_num: int) -> dict[str, tuple[str, str]]:
    """Verifies token0 and token1 for each pool via RPC eth_call at block_num."""
    ref = client.get_block(block_num)
    token_map: dict[str, tuple[str, str]] = {}
    for pool in pools:
        addr = pool["address"]
        # token0(): 0x0dfe1681, token1(): 0xd21220a7
        r0 = client.eth_call(addr, "0x0dfe1681", ref)
        r1 = client.eth_call(addr, "0xd21220a7", ref)
        if not r0.success or not r1.success:
            raise RuntimeError(f"Failed to query token0/token1 for pool {addr} at block {block_num}")
        t0 = abi_decode(["address"], bytes.fromhex(r0.raw[2:]))[0].lower()
        t1 = abi_decode(["address"], bytes.fromhex(r1.raw[2:]))[0].lower()
        if {t0, t1} != {WETH_ADDRESS.lower(), USDC_ADDRESS.lower()}:
            raise RuntimeError(f"Pool {addr} does not pair WETH and USDC: token0={t0}, token1={t1}")
        token_map[addr.lower()] = (t0, t1)
    return token_map


def validate_cache(
    cached_data: dict[str, Any],
    source_rows: dict[int, Any],
    expected_pools: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Validates cached data integrity and fails closed on any mismatch."""
    chain_id = cached_data.get("chainId")
    if chain_id != 1:
        raise ValueError(f"Cache chainId mismatch: expected 1, got {chain_id}")
    if cached_data.get("startBlock") != CANONICAL_START_BLOCK:
        raise ValueError(
            f"Cache startBlock mismatch: expected {CANONICAL_START_BLOCK}, got {cached_data.get('startBlock')}"
        )
    if cached_data.get("endBlock") != CANONICAL_END_BLOCK:
        raise ValueError(
            f"Cache endBlock mismatch: expected {CANONICAL_END_BLOCK}, got {cached_data.get('endBlock')}"
        )

    # Validate exact pool set in tokenMap
    token_map = cached_data.get("tokenMap", {})
    expected_pool_addrs = {p["address"].lower() for p in expected_pools}
    cached_pool_addrs = {k.lower() for k in token_map}
    if cached_pool_addrs != expected_pool_addrs:
        raise ValueError(f"Cache pool set mismatch: expected {expected_pool_addrs}, got {cached_pool_addrs}")

    # Validate token pairs in tokenMap
    validated_token_map: dict[str, tuple[str, str]] = {}
    for pool_addr, pair in token_map.items():
        addr_lower = pool_addr.lower()
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"Invalid tokenMap pair for pool {pool_addr}: {pair}")
        t0, t1 = pair[0].lower(), pair[1].lower()
        if {t0, t1} != {WETH_ADDRESS.lower(), USDC_ADDRESS.lower()}:
            raise ValueError(f"Pool {pool_addr} does not pair WETH and USDC: token0={t0}, token1={t1}")
        validated_token_map[addr_lower] = (t0, t1)

    # Validate block hashes match all requested canonical rows
    cached_hashes = cached_data.get("blockHashes", {})
    for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1):
        expected_hash = source_rows[b]["blockHash"].lower()
        cached_hash = cached_hashes.get(str(b), "").lower()
        if cached_hash != expected_hash:
            raise ValueError(
                f"Cache block hash mismatch for block {b}: expected {expected_hash}, got {cached_hash}"
            )

    return validated_token_map


def load_canonical_sources() -> dict[str, Any]:
    sources_path = ROOT / "frontend/public/five-crash-liquidity/crash-5/sources.json"
    if not sources_path.is_file():
        raise FileNotFoundError(f"Missing sources file at {sources_path}")
    with open(sources_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_interactive_html(
    data_payload: dict[str, Any],
    trades: list[dict[str, Any]],
    out_html: Path,
) -> None:
    """Generates a standalone, dependency-free interactive HTML visualization."""
    payload_json = json.dumps(data_payload)
    trades_json = json.dumps(trades)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>October 10 2025 Swap Events (21:30:23Z - 21:40:23Z)</title>
<style>
  :root {{
    --bg-dark: #0f172a;
    --card-bg: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
    --buy-color: #10b981;
    --sell-color: #f43f5e;
    --chainlink-color: #3b82f6;
    --aggregate-color: #f59e0b;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: var(--bg-dark);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .container {{ max-width: 1300px; margin: 0 auto; }}
  header {{
    margin-bottom: 24px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #fff; }}
  .meta {{ color: var(--text-muted); font-size: 13px; display: flex; gap: 16px; flex-wrap: wrap; }}
  .btn-group {{ display: flex; gap: 8px; margin-top: 12px; }}
  .btn {{
    background: #334155;
    color: #fff;
    padding: 6px 12px;
    border-radius: 4px;
    text-decoration: none;
    font-size: 12px;
    border: 1px solid var(--border);
    cursor: pointer;
  }}
  .btn:hover {{ background: #475569; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }}
  .card h3 {{ margin: 0 0 8px 0; font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .val {{ font-size: 22px; font-weight: 700; color: #fff; }}
  .card .sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .caveat-box {{
    background: rgba(245, 158, 11, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.3);
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 24px;
    font-size: 12px;
    color: #fcd34d;
  }}
  .caveat-box strong {{ color: #fbbf24; }}
  .chart-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
    position: relative;
  }}
  .chart-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .legend {{ display: flex; gap: 16px; font-size: 12px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; user-select: none; }}
  .dot-legend {{ width: 10px; height: 10px; border-radius: 50%; }}
  .line-legend {{ width: 16px; height: 3px; border-radius: 1px; }}
  svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
  .tooltip {{
    position: absolute;
    background: #090d16;
    border: 1px solid var(--primary);
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 12px;
    color: #fff;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s ease-in-out;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    z-index: 50;
    max-width: 320px;
  }}
  .table-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
    max-height: 480px;
    margin-bottom: 24px;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: #182234; color: var(--text-muted); font-weight: 600; position: sticky; top: 0; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }}
  .badge-buy {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }}
  .badge-sell {{ background: rgba(244, 63, 94, 0.2); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.4); }}
  code {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>October 10 2025 Historical Swap-Event Experiment</h1>
    <div class="meta">
      <span><strong>Scope:</strong> Blocks 23550020 &rarr; 23550069 (50 canonical blocks)</span>
      <span><strong>Window:</strong> 2025-10-10T21:30:23Z &rarr; 2025-10-10T21:40:23Z</span>
      <span><strong>Worst 1WETH Gap Block:</strong> #23550044</span>
      <span><strong>Total Swap Executions:</strong> {len(trades)}</span>
    </div>
    <div class="btn-group">
      <a class="btn" href="data.json" download>Download data.json</a>
      <a class="btn" href="trades.csv" download>Download trades.csv</a>
    </div>
  </header>

  <div class="caveat-box">
    <strong>Benchmark & Boundary Notice:</strong>
    Chainlink is a <em>block-end reference benchmark</em>, not an intrablock oracle necessarily visible to transactions at execution time.
    No intrablock causal ordering with oracle updates is asserted; observed price differences represent block-end benchmark gaps only.
    Pool executions represent individual pool swaps (including arbitrage/MEV rebalancing legs), not user net-routed flows.
    Uniswap V4 and Fluid DEX are excluded from this first direct-pool experiment due to singleton/accounting semantics.
  </div>

  <div class="grid" id="summary-cards">
    <!-- Populated by JS -->
  </div>

  <div class="chart-box">
    <div class="chart-header">
      <div><strong>Execution Prices & Reference Trajectory (USDC per WETH)</strong></div>
      <div class="legend">
        <div class="legend-item"><div class="line-legend" style="background: var(--chainlink-color)"></div>Chainlink (Block-End Benchmark)</div>
        <div class="legend-item"><div class="line-legend" style="background: var(--aggregate-color)"></div>1 WETH Simulated DEX Aggregate</div>
        <div class="legend-item"><div class="line-legend" style="background: var(--buy-color); border-bottom: 1px dashed var(--buy-color);"></div>Buy VWAP (Non-Bridging)</div>
        <div class="legend-item"><div class="line-legend" style="background: var(--sell-color); border-bottom: 1px dashed var(--sell-color);"></div>Sell VWAP (Non-Bridging)</div>
        <div class="legend-item"><div class="dot-legend" style="background: var(--buy-color)"></div>Buy WETH Execution</div>
        <div class="legend-item"><div class="dot-legend" style="background: var(--sell-color)"></div>Sell WETH Execution</div>
      </div>
    </div>
    <div id="chart-container" style="position: relative; width: 100%;">
      <svg id="svg-chart" viewBox="0 0 1200 520"></svg>
      <div id="tooltip" class="tooltip"></div>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 1fr 1fr;">
    <div class="card">
      <h3>Worst-Gap Block #23550044 Comparison</h3>
      <div id="worst-gap-details" style="font-size: 13px; margin-top: 8px;">
        <!-- Populated by JS -->
      </div>
    </div>
    <div class="card">
      <h3>Pool Activity Breakdown</h3>
      <div id="pool-breakdown-details" style="font-size: 13px; margin-top: 8px;">
        <!-- Populated by JS -->
      </div>
    </div>
  </div>

  <div class="table-box">
    <h3 style="padding: 12px 12px 0 12px; margin: 0; font-size: 14px;">Recent Executions (Sample)</h3>
    <table id="trades-table">
      <thead>
        <tr>
          <th>Block</th>
          <th>Intrablock</th>
          <th>Timestamp (UTC)</th>
          <th>Pool</th>
          <th>Direction</th>
          <th>Base (WETH)</th>
          <th>Quote (USDC)</th>
          <th>Execution Price</th>
          <th>Post-Swap Spot (V3)</th>
          <th>Tx Hash</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<script>
const payload = {payload_json};
const trades = {trades_json};

// Render Summary Cards
const summaryCards = document.getElementById('summary-cards');
summaryCards.innerHTML = `
  <div class="card">
    <h3>Total Volume</h3>
    <div class="val">${{payload.totals.combined.wethVolume.toFixed(2)}} WETH</div>
    <div class="sub">$${{payload.totals.combined.usdcVolume.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}} USDC across ${{payload.totals.combined.count}} trades</div>
  </div>
  <div class="card">
    <h3>Buy WETH VWAP</h3>
    <div class="val" style="color: var(--buy-color);">${{payload.totals.buy.vwap ? '$' + payload.totals.buy.vwap.toFixed(2) : 'N/A'}}</div>
    <div class="sub">${{payload.totals.buy.wethVolume.toFixed(2)}} WETH (${{payload.totals.buy.count}} trades)</div>
  </div>
  <div class="card">
    <h3>Sell WETH VWAP</h3>
    <div class="val" style="color: var(--sell-color);">${{payload.totals.sell.vwap ? '$' + payload.totals.sell.vwap.toFixed(2) : 'N/A'}}</div>
    <div class="sub">${{payload.totals.sell.wethVolume.toFixed(2)}} WETH (${{payload.totals.sell.count}} trades)</div>
  </div>
  <div class="card">
    <h3>Combined Mixed VWAP</h3>
    <div class="val">${{payload.totals.combined.vwap ? '$' + payload.totals.combined.vwap.toFixed(2) : 'N/A'}}</div>
    <div class="sub">Buy & Sell combined (mixed-direction)</div>
  </div>
`;

// Render Worst-Gap Block Details
const wg = payload.worstGapBlockAnalysis;
const wgDiv = document.getElementById('worst-gap-details');
wgDiv.innerHTML = `
  <p><strong>Block:</strong> #${{wg.block}} (${{wg.timestamp}})</p>
  <p><strong>Chainlink Benchmark:</strong> $${{wg.chainlinkPrice.toFixed(2)}}</p>
  <p><strong>Simulated 1 WETH Aggregate:</strong> $${{wg.simulatedAggregate1Weth.toFixed(2)}} (Benchmark Gap: -${{((wg.chainlinkPrice - wg.simulatedAggregate1Weth)/wg.chainlinkPrice * 10000).toFixed(1)}} bps)</p>
  <p><strong>Actual Executions in Block:</strong> ${{wg.executionCount}} (${{wg.buyCount}} buys, ${{wg.sellCount}} sells)</p>
  <p><strong>Actual Buy VWAP:</strong> ${{wg.actualBuyVwap ? '$' + wg.actualBuyVwap.toFixed(2) : 'None'}}</p>
  <p><strong>Actual Sell VWAP:</strong> ${{wg.actualSellVwap ? '$' + wg.actualSellVwap.toFixed(2) : 'None'}}</p>
`;

// Render Pool Breakdown
const poolDiv = document.getElementById('pool-breakdown-details');
let poolHtml = '<table style="width:100%; font-size:12px;"><tr><th>Pool</th><th>Trades</th><th>WETH Vol</th><th>Buy VWAP</th><th>Sell VWAP</th></tr>';
for (const p of Object.values(payload.perPool)) {{
  poolHtml += `<tr>
    <td><strong>${{p.label}}</strong></td>
    <td>${{p.total.count}}</td>
    <td>${{p.total.wethVolume.toFixed(1)}}</td>
    <td style="color:var(--buy-color);">${{p.buy.vwap ? '$' + p.buy.vwap.toFixed(1) : '-'}}</td>
    <td style="color:var(--sell-color);">${{p.sell.vwap ? '$' + p.sell.vwap.toFixed(1) : '-'}}</td>
  </tr>`;
}}
poolHtml += '</table>';
poolDiv.innerHTML = poolHtml;

// Render Chart
const svg = document.getElementById('svg-chart');
const tooltip = document.getElementById('tooltip');

const W = 1200, H = 520;
const padL = 70, padR = 30, padT = 30, padB = 60;
const plotW = W - padL - padR;
const plotH = H - padT - padB;

const minBlock = 23550020;
const maxBlock = 23550069;
const blockSpan = maxBlock - minBlock;

// Price range
let minPrice = 3200, maxPrice = 3850;

function xBlock(b) {{ return padL + ((b - minBlock) / blockSpan) * plotW; }}
function yPrice(p) {{ return padT + ((maxPrice - p) / (maxPrice - minPrice)) * plotH; }}

let svgContent = '';

// Grid lines
for (let p = 3200; p <= 3850; p += 100) {{
  const y = yPrice(p);
  svgContent += `<line x1="${{padL}}" y1="${{y}}" x2="${{W - padR}}" y2="${{y}}" stroke="#334155" stroke-dasharray="3,3" stroke-width="0.7"/>`;
  svgContent += `<text x="${{padL - 10}}" y="${{y + 4}}" fill="#94a3b8" font-size="11" text-anchor="end">$${{p}}</text>`;
}}

// Vertical block grid & labels
for (let b = minBlock; b <= maxBlock; b += 5) {{
  const x = xBlock(b);
  const row = payload.perBlock.find(r => r.block === b);
  const timeStr = row ? row.timestamp.substring(11, 19) : '';
  svgContent += `<line x1="${{x}}" y1="${{padT}}" x2="${{x}}" y2="${{padT + plotH}}" stroke="#334155" stroke-dasharray="2,2" stroke-width="0.5"/>`;
  svgContent += `<text x="${{x}}" y="${{padT + plotH + 20}}" fill="#cbd5e1" font-size="11" text-anchor="middle">#${{b}}</text>`;
  svgContent += `<text x="${{x}}" y="${{padT + plotH + 34}}" fill="#64748b" font-size="9" text-anchor="middle">${{timeStr}}</text>`;
}}

// Chainlink line (contiguous line)
let clPoints = [];
for (const b of payload.perBlock) {{
  if (b.chainlink) {{
    clPoints.push(`${{xBlock(b.block).toFixed(1)}},${{yPrice(b.chainlink).toFixed(1)}}`);
  }}
}}
svgContent += `<polyline fill="none" stroke="var(--chainlink-color)" stroke-width="2" points="${{clPoints.join(' ')}}"/>`;

// Aggregate line (contiguous line)
let aggPoints = [];
for (const b of payload.perBlock) {{
  if (b.aggregateWeth1) {{
    aggPoints.push(`${{xBlock(b.block).toFixed(1)}},${{yPrice(b.aggregateWeth1).toFixed(1)}}`);
  }}
}}
svgContent += `<polyline fill="none" stroke="var(--aggregate-color)" stroke-width="2" stroke-dasharray="4,2" points="${{aggPoints.join(' ')}}"/>`;

// Broken path builder that strictly breaks on null/missing blocks
function buildBrokenPath(perBlock, getVal, strokeColor, dashArray, width) {{
  let segs = [];
  let cur = [];
  for (let i = 0; i < perBlock.length; i++) {{
    const b = perBlock[i];
    const val = getVal(b);
    if (val !== null && val !== undefined) {{
      cur.push({{ x: xBlock(b.block), y: yPrice(val) }});
    }} else {{
      if (cur.length > 0) {{
        segs.push(cur);
        cur = [];
      }}
    }}
  }}
  if (cur.length > 0) {{
    segs.push(cur);
  }}
  let out = '';
  for (const seg of segs) {{
    if (seg.length >= 2) {{
      const d = 'M ' + seg.map(p => `${{p.x.toFixed(1)}},${{p.y.toFixed(1)}}`).join(' L ');
      out += `<path fill="none" stroke="${{strokeColor}}" stroke-width="${{width}}" stroke-dasharray="${{dashArray}}" d="${{d}}"/>`;
    }} else if (seg.length === 1) {{
      const p = seg[0];
      out += `<line x1="${{(p.x - 3).toFixed(1)}}" y1="${{p.y.toFixed(1)}}" x2="${{(p.x + 3).toFixed(1)}}" y2="${{p.y.toFixed(1)}}" stroke="${{strokeColor}}" stroke-width="${{width + 1}}"/>`;
    }}
  }}
  return out;
}}

// Non-bridging Buy & Sell VWAP lines
svgContent += buildBrokenPath(payload.perBlock, b => b.buy.vwap, 'var(--buy-color)', '3,3', 1.8);
svgContent += buildBrokenPath(payload.perBlock, b => b.sell.vwap, 'var(--sell-color)', '3,3', 1.8);

// Execution dots
trades.forEach((t, i) => {{
  const totalInBlock = trades.filter(tr => tr.blockNumber === t.blockNumber).length;
  const intraOffset = totalInBlock > 1 ? (t.intrablockIndex - (totalInBlock - 1) / 2) * 3.5 : 0;
  const cx = xBlock(t.blockNumber) + intraOffset;
  const cy = yPrice(t.executionPrice);

  const radius = Math.max(3.5, Math.min(14, 3.5 + Math.sqrt(t.baseVolumeWeth) * 2.2));
  const fill = t.direction === 'BUY_WETH' ? 'var(--buy-color)' : 'var(--sell-color)';

  svgContent += `<circle class="trade-dot" data-idx="${{i}}" cx="${{cx.toFixed(1)}}" cy="${{cy.toFixed(1)}}" r="${{radius.toFixed(1)}}" fill="${{fill}}" opacity="0.8" stroke="#fff" stroke-width="0.8" style="cursor: pointer;"/>`;
}});

svg.innerHTML = svgContent;

// Tooltip interactivity
const dots = document.querySelectorAll('.trade-dot');
dots.forEach(dot => {{
  dot.addEventListener('mouseenter', (e) => {{
    const idx = parseInt(e.target.getAttribute('data-idx'), 10);
    const t = trades[idx];
    const rect = svg.getBoundingClientRect();
    const cx = parseFloat(e.target.getAttribute('cx')) * (rect.width / W);
    const cy = parseFloat(e.target.getAttribute('cy')) * (rect.height / H);

    tooltip.style.opacity = '1';
    tooltip.style.left = `${{cx + 12}}px`;
    tooltip.style.top = `${{cy - 20}}px`;
    tooltip.innerHTML = `
      <div style="font-weight: 700; color: ${{t.direction === 'BUY_WETH' ? 'var(--buy-color)' : 'var(--sell-color)'}};">
        ${{t.direction === 'BUY_WETH' ? 'BUY WETH' : 'SELL WETH'}}
      </div>
      <div><strong>Price:</strong> $${{t.executionPrice.toFixed(2)}} USDC</div>
      <div><strong>Size:</strong> ${{t.baseVolumeWeth.toFixed(4)}} WETH ($${{t.quoteVolumeUsdc.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}})</div>
      <div><strong>Pool:</strong> ${{t.poolLabel}}</div>
      <div><strong>Block:</strong> #${{t.blockNumber}} (Ordinal #${{t.intrablockIndex + 1}})</div>
      <div><strong>Log Index:</strong> ${{t.logIndex}} &bull; <strong>Tx Index:</strong> ${{t.transactionIndex}}</div>
      ${{t.postSwapSpot ? `<div><strong>Post-Swap Spot:</strong> $${{t.postSwapSpot.toFixed(2)}}</div>` : ''}}
      <div style="margin-top:4px; font-size:10px; color:#94a3b8;"><code>${{t.transactionHash.substring(0, 14)}}...</code></div>
    `;
  }});
  dot.addEventListener('mouseleave', () => {{
    tooltip.style.opacity = '0';
  }});
}});

// Populate Trades Sample Table
const tbody = document.querySelector('#trades-table tbody');
let tableRows = '';
trades.slice(0, 50).forEach(t => {{
  tableRows += `<tr>
    <td>#${{t.blockNumber}}</td>
    <td>#${{t.intrablockIndex + 1}}</td>
    <td>${{t.timestamp.substring(11, 19)}}</td>
    <td>${{t.poolLabel}}</td>
    <td><span class="badge ${{t.direction === 'BUY_WETH' ? 'badge-buy' : 'badge-sell'}}">${{t.direction}}</span></td>
    <td>${{t.baseVolumeWeth.toFixed(4)}}</td>
    <td>$${{t.quoteVolumeUsdc.toFixed(2)}}</td>
    <td>$${{t.executionPrice.toFixed(2)}}</td>
    <td>${{t.postSwapSpot ? '$' + t.postSwapSpot.toFixed(2) : 'N/A'}}</td>
    <td><code>${{t.transactionHash.substring(0, 10)}}...</code></td>
  </tr>`;
}});
tbody.innerHTML = tableRows;
</script>
</body>
</html>
"""
    atomic_write_text(out_html, html_content)


def run_experiment(
    force_refresh: bool = False,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Runs the full historical swap-event experiment and outputs artifacts."""
    start_time = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)

    cache_raw_logs_path = OUTPUT_DIR / "raw_logs.json"
    cache_reused = False

    canonical_sources = load_canonical_sources()
    source_rows = {r["block"]: r for r in canonical_sources["rows"]}

    # Verify canonical block bounds
    for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1):
        if b not in source_rows:
            raise ValueError(f"Missing canonical block {b} in sources.json")

    # Step 1: Raw Log Collection or Cache Reuse
    raw_logs: list[dict[str, Any]]
    cached_block_hashes: dict[str, str] = {}
    token_map: dict[str, tuple[str, str]]

    if not force_refresh and cache_raw_logs_path.is_file():
        print(f"Loading raw logs from existing cache: {cache_raw_logs_path}")
        with open(cache_raw_logs_path, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
        token_map = validate_cache(cached_data, source_rows, POOLS_CATALOG)
        raw_logs = cached_data["logs"]
        cached_block_hashes = cached_data.get("blockHashes", {})
        cache_reused = True
    else:
        print(f"Querying RPC for logs across blocks {CANONICAL_START_BLOCK}..{CANONICAL_END_BLOCK}...")
        if client is None:
            client = RpcClient()

        # Check chain id
        chain_id = client.chain_id()
        if chain_id != 1:
            raise ValueError(f"RpcClient chainId mismatch: expected 1 (Ethereum mainnet), got {chain_id}")

        # Token verification across all 6 pools
        token_map = verify_pool_tokens(client, POOLS_CATALOG, CANONICAL_START_BLOCK)
        expected_pool_addrs = {p["address"].lower() for p in POOLS_CATALOG}
        if {k.lower() for k in token_map} != expected_pool_addrs:
            raise ValueError("Token verification did not return all expected pools")
        for addr, (t0, t1) in token_map.items():
            if {t0.lower(), t1.lower()} != {WETH_ADDRESS.lower(), USDC_ADDRESS.lower()}:
                raise ValueError(f"Pool {addr} does not pair WETH and USDC: ({t0}, {t1})")

        # Build address list and topic filter
        pool_addresses = [p["address"] for p in POOLS_CATALOG]
        filter_params = [{
            "address": pool_addresses,
            "topics": [[V3_SWAP_TOPIC, V2_SWAP_TOPIC]],
            "fromBlock": hex(CANONICAL_START_BLOCK),
            "toBlock": hex(CANONICAL_END_BLOCK),
        }]

        raw_logs = client._rpc("eth_getLogs", filter_params)
        print(f"RPC returned {len(raw_logs)} raw logs.")

        # Persist block hashes for verification
        for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1):
            cached_block_hashes[str(b)] = source_rows[b]["blockHash"].lower()

        atomic_write_json(
            cache_raw_logs_path,
            {
                "chainId": chain_id,
                "startBlock": CANONICAL_START_BLOCK,
                "endBlock": CANONICAL_END_BLOCK,
                "tokenMap": token_map,
                "blockHashes": cached_block_hashes,
                "logs": raw_logs,
                "fetchedAt": datetime.now(UTC).isoformat(),
            },
        )

    # Step 2: Deduplication & Strict Ordering
    # Identity: (chain, blockhash, txhash, logindex)
    seen_identities: set[tuple[str, str, int]] = set()
    deduped_logs: list[dict[str, Any]] = []

    for log in raw_logs:
        bhash = log["blockHash"].lower()
        txhash = log["transactionHash"].lower()
        log_idx = int(log["logIndex"], 16) if isinstance(log["logIndex"], str) else log["logIndex"]
        identity = (bhash, txhash, log_idx)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        deduped_logs.append(log)

    # Sort strictly: blockNumber, transactionIndex, logIndex
    def sort_key(l: dict[str, Any]) -> tuple[int, int, int]:
        b = int(l["blockNumber"], 16) if isinstance(l["blockNumber"], str) else l["blockNumber"]
        t = int(l["transactionIndex"], 16) if isinstance(l["transactionIndex"], str) else l["transactionIndex"]
        idx = int(l["logIndex"], 16) if isinstance(l["logIndex"], str) else l["logIndex"]
        return (b, t, idx)

    deduped_logs.sort(key=sort_key)

    # Step 3: Decoding & Fail-Closed Validation
    pool_meta_by_addr = {p["address"].lower(): p for p in POOLS_CATALOG}
    decoded_swaps: list[DecodedSwap] = []
    rejected_logs: list[dict[str, Any]] = []

    for log in deduped_logs:
        # Check removed flag
        if log.get("removed", False) is True:
            rejected_logs.append({"log": log, "reason": "log has removed=True"})
            continue

        pool_addr = log["address"].lower()
        if pool_addr not in pool_meta_by_addr:
            rejected_logs.append({"log": log, "reason": f"unknown pool address {pool_addr}"})
            continue

        pool_meta = pool_meta_by_addr[pool_addr]
        b_num = int(log["blockNumber"], 16) if isinstance(log["blockNumber"], str) else log["blockNumber"]

        # Interval bounds check (exact 50 blocks)
        if not (CANONICAL_START_BLOCK <= b_num <= CANONICAL_END_BLOCK):
            rejected_logs.append({
                "log": log,
                "reason": f"block {b_num} out of canonical interval [{CANONICAL_START_BLOCK}..{CANONICAL_END_BLOCK}]",
            })
            continue

        # Check block hash equals canonical header hash
        expected_block_hash = source_rows[b_num]["blockHash"].lower()
        log_block_hash = log["blockHash"].lower()
        if log_block_hash != expected_block_hash:
            rejected_logs.append({
                "log": log,
                "reason": f"blockHash mismatch for block {b_num}: expected {expected_block_hash}, got {log_block_hash}",
            })
            continue

        timestamp = source_rows[b_num]["timestamp"]
        if pool_addr not in token_map:
            rejected_logs.append({"log": log, "reason": f"pool {pool_addr} missing from verified token map"})
            continue
        t0, t1 = token_map[pool_addr]

        topics = [t.lower() for t in log.get("topics", [])]
        if not topics:
            rejected_logs.append({"log": log, "reason": "empty topics"})
            continue

        top0 = topics[0]
        # Check topic matches pool family
        family = pool_meta["family"]
        if family in ("uniswap_v3", "pancake_v3"):
            if top0 != V3_SWAP_TOPIC.lower():
                rejected_logs.append({"log": log, "reason": f"family {family} emitted non-V3 topic: {top0}"})
                continue
            swap, err = decode_v3_swap_log(log, pool_meta, t0, t1, timestamp)
        elif family == "uniswap_v2":
            if top0 != V2_SWAP_TOPIC.lower():
                rejected_logs.append({"log": log, "reason": f"family {family} emitted non-V2 topic: {top0}"})
                continue
            swap, err = decode_v2_swap_log(log, pool_meta, t0, t1, timestamp)
        else:
            swap, err = None, f"unsupported family: {family}"

        if err or swap is None:
            rejected_logs.append({"log": log, "reason": err or "decoding returned None"})
        else:
            decoded_swaps.append(swap)

    # Assign intrablock ordering (ordinal 0, 1, 2... within each block)
    block_trade_counts: dict[int, int] = {}
    for swap in decoded_swaps:
        ord_idx = block_trade_counts.get(swap.blockNumber, 0)
        swap.intrablockIndex = ord_idx
        block_trade_counts[swap.blockNumber] = ord_idx + 1

    # Step 4: Verification Proofs
    first_log = deduped_logs[0] if deduped_logs else None
    last_log = deduped_logs[-1] if deduped_logs else None

    first_block_header_hash = source_rows[CANONICAL_START_BLOCK]["blockHash"].lower()
    last_block_header_hash = source_rows[CANONICAL_END_BLOCK]["blockHash"].lower()

    first_log_hash = first_log["blockHash"].lower() if first_log else None
    last_log_hash = last_log["blockHash"].lower() if last_log else None

    first_hash_match = (first_log_hash == first_block_header_hash)
    last_hash_match = (last_log_hash == last_block_header_hash)

    # Step 5: Metrics Aggregation with Exact Integer Summation
    def sum_direction(swaps_list: list[DecodedSwap], direction: str) -> dict[str, Any]:
        filtered = [s for s in swaps_list if s.direction == direction]
        count = len(filtered)
        base_wei_sum = sum(int(s.baseVolumeWei) for s in filtered)
        quote_raw_sum = sum(int(s.quoteVolumeRaw) for s in filtered)
        weth_vol = base_wei_sum / (10**WETH_DECIMALS)
        usdc_vol = quote_raw_sum / (10**USDC_DECIMALS)
        vwap = (quote_raw_sum * 10**12) / base_wei_sum if base_wei_sum > 0 else None
        return {
            "count": count,
            "baseVolumeWei": str(base_wei_sum),
            "quoteVolumeRaw": str(quote_raw_sum),
            "wethVolume": weth_vol,
            "usdcVolume": usdc_vol,
            "vwap": vwap,
        }

    buy_metrics = sum_direction(decoded_swaps, "BUY_WETH")
    sell_metrics = sum_direction(decoded_swaps, "SELL_WETH")
    total_count = len(decoded_swaps)
    total_base_wei = int(buy_metrics["baseVolumeWei"]) + int(sell_metrics["baseVolumeWei"])
    total_quote_raw = int(buy_metrics["quoteVolumeRaw"]) + int(sell_metrics["quoteVolumeRaw"])
    total_weth = total_base_wei / (10**WETH_DECIMALS)
    total_usdc = total_quote_raw / (10**USDC_DECIMALS)
    combined_vwap = (total_quote_raw * 10**12) / total_base_wei if total_base_wei > 0 else None
    combined_metrics = {
        "count": total_count,
        "baseVolumeWei": str(total_base_wei),
        "quoteVolumeRaw": str(total_quote_raw),
        "wethVolume": total_weth,
        "usdcVolume": total_usdc,
        "vwap": combined_vwap,
        "note": "Mixed-direction volume-weighted average price",
    }

    # Per-pool metrics
    per_pool_metrics: dict[str, Any] = {}
    for p in POOLS_CATALOG:
        addr = p["address"].lower()
        p_swaps = [s for s in decoded_swaps if s.poolAddress == addr]
        p_buy = sum_direction(p_swaps, "BUY_WETH")
        p_sell = sum_direction(p_swaps, "SELL_WETH")
        p_base_wei = int(p_buy["baseVolumeWei"]) + int(p_sell["baseVolumeWei"])
        p_quote_raw = int(p_buy["quoteVolumeRaw"]) + int(p_sell["quoteVolumeRaw"])
        p_weth = p_base_wei / (10**WETH_DECIMALS)
        p_usdc = p_quote_raw / (10**USDC_DECIMALS)
        p_comb_vwap = (p_quote_raw * 10**12) / p_base_wei if p_base_wei > 0 else None
        per_pool_metrics[addr] = {
            "address": addr,
            "label": p["label"],
            "family": p["family"],
            "feeBps": p["feeBps"],
            "buy": p_buy,
            "sell": p_sell,
            "total": {
                "count": len(p_swaps),
                "baseVolumeWei": str(p_base_wei),
                "quoteVolumeRaw": str(p_quote_raw),
                "wethVolume": p_weth,
                "usdcVolume": p_usdc,
                "vwap": p_comb_vwap,
            },
        }

    # Per-block buckets (all 50 canonical blocks; empty buckets null, no fills)
    per_block_buckets: list[dict[str, Any]] = []
    for b in range(CANONICAL_START_BLOCK, CANONICAL_END_BLOCK + 1):
        b_swaps = [s for s in decoded_swaps if s.blockNumber == b]
        b_buy = sum_direction(b_swaps, "BUY_WETH")
        b_sell = sum_direction(b_swaps, "SELL_WETH")
        b_base_wei = int(b_buy["baseVolumeWei"]) + int(b_sell["baseVolumeWei"])
        b_quote_raw = int(b_buy["quoteVolumeRaw"]) + int(b_sell["quoteVolumeRaw"])
        b_weth = b_base_wei / (10**WETH_DECIMALS)
        b_usdc = b_quote_raw / (10**USDC_DECIMALS)
        b_comb_vwap = (b_quote_raw * 10**12) / b_base_wei if b_base_wei > 0 else None

        row = source_rows[b]
        agg_weth_1 = row.get("aggregates", {}).get("WETH", {}).get("1")
        per_block_buckets.append({
            "block": b,
            "timestamp": row["timestamp"],
            "blockHash": row["blockHash"],
            "chainlink": row.get("chainlink"),
            "aggregateWeth1": agg_weth_1,
            "buy": b_buy,
            "sell": b_sell,
            "combined": {
                "count": len(b_swaps),
                "baseVolumeWei": str(b_base_wei),
                "quoteVolumeRaw": str(b_quote_raw),
                "wethVolume": b_weth,
                "usdcVolume": b_usdc,
                "vwap": b_comb_vwap,
            },
        })

    # Minute buckets
    minute_groups: dict[str, list[DecodedSwap]] = {}
    for s in decoded_swaps:
        m_str = s.timestamp[:16]
        minute_groups.setdefault(m_str, []).append(s)

    all_minutes = [
        "2025-10-10T21:30", "2025-10-10T21:31", "2025-10-10T21:32", "2025-10-10T21:33", "2025-10-10T21:34",
        "2025-10-10T21:35", "2025-10-10T21:36", "2025-10-10T21:37", "2025-10-10T21:38", "2025-10-10T21:39",
        "2025-10-10T21:40"
    ]
    per_minute_buckets: list[dict[str, Any]] = []
    for m in all_minutes:
        m_swaps = minute_groups.get(m, [])
        m_buy = sum_direction(m_swaps, "BUY_WETH")
        m_sell = sum_direction(m_swaps, "SELL_WETH")
        m_base_wei = int(m_buy["baseVolumeWei"]) + int(m_sell["baseVolumeWei"])
        m_quote_raw = int(m_buy["quoteVolumeRaw"]) + int(m_sell["quoteVolumeRaw"])
        m_weth = m_base_wei / (10**WETH_DECIMALS)
        m_usdc = m_quote_raw / (10**USDC_DECIMALS)
        m_comb_vwap = (m_quote_raw * 10**12) / m_base_wei if m_base_wei > 0 else None
        per_minute_buckets.append({
            "minute": m,
            "buy": m_buy,
            "sell": m_sell,
            "combined": {
                "count": len(m_swaps),
                "baseVolumeWei": str(m_base_wei),
                "quoteVolumeRaw": str(m_quote_raw),
                "wethVolume": m_weth,
                "usdcVolume": m_usdc,
                "vwap": m_comb_vwap,
            },
        })

    # Size bins: <1, 1-10, 10-100, >=100 WETH
    bins_def = [
        ("< 1 WETH", lambda w: w < 1.0),
        ("1 - 10 WETH", lambda w: 1.0 <= w < 10.0),
        ("10 - 100 WETH", lambda w: 10.0 <= w < 100.0),
        (">= 100 WETH", lambda w: w >= 100.0),
    ]
    size_bins_metrics: list[dict[str, Any]] = []
    for label, pred in bins_def:
        b_swaps = [s for s in decoded_swaps if pred(s.baseVolumeWeth)]
        b_buy = sum_direction(b_swaps, "BUY_WETH")
        b_sell = sum_direction(b_swaps, "SELL_WETH")
        b_base_wei = int(b_buy["baseVolumeWei"]) + int(b_sell["baseVolumeWei"])
        b_quote_raw = int(b_buy["quoteVolumeRaw"]) + int(b_sell["quoteVolumeRaw"])
        b_weth = b_base_wei / (10**WETH_DECIMALS)
        b_usdc = b_quote_raw / (10**USDC_DECIMALS)
        size_bins_metrics.append({
            "bin": label,
            "count": len(b_swaps),
            "baseVolumeWei": str(b_base_wei),
            "quoteVolumeRaw": str(b_quote_raw),
            "wethVolume": b_weth,
            "usdcVolume": b_usdc,
            "combinedVwap": (b_quote_raw * 10**12) / b_base_wei if b_base_wei > 0 else None,
            "buy": b_buy,
            "sell": b_sell,
        })

    # Worst-gap block analysis (23550044)
    wg_swaps = [s for s in decoded_swaps if s.blockNumber == WORST_GAP_BLOCK]
    wg_buy = sum_direction(wg_swaps, "BUY_WETH")
    wg_sell = sum_direction(wg_swaps, "SELL_WETH")
    wg_row = source_rows[WORST_GAP_BLOCK]

    worst_gap_analysis = {
        "block": WORST_GAP_BLOCK,
        "timestamp": wg_row["timestamp"],
        "chainlinkPrice": wg_row.get("chainlink"),
        "simulatedAggregate1Weth": wg_row.get("aggregates", {}).get("WETH", {}).get("1"),
        "executionCount": len(wg_swaps),
        "buy": wg_buy,
        "sell": wg_sell,
        "executions": [asdict(s) for s in wg_swaps],
    }

    # Step 6: Assemble Full Payload & Evidence
    trades_dicts = [asdict(s) for s in decoded_swaps]

    full_payload = {
        "metadata": {
            "title": "October 10 2025 Bounded Swap-Event Experiment",
            "startBlock": CANONICAL_START_BLOCK,
            "endBlock": CANONICAL_END_BLOCK,
            "startUtc": CANONICAL_START_UTC,
            "endUtc": CANONICAL_END_UTC,
            "canonicalBlockCount": 50,
            "worstGapBlock": WORST_GAP_BLOCK,
            "cacheReused": cache_reused,
            "generatedAt": datetime.now(UTC).isoformat(),
            "executionTimeSeconds": round(time.perf_counter() - start_time, 4),
        },
        "coverageAndExclusions": {
            "includedPools": [
                {"address": p["address"], "label": p["label"], "family": p["family"], "feeBps": p["feeBps"]}
                for p in POOLS_CATALOG
            ],
            "excludedFamilies": [
                {
                    "family": "uniswap_v4",
                    "reason": "Singleton pool manager, flash accounting, and distinct Swap event semantics.",
                },
                {
                    "family": "fluid_dex",
                    "reason": "Internal debt/collateral liquidity architecture and distinct event schema.",
                },
            ],
            "excludedAssets": [
                {"asset": "native_eth", "reason": "Only direct canonical WETH/USDC pairs analyzed in this phase."}
            ],
            "interpretationNotes": [
                "Pool legs reflect direct pool execution swaps, not reconstructed net routed user intents.",
                "Arbitrage and MEV rebalance transactions are included in pool volumes.",
                "Chainlink is a block-end benchmark, not necessarily visible to transactions at execution time.",
            ],
        },
        "verification": {
            "firstBlockHeaderHash": first_block_header_hash,
            "firstLogBlockHash": first_log_hash,
            "firstHashMatch": first_hash_match,
            "lastBlockHeaderHash": last_block_header_hash,
            "lastLogBlockHash": last_log_hash,
            "lastHashMatch": last_hash_match,
            "totalRawLogs": len(raw_logs),
            "dedupedLogs": len(deduped_logs),
            "validDecodedSwaps": len(decoded_swaps),
            "rejectedLogsCount": len(rejected_logs),
        },
        "totals": {
            "buy": buy_metrics,
            "sell": sell_metrics,
            "combined": combined_metrics,
        },
        "worstGapBlockAnalysis": worst_gap_analysis,
        "perPool": per_pool_metrics,
        "sizeBins": size_bins_metrics,
        "perMinute": per_minute_buckets,
        "perBlock": per_block_buckets,
    }

    # Step 7: Save Outputs
    # Save data.json
    out_data_path = OUTPUT_DIR / "data.json"
    pub_data_path = FRONTEND_DIR / "data.json"
    atomic_write_json(out_data_path, full_payload)
    atomic_write_json(pub_data_path, full_payload)

    # Save trades.csv
    csv_headers = [
        "blockNumber", "timestamp", "transactionIndex", "logIndex", "intrablockIndex",
        "transactionHash", "poolAddress", "poolLabel", "family", "feeBps",
        "direction", "baseVolumeWeth", "quoteVolumeUsdc", "baseVolumeWei", "quoteVolumeRaw",
        "executionPrice", "postSwapSpot", "sqrtPriceX96",
    ]
    out_csv_path = OUTPUT_DIR / "trades.csv"
    pub_csv_path = FRONTEND_DIR / "trades.csv"

    def write_csv(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers)
            writer.writeheader()
            for t in trades_dicts:
                writer.writerow({k: t.get(k) for k in csv_headers})

    write_csv(out_csv_path)
    write_csv(pub_csv_path)

    # Step 8: Build Interactive HTML
    out_html_path = FRONTEND_DIR / "index.html"
    build_interactive_html(full_payload, trades_dicts, out_html_path)

    elapsed = time.perf_counter() - start_time
    full_payload["metadata"]["executionTimeSeconds"] = round(elapsed, 4)

    buy_vwap = buy_metrics["vwap"]
    sell_vwap = sell_metrics["vwap"]
    wg_buy_vwap = wg_buy["vwap"]
    wg_sell_vwap = wg_sell["vwap"]
    actual_buy_str = f"${wg_buy_vwap:.2f}" if wg_buy_vwap else "None"
    actual_sell_str = f"${wg_sell_vwap:.2f}" if wg_sell_vwap else "None"

    print(f"\n--- Swap Event Experiment Completed in {elapsed:.3f}s (Cache Reused: {cache_reused}) ---")
    print(f"Total Valid Swaps: {total_count} (Buys: {buy_metrics['count']}, Sells: {sell_metrics['count']})")
    print(f"Total WETH Volume: {total_weth:.4f} WETH | Total USDC Volume: ${total_usdc:,.2f} USDC")
    print(f"Buy VWAP:  ${buy_vwap:.2f} USDC/WETH" if buy_vwap else "Buy VWAP:  N/A")
    print(f"Sell VWAP: ${sell_vwap:.2f} USDC/WETH" if sell_vwap else "Sell VWAP: N/A")
    print(f"Mixed Combined VWAP: ${combined_vwap:.2f} USDC/WETH" if combined_vwap else "Mixed VWAP: N/A")
    print(f"First block hash verified: {first_hash_match} ({first_log_hash})")
    print(f"Last block hash verified:  {last_hash_match} ({last_log_hash})")
    print(f"Worst-gap block 23550044: Chainlink=${wg_row.get('chainlink'):.2f}, "
          f"Simulated=${wg_row.get('aggregates', {}).get('WETH', {}).get('1'):.2f}, "
          f"Actual Buy VWAP={actual_buy_str}, "
          f"Actual Sell VWAP={actual_sell_str}")
    print(f"Outputs written to {OUTPUT_DIR} and {FRONTEND_DIR}")

    return full_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="October 10 2025 Historical Swap-Event Experiment")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Bypass cached raw logs and re-fetch from RPC",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_experiment(force_refresh=args.force_refresh)


if __name__ == "__main__":
    main()
