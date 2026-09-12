#!/usr/bin/env python3
"""Bounded 24-hour mainnet sweep backwards from now every 10 minutes.

Compares:
1. Chainlink ETH/USD divided by USDC/USD (USDC per WETH)
2. 1inch OffchainOracle getRate(WETH, USDC, false) (USDC per WETH)
3. Uniswap V3 USDC/WETH fee 500 TWAP 300s & 60s (USDC per WETH)

145 target timestamps (endpoints inclusive, span 24h every 600s).
Anchored to latest mined block timestamp fetched live NOW.
Verified block bounds: chosen_block.timestamp <= target < next_block.timestamp.
Bounded concurrency (3-4 workers) via Multicall3 and RpcClient.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.oracles.reference_collector import (
    ONEINCH_OFFCHAIN_ORACLE,
    UNISWAP_V3_USDC_WETH_500,
    USDC_ADDRESS,
    WETH_ADDRESS,
    decode_1inch_rate,
    decode_univ3_twap,
    encode_1inch_get_rate,
    encode_univ3_observe,
)
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

# Chainlink Mainnet AggregatorV3 Feeds
CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
CHAINLINK_USDC_USD = "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6"

LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"


def verify_contract_deployments(client: RpcClient, anchor_block: BlockRef) -> dict[str, Any]:
    """Query and record code existence at anchor block for all key addresses."""
    addresses = {
        "1inch_offchain_oracle": ONEINCH_OFFCHAIN_ORACLE,
        "uniswap_v3_usdc_weth_500": UNISWAP_V3_USDC_WETH_500,
        "chainlink_eth_usd": CHAINLINK_ETH_USD,
        "chainlink_usdc_usd": CHAINLINK_USDC_USD,
        "weth": WETH_ADDRESS,
        "usdc": USDC_ADDRESS,
    }
    verification: dict[str, Any] = {}
    for name, addr in addresses.items():
        code = client._rpc("eth_getCode", [addr, hex(anchor_block.number)])
        raw_bytes = bytes.fromhex(code.removeprefix("0x"))
        if len(raw_bytes) == 0:
            raise RuntimeError(f"Contract {name} ({addr}) has empty code at block {anchor_block.number}")
        verification[name] = {
            "address": addr,
            "bytecodeLength": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "hasCode": True,
        }
    return verification


def find_block_for_target(
    client: RpcClient,
    target_ts: int,
    low_bound: int,
    high_bound: int,
    cache: dict[int, BlockRef],
) -> tuple[BlockRef, BlockRef | None]:
    """Finds the greatest block with timestamp <= target_ts via interpolated/binary search.

    Verifies chosen_block.timestamp <= target_ts < next_block.timestamp.
    """
    def _get(num: int) -> BlockRef:
        if num not in cache:
            cache[num] = client.get_block(num)
        return cache[num]

    low = low_bound
    high = high_bound

    # Ensure initial bounds bracket the target
    while _get(low).timestamp > target_ts:
        low -= 20
    while _get(high).timestamp <= target_ts:
        high += 20

    while low <= high:
        b_low = _get(low)
        b_low_next = _get(low + 1)
        if b_low.timestamp <= target_ts < b_low_next.timestamp:
            return b_low, b_low_next

        # Interpolated guess
        ts_low = b_low.timestamp
        ts_high = _get(high).timestamp
        rate = (ts_high - ts_low) / max(1, (high - low))
        guess = low + int((target_ts - ts_low) / max(0.1, rate))
        guess = max(low + 1, min(high - 1, guess))

        b_guess = _get(guess)
        b_guess_next = _get(guess + 1)
        if b_guess.timestamp <= target_ts < b_guess_next.timestamp:
            return b_guess, b_guess_next
        elif b_guess.timestamp <= target_ts:
            low = guess + 1
        else:
            high = guess - 1

    raise RuntimeError(f"Could not find block for target timestamp {target_ts}")


def resolve_145_target_blocks(
    client: RpcClient,
    anchor_block: BlockRef,
    step_seconds: int = 600,
    point_count: int = 145,
) -> list[dict[str, Any]]:
    """Resolves all target blocks spanning 24h backwards from anchor_block every step_seconds."""
    anchor_ts = anchor_block.timestamp
    # Generate targets from oldest (anchor - (point_count - 1) * 600) to latest (anchor)
    targets = [anchor_ts - (point_count - 1 - i) * step_seconds for i in range(point_count)]

    cache: dict[int, BlockRef] = {anchor_block.number: anchor_block}

    # Initial bracket for the oldest target: ~7200 blocks post-merge (12s/block)
    est_oldest = anchor_block.number - int((point_count * step_seconds) / 12) - 100
    while True:
        if est_oldest not in cache:
            cache[est_oldest] = client.get_block(est_oldest)
        if cache[est_oldest].timestamp <= targets[0]:
            break
        est_oldest -= 50

    results: list[dict[str, Any]] = []
    current_low = est_oldest

    for i, target_ts in enumerate(targets):
        if i == point_count - 1:
            chosen = anchor_block
            chosen_next = None
            verified = (chosen.timestamp == target_ts)
        else:
            chosen, chosen_next = find_block_for_target(
                client,
                target_ts,
                low_bound=current_low,
                high_bound=anchor_block.number,
                cache=cache,
            )
            verified = (chosen.timestamp <= target_ts < chosen_next.timestamp)
            current_low = chosen.number

        diff_seconds = target_ts - chosen.timestamp
        results.append({
            "target_index": i,
            "target_timestamp": target_ts,
            "target_utc": datetime.fromtimestamp(target_ts, UTC).isoformat(),
            "block_number": chosen.number,
            "block_hash": chosen.hash,
            "block_timestamp": chosen.timestamp,
            "block_utc": datetime.fromtimestamp(chosen.timestamp, UTC).isoformat(),
            "diff_seconds": diff_seconds,
            "next_block_number": chosen_next.number if chosen_next else None,
            "next_block_timestamp": chosen_next.timestamp if chosen_next else None,
            "verified_bounds": verified,
            "block_ref": chosen,
        })

    return results


def decode_chainlink_pair(
    res_eth: CallResult,
    res_usdc: CallResult,
    block_timestamp: int,
) -> dict[str, Any]:
    """Decodes Chainlink ETH/USD and USDC/USD feeds and calculates USDC per WETH."""
    if not res_eth.success or not res_usdc.success:
        return {
            "price": None,
            "status": "call_reverted",
            "eth_usd": None,
            "usdc_usd": None,
            "eth_raw_answer": None,
            "usdc_raw_answer": None,
            "eth_updated_at": None,
            "usdc_updated_at": None,
            "eth_age_seconds": None,
            "usdc_age_seconds": None,
            "reason": f"Call failed: eth_success={res_eth.success}, usdc_success={res_usdc.success}",
        }

    try:
        def _parse_raw(raw: str) -> tuple[int, int, float]:
            data = bytes.fromhex(raw.removeprefix("0x"))
            if len(data) != 160:
                raise ValueError(f"expected 160 bytes, got {len(data)}")
            words = [int.from_bytes(data[i : i + 32], "big") for i in range(0, 160, 32)]
            w_ans = words[1]
            ans = w_ans - (1 << 256) if w_ans >= 1 << 255 else w_ans
            upd = words[3]
            price = float(Decimal(ans) / Decimal(10**8))
            return ans, upd, price

        eth_ans, eth_upd, eth_price = _parse_raw(res_eth.raw)
        usdc_ans, usdc_upd, usdc_price = _parse_raw(res_usdc.raw)
    except (ValueError, IndexError) as exc:
        return {
            "price": None,
            "status": "decoding_error",
            "eth_usd": None,
            "usdc_usd": None,
            "eth_raw_answer": None,
            "usdc_raw_answer": None,
            "eth_updated_at": None,
            "usdc_updated_at": None,
            "eth_age_seconds": None,
            "usdc_age_seconds": None,
            "reason": f"Decode error: {exc}",
        }

    # Fail-closed checks on nonpositive answers or missing updatedAt
    if eth_ans <= 0 or usdc_ans <= 0:
        return {
            "price": None,
            "status": "non_positive_answer",
            "eth_usd": eth_price,
            "usdc_usd": usdc_price,
            "eth_raw_answer": eth_ans,
            "usdc_raw_answer": usdc_ans,
            "eth_updated_at": eth_upd,
            "usdc_updated_at": usdc_upd,
            "eth_age_seconds": block_timestamp - eth_upd if eth_upd else None,
            "usdc_age_seconds": block_timestamp - usdc_upd if usdc_upd else None,
            "reason": "Non-positive answer reported by feed",
        }

    if eth_upd == 0 or usdc_upd == 0:
        return {
            "price": None,
            "status": "missing_history",
            "eth_usd": eth_price,
            "usdc_usd": usdc_price,
            "eth_raw_answer": eth_ans,
            "usdc_raw_answer": usdc_ans,
            "eth_updated_at": eth_upd,
            "usdc_updated_at": usdc_upd,
            "eth_age_seconds": None,
            "usdc_age_seconds": None,
            "reason": "updatedAt timestamp is 0",
        }

    eth_age = block_timestamp - eth_upd
    usdc_age = block_timestamp - usdc_upd

    # Price = (ETH / USD) / (USDC / USD) = USDC per WETH
    price = float(Decimal(eth_ans) / Decimal(usdc_ans))

    return {
        "price": price,
        "status": "ok",
        "eth_usd": eth_price,
        "usdc_usd": usdc_price,
        "eth_raw_answer": eth_ans,
        "usdc_raw_answer": usdc_ans,
        "eth_updated_at": eth_upd,
        "usdc_updated_at": usdc_upd,
        "eth_age_seconds": eth_age,
        "usdc_age_seconds": usdc_age,
        "reason": None,
    }


def query_block_oracle_references(
    multicall: Multicall3,
    block: BlockRef,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Performs a single batched Multicall3 query per block and decodes all feeds."""
    specs = [
        CallSpec(ONEINCH_OFFCHAIN_ORACLE, encode_1inch_get_rate(WETH_ADDRESS, USDC_ADDRESS, False)),
        CallSpec(UNISWAP_V3_USDC_WETH_500, encode_univ3_observe(60)),
        CallSpec(UNISWAP_V3_USDC_WETH_500, encode_univ3_observe(300)),
        CallSpec(CHAINLINK_ETH_USD, LATEST_ROUND_DATA_SELECTOR),
        CallSpec(CHAINLINK_USDC_USD, LATEST_ROUND_DATA_SELECTOR),
    ]

    results = multicall.call(specs, block)
    res_1inch, res_60, res_300, res_eth, res_usdc = results

    dec_1inch = decode_1inch_rate(res_1inch)
    dec_60 = decode_univ3_twap(res_60, 60)
    dec_300 = decode_univ3_twap(res_300, 300)
    dec_cl = decode_chainlink_pair(res_eth, res_usdc, block.timestamp)

    decoded_row = {
        "chainlink": {
            "price": round(dec_cl["price"], 6) if dec_cl["price"] is not None else None,
            "status": dec_cl["status"],
            "eth_usd": dec_cl["eth_usd"],
            "usdc_usd": dec_cl["usdc_usd"],
            "eth_raw_answer": dec_cl["eth_raw_answer"],
            "usdc_raw_answer": dec_cl["usdc_raw_answer"],
            "eth_updated_at": dec_cl["eth_updated_at"],
            "usdc_updated_at": dec_cl["usdc_updated_at"],
            "eth_age_seconds": dec_cl["eth_age_seconds"],
            "usdc_age_seconds": dec_cl["usdc_age_seconds"],
            "reason": dec_cl["reason"],
        },
        "oneinch": {
            "price": round(dec_1inch["price"], 6) if dec_1inch["price"] is not None else None,
            "raw_rate": dec_1inch.get("rawRate"),
            "status": dec_1inch["status"],
            "reason": dec_1inch.get("reason"),
        },
        "univ3_twap_300": {
            "price": round(dec_300["price"], 6) if dec_300["price"] is not None else None,
            "arithmetic_mean_tick": dec_300.get("arithmeticMeanTick"),
            "delta_tick": dec_300.get("deltaTick"),
            "status": dec_300["status"],
            "reason": dec_300.get("reason"),
        },
        "univ3_twap_60": {
            "price": round(dec_60["price"], 6) if dec_60["price"] is not None else None,
            "arithmetic_mean_tick": dec_60.get("arithmeticMeanTick"),
            "delta_tick": dec_60.get("deltaTick"),
            "status": dec_60["status"],
            "reason": dec_60.get("reason"),
        },
    }

    raw_evidence = {
        "block": block.number,
        "blockHash": block.hash,
        "blockTimestamp": block.timestamp,
        "calls": {
            "1inch_getRate": {
                "success": res_1inch.success,
                "raw": res_1inch.raw,
                "via": res_1inch.via,
            },
            "univ3_observe_60": {
                "success": res_60.success,
                "raw": res_60.raw,
                "via": res_60.via,
            },
            "univ3_observe_300": {
                "success": res_300.success,
                "raw": res_300.raw,
                "via": res_300.via,
            },
            "chainlink_eth_usd_round": {
                "success": res_eth.success,
                "raw": res_eth.raw,
                "via": res_eth.via,
            },
            "chainlink_usdc_usd_round": {
                "success": res_usdc.success,
                "raw": res_usdc.raw,
                "via": res_usdc.via,
            },
        },
    }

    return decoded_row, raw_evidence


def calculate_gaps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculates divergences vs Chainlink for 1inch, UniV3 300s, and UniV3 60s."""
    sources = ["oneinch", "univ3_twap_300", "univ3_twap_60"]
    summaries: dict[str, Any] = {}

    for src in sources:
        gaps_signed: list[float] = []
        gaps_abs: list[float] = []
        worst_point: dict[str, Any] | None = None
        worst_abs_gap = -1.0
        count_gt_100 = 0
        count_gt_500 = 0

        for r in rows:
            cl_price = r["chainlink"]["price"]
            src_price = r[src]["price"]
            if cl_price is not None and cl_price > 0 and src_price is not None and src_price > 0:
                gap_bps = ((src_price - cl_price) / cl_price) * 10000.0
                abs_gap = abs(gap_bps)
                gaps_signed.append(gap_bps)
                gaps_abs.append(abs_gap)
                if abs_gap > 100.0:
                    count_gt_100 += 1
                if abs_gap > 500.0:
                    count_gt_500 += 1
                if abs_gap > worst_abs_gap:
                    worst_abs_gap = abs_gap
                    worst_point = {
                        "target_index": r["target_index"],
                        "target_utc": r["target_utc"],
                        "block_number": r["block_number"],
                        "gap_bps": round(gap_bps, 2),
                        "abs_gap_bps": round(abs_gap, 2),
                        "chainlink_price": cl_price,
                        "source_price": src_price,
                        "eth_age_seconds": r["chainlink"]["eth_age_seconds"],
                        "usdc_age_seconds": r["chainlink"]["usdc_age_seconds"],
                    }

        if gaps_signed:
            summaries[src] = {
                "valid_count": len(gaps_signed),
                "min_gap_bps": round(min(gaps_signed), 2),
                "max_gap_bps": round(max(gaps_signed), 2),
                "median_gap_bps": round(statistics.median(gaps_signed), 2),
                "min_abs_gap_bps": round(min(gaps_abs), 2),
                "max_abs_gap_bps": round(max(gaps_abs), 2),
                "median_abs_gap_bps": round(statistics.median(gaps_abs), 2),
                "count_gt_100bps": count_gt_100,
                "count_gt_500bps": count_gt_500,
                "worst_divergence": worst_point,
            }
        else:
            summaries[src] = {
                "valid_count": 0,
                "min_gap_bps": None,
                "max_gap_bps": None,
                "median_gap_bps": None,
                "count_gt_100bps": 0,
                "count_gt_500bps": 0,
                "worst_divergence": None,
            }

    return summaries


def build_html_report(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    summaries: dict[str, Any],
) -> str:
    """Generates standalone HTML report with responsive SVG charts and data table."""
    valid_points = [r for r in rows if r["chainlink"]["price"] is not None]
    
    all_prices: list[float] = []
    for r in valid_points:
        for k in ["chainlink", "oneinch", "univ3_twap_300"]:
            p = r[k]["price"]
            if p is not None:
                all_prices.append(p)
    
    p_min = min(all_prices) if all_prices else 2000.0
    p_max = max(all_prices) if all_prices else 3000.0
    pad = (p_max - p_min) * 0.08 or 10.0
    y_min = p_min - pad
    y_max = p_max + pad

    width = 1100
    height = 420
    margin_l = 75
    margin_r = 30
    margin_t = 30
    margin_b = 50

    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b

    def get_x(idx: int) -> float:
        if len(rows) <= 1:
            return margin_l
        return margin_l + (idx / (len(rows) - 1)) * plot_w

    def get_y(val: float | None) -> float | None:
        if val is None or y_max == y_min:
            return None
        return margin_t + plot_h - ((val - y_min) / (y_max - y_min)) * plot_h

    series_colors = {
        "chainlink": "#2563eb",       # Blue
        "oneinch": "#10b981",         # Emerald
        "univ3_twap_300": "#f59e0b",  # Amber
        "univ3_twap_60": "#8b5cf6",   # Purple
    }

    svg_paths: dict[str, str] = {}
    for s_id in ["chainlink", "oneinch", "univ3_twap_300", "univ3_twap_60"]:
        pts: list[str] = []
        for r in rows:
            p = r[s_id]["price"]
            y = get_y(p)
            if y is not None:
                pts.append(f"{get_x(r['target_index']):.1f},{y:.1f}")
        svg_paths[s_id] = " ".join(pts)

    # Y-axis ticks
    y_ticks_html = []
    num_y_ticks = 6
    for i in range(num_y_ticks):
        val = y_min + (i / (num_y_ticks - 1)) * (y_max - y_min)
        y_pos = margin_t + plot_h - (i / (num_y_ticks - 1)) * plot_h
        y_ticks_html.append(
            f'<line x1="{margin_l}" y1="{y_pos:.1f}" x2="{width - margin_r}" y2="{y_pos:.1f}" stroke="#334155" stroke-dasharray="3,3" stroke-width="0.7"/>'
            f'<text x="{margin_l - 10}" y="{y_pos + 4:.1f}" fill="#94a3b8" font-size="11" text-anchor="end">${val:.1f}</text>'
        )

    # X-axis ticks (e.g. every 24 targets = 4 hours)
    x_ticks_html = []
    step_x = max(1, len(rows) // 6)
    for idx in range(0, len(rows), step_x):
        r = rows[idx]
        x_pos = get_x(idx)
        time_label = r["target_utc"][11:16]
        date_label = r["target_utc"][5:10]
        x_ticks_html.append(
            f'<line x1="{x_pos:.1f}" y1="{margin_t}" x2="{x_pos:.1f}" y2="{margin_t + plot_h}" stroke="#334155" stroke-dasharray="2,2" stroke-width="0.5"/>'
            f'<line x1="{x_pos:.1f}" y1="{margin_t + plot_h}" x2="{x_pos:.1f}" y2="{margin_t + plot_h + 6}" stroke="#64748b" stroke-width="1.2"/>'
            f'<text x="{x_pos:.1f}" y="{margin_t + plot_h + 18}" fill="#cbd5e1" font-size="11" text-anchor="middle">{time_label}</text>'
            f'<text x="{x_pos:.1f}" y="{margin_t + plot_h + 30}" fill="#64748b" font-size="9" text-anchor="middle">{date_label}</text>'
        )

    # Table rows
    table_rows_html = []
    for r in rows:
        cl_p = r["chainlink"]["price"]
        oi_p = r["oneinch"]["price"]
        u3_p = r["univ3_twap_300"]["price"]
        u6_p = r["univ3_twap_60"]["price"]

        oi_gap = f"{((oi_p - cl_p) / cl_p * 10000):+.1f} bps" if (cl_p and oi_p) else "-"
        u3_gap = f"{((u3_p - cl_p) / cl_p * 10000):+.1f} bps" if (cl_p and u3_p) else "-"
        
        eth_age = f"{r['chainlink']['eth_age_seconds']}s" if r["chainlink"]["eth_age_seconds"] is not None else "-"
        usdc_age = f"{r['chainlink']['usdc_age_seconds']}s" if r["chainlink"]["usdc_age_seconds"] is not None else "-"

        table_rows_html.append(
            f"<tr>"
            f"<td>{r['target_index']}</td>"
            f"<td><code>{r['target_utc'][11:19]}</code></td>"
            f"<td><a href='https://etherscan.io/block/{r['block_number']}' target='_blank'>#{r['block_number']}</a></td>"
            f"<td>{r['diff_seconds']}s</td>"
            f"<td><strong>${cl_p:.2f}</strong></td>"
            f"<td>${oi_p:.2f}</td>"
            f"<td>${u3_p:.2f}</td>"
            f"<td>${u6_p:.2f}</td>"
            f"<td><code>{oi_gap}</code></td>"
            f"<td><code>{u3_gap}</code></td>"
            f"<td>{eth_age}</td>"
            f"<td>{usdc_age}</td>"
            f"<td><span class='badge badge-ok'>OK</span></td>"
            f"</tr>"
        )

    oi_sum = summaries.get("oneinch", {})
    u3_sum = summaries.get("univ3_twap_300", {})

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>24h Mainnet Oracle Sweep: Chainlink vs 1inch vs UniV3 TWAP</title>
<style>
  :root {{
    --bg-dark: #0f172a;
    --card-bg: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
    --accent-cl: #2563eb;
    --accent-1i: #10b981;
    --accent-u3: #f59e0b;
    --accent-u6: #8b5cf6;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: var(--bg-dark);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .container {{
    max-width: 1240px;
    margin: 0 auto;
  }}
  header {{
    margin-bottom: 24px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #fff; }}
  .meta {{ color: var(--text-muted); font-size: 13px; display: flex; gap: 16px; flex-wrap: wrap; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }}
  .card h3 {{ margin: 0 0 8px 0; font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .val {{ font-size: 24px; font-weight: 700; color: #fff; }}
  .card .sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .chart-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
  }}
  .chart-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .legend {{ display: flex; gap: 20px; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 12px; height: 12px; border-radius: 3px; }}
  svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: #182234; color: var(--text-muted); font-weight: 600; position: sticky; top: 0; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .table-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
    max-height: 480px;
    margin-bottom: 24px;
  }}
  .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }}
  .badge-ok {{ background: #064e3b; color: #6ee7b7; }}
  .btn-group {{ display: flex; gap: 8px; margin-top: 12px; }}
  .btn {{
    background: #334155;
    color: #fff;
    padding: 6px 12px;
    border-radius: 4px;
    text-decoration: none;
    font-size: 12px;
    border: 1px solid var(--border);
  }}
  .btn:hover {{ background: #475569; }}
  code {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; }}
  a {{ color: var(--primary); text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>24-Hour Mainnet Oracle Sweep</h1>
    <div class="meta">
      <span><strong>Anchor Block:</strong> #{manifest['anchor_block']['number']} ({manifest['anchor_block']['timestamp']})</span>
      <span><strong>Run UTC:</strong> {manifest['run_timestamp_utc']}</span>
      <span><strong>Window:</strong> {manifest['window_start_utc']} &rarr; {manifest['window_end_utc']}</span>
      <span><strong>Targets:</strong> 145 points (every 10m)</span>
      <span><strong>Duration:</strong> {manifest['duration_seconds']}s</span>
    </div>
    <div class="btn-group">
      <a class="btn" href="recent-oracle-check.json" download>Download JSON</a>
      <a class="btn" href="recent-oracle-check.csv" download>Download CSV</a>
      <a class="btn" href="raw-evidence.json" download>Download Raw Evidence</a>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h3>1inch vs Chainlink Median Gap</h3>
      <div class="val">{oi_sum.get('median_gap_bps', 0):+.1f} bps</div>
      <div class="sub">Range: {oi_sum.get('min_gap_bps', 0):+.1f} to {oi_sum.get('max_gap_bps', 0):+.1f} bps | &gt;100bps: {oi_sum.get('count_gt_100bps', 0)}</div>
    </div>
    <div class="card">
      <h3>UniV3 300s vs Chainlink Median Gap</h3>
      <div class="val">{u3_sum.get('median_gap_bps', 0):+.1f} bps</div>
      <div class="sub">Range: {u3_sum.get('min_gap_bps', 0):+.1f} to {u3_sum.get('max_gap_bps', 0):+.1f} bps | &gt;100bps: {u3_sum.get('count_gt_100bps', 0)}</div>
    </div>
    <div class="card">
      <h3>Max Divergence Point (1inch)</h3>
      <div class="val">{oi_sum.get('worst_divergence', {}).get('gap_bps', 0):+.1f} bps</div>
      <div class="sub">At {oi_sum.get('worst_divergence', {}).get('target_utc', '')[11:19]} UTC (Block #{oi_sum.get('worst_divergence', {}).get('block_number', 0)}) | ETH age: {oi_sum.get('worst_divergence', {}).get('eth_age_seconds', 0)}s, USDC age: {oi_sum.get('worst_divergence', {}).get('usdc_age_seconds', 0)}s</div>
    </div>
    <div class="card">
      <h3>Max Divergence Point (UniV3 300s)</h3>
      <div class="val">{u3_sum.get('worst_divergence', {}).get('gap_bps', 0):+.1f} bps</div>
      <div class="sub">At {u3_sum.get('worst_divergence', {}).get('target_utc', '')[11:19]} UTC (Block #{u3_sum.get('worst_divergence', {}).get('block_number', 0)}) | ETH age: {u3_sum.get('worst_divergence', {}).get('eth_age_seconds', 0)}s, USDC age: {u3_sum.get('worst_divergence', {}).get('usdc_age_seconds', 0)}s</div>
    </div>
  </div>

  <div class="chart-box">
    <div class="chart-header">
      <div><strong>Price Trajectory (USDC per WETH)</strong></div>
      <div class="legend">
        <div class="legend-item"><div class="dot" style="background: {series_colors['chainlink']}"></div>Chainlink (ETH/USD &divide; USDC/USD)</div>
        <div class="legend-item"><div class="dot" style="background: {series_colors['oneinch']}"></div>1inch Spot</div>
        <div class="legend-item"><div class="dot" style="background: {series_colors['univ3_twap_300']}"></div>UniV3 TWAP 300s</div>
        <div class="legend-item"><div class="dot" style="background: {series_colors['univ3_twap_60']}"></div>UniV3 TWAP 60s</div>
      </div>
    </div>
    <svg viewBox="0 0 {width} {height}">
      {''.join(y_ticks_html)}
      {''.join(x_ticks_html)}
      <polyline fill="none" stroke="{series_colors['chainlink']}" stroke-width="2.5" points="{svg_paths['chainlink']}"/>
      <polyline fill="none" stroke="{series_colors['oneinch']}" stroke-width="2.0" points="{svg_paths['oneinch']}"/>
      <polyline fill="none" stroke="{series_colors['univ3_twap_300']}" stroke-width="2.0" stroke-dasharray="4,2" points="{svg_paths['univ3_twap_300']}"/>
      <polyline fill="none" stroke="{series_colors['univ3_twap_60']}" stroke-width="1.2" stroke-opacity="0.6" points="{svg_paths['univ3_twap_60']}"/>
    </svg>
  </div>

  <div class="table-box">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Target (UTC)</th>
          <th>Block</th>
          <th>Diff</th>
          <th>Chainlink</th>
          <th>1inch Spot</th>
          <th>UniV3 300s</th>
          <th>UniV3 60s</th>
          <th>1i vs CL</th>
          <th>U3 vs CL</th>
          <th>ETH Age</th>
          <th>USDC Age</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows_html)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    return html_content


def main() -> None:
    parser = argparse.ArgumentParser(description="24-hour mainnet oracle sweep backwards from now every 10m")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs/recent-oracle-check")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--span-hours", type=int, default=24)
    parser.add_argument("--interval-seconds", type=int, default=600)
    args = parser.parse_args()

    start_perf = time.perf_counter()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_root = output_dir / "rpc-cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    # Initialize master RPC client
    master_client = RpcClient(cache_root=cache_root)
    chain_id = master_client.chain_id()
    if chain_id != 1:
        raise RuntimeError(f"Configured RPC chain ID is {chain_id}, expected 1 (Ethereum Mainnet)")

    # Fetch live anchor block NOW
    anchor_block = master_client.get_block("latest")
    run_utc = datetime.now(UTC).isoformat()
    anchor_utc = datetime.fromtimestamp(anchor_block.timestamp, UTC).isoformat()

    print("=== Recent Oracle Check (Chainlink vs 1inch vs UniV3) ===")
    print(f"Anchored to live block #{anchor_block.number} (timestamp {anchor_block.timestamp} -> {anchor_utc})")
    print(f"Execution started at: {run_utc}")

    # Verify deployment / code existence on chain at anchor block
    print("Verifying contract code on chain at latest block...")
    deployments = verify_contract_deployments(master_client, anchor_block)
    for name, info in deployments.items():
        print(f"  Verified {name} ({info['address']}): {info['bytecodeLength']} bytes")

    # Resolve 145 target blocks spanning 24h backwards
    print("Resolving 145 target blocks backwards from now every 600s...")
    t_search_start = time.perf_counter()
    target_blocks = resolve_145_target_blocks(
        master_client,
        anchor_block,
        step_seconds=args.interval_seconds,
        point_count=145,
    )
    t_search_end = time.perf_counter()
    print(f"Resolved 145 target blocks in {t_search_end - t_search_start:.2f}s")
    for r in [target_blocks[0], target_blocks[72], target_blocks[-1]]:
        print(f"  [Idx {r['target_index']}] Target: {r['target_utc']} -> Block #{r['block_number']} ({r['block_utc']}), diff: {r['diff_seconds']}s")

    # Batch query all references per block with bounded concurrency
    print(f"Querying references across all 145 blocks with {args.workers} workers...")
    _local = threading.local()

    def worker_task(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not hasattr(_local, "client"):
            _local.client = RpcClient(cache_root=cache_root)
            _local.multicall = Multicall3(_local.client)
        block_ref = entry["block_ref"]
        decoded_row, raw_evidence = query_block_oracle_references(_local.multicall, block_ref)
        return decoded_row, raw_evidence

    t_query_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        batch_results = list(executor.map(worker_task, target_blocks))
    t_query_end = time.perf_counter()
    print(f"Queried all 145 blocks in {t_query_end - t_query_start:.2f}s")

    # Combine results
    rows: list[dict[str, Any]] = []
    raw_evidence_list: list[dict[str, Any]] = []
    status_counts: dict[str, dict[str, int]] = {
        "chainlink": {},
        "oneinch": {},
        "univ3_twap_300": {},
        "univ3_twap_60": {},
    }

    for entry, (decoded_data, raw_data) in zip(target_blocks, batch_results, strict=True):
        row = {
            "target_index": entry["target_index"],
            "target_timestamp": entry["target_timestamp"],
            "target_utc": entry["target_utc"],
            "block_number": entry["block_number"],
            "block_hash": entry["block_hash"],
            "block_timestamp": entry["block_timestamp"],
            "block_utc": entry["block_utc"],
            "diff_seconds": entry["diff_seconds"],
            "verified_bounds": entry["verified_bounds"],
            "chainlink": decoded_data["chainlink"],
            "oneinch": decoded_data["oneinch"],
            "univ3_twap_300": decoded_data["univ3_twap_300"],
            "univ3_twap_60": decoded_data["univ3_twap_60"],
        }
        rows.append(row)
        raw_evidence_list.append(raw_data)

        # Count statuses
        for src, counts in status_counts.items():
            st = decoded_data[src]["status"]
            counts[st] = counts.get(st, 0) + 1

    # Calculate summary gaps vs Chainlink
    summaries = calculate_gaps(rows)

    total_duration = round(time.perf_counter() - start_perf, 2)
    print(f"Full sweep completed in {total_duration}s")

    # Build manifest
    manifest = {
        "run_timestamp_utc": run_utc,
        "duration_seconds": total_duration,
        "chain_id": chain_id,
        "target_count": len(rows),
        "interval_seconds": args.interval_seconds,
        "anchor_block": {
            "number": anchor_block.number,
            "hash": anchor_block.hash,
            "timestamp": anchor_block.timestamp,
            "utc": anchor_utc,
        },
        "window_start_utc": rows[0]["target_utc"],
        "window_end_utc": rows[-1]["target_utc"],
        "deployment_verification": deployments,
        "status_counts": status_counts,
        "timings": {
            "total_seconds": total_duration,
            "header_search_seconds": round(t_search_end - t_search_start, 2),
            "multicall_queries_seconds": round(t_query_end - t_query_start, 2),
        },
        "summary_gaps_vs_chainlink": summaries,
    }

    # Print summary
    print("\n=== Oracle Comparison Summary vs Chainlink ===")
    for src, sm in summaries.items():
        print(f"[{src}] Valid: {sm['valid_count']} | Median Gap: {sm['median_gap_bps']:+.2f} bps | Range: [{sm['min_gap_bps']:+.2f}, {sm['max_gap_bps']:+.2f}] bps | >100bps: {sm['count_gt_100bps']} | >500bps: {sm['count_gt_500bps']}")
        if sm["worst_divergence"]:
            w = sm["worst_divergence"]
            print(f"  Worst: {w['gap_bps']:+.2f} bps at {w['target_utc']} (Block #{w['block_number']}) | CL: ${w['chainlink_price']:.2f}, SRC: ${w['source_price']:.2f} | ETH age: {w['eth_age_seconds']}s, USDC age: {w['usdc_age_seconds']}s")

    # Write JSON output
    json_path = output_dir / "recent-oracle-check.json"
    full_output = {
        "manifest": manifest,
        "rows": rows,
    }
    json_content = json.dumps(full_output, indent=2).encode("utf-8")
    json_path.write_bytes(json_content)
    json_cid_sha256 = hashlib.sha256(json_content).hexdigest()
    print(f"\nWrote JSON dataset: {json_path} (SHA-256: {json_cid_sha256})")

    # Write Raw Evidence
    raw_path = output_dir / "raw-evidence.json"
    raw_content = json.dumps({"manifest": manifest, "evidence": raw_evidence_list}, indent=2).encode("utf-8")
    raw_path.write_bytes(raw_content)
    print(f"Wrote raw response evidence: {raw_path}")

    # Write CSV output
    csv_path = output_dir / "recent-oracle-check.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "target_index",
            "target_timestamp",
            "target_utc",
            "block_number",
            "block_hash",
            "block_timestamp",
            "block_utc",
            "diff_seconds",
            "chainlink_price",
            "oneinch_price",
            "univ3_twap_300_price",
            "univ3_twap_60_price",
            "oneinch_gap_bps",
            "univ3_300_gap_bps",
            "univ3_60_gap_bps",
            "chainlink_eth_age_seconds",
            "chainlink_usdc_age_seconds",
            "chainlink_status",
            "oneinch_status",
            "univ3_300_status",
            "univ3_60_status",
        ])
        for r in rows:
            cl_p = r["chainlink"]["price"]
            oi_p = r["oneinch"]["price"]
            u3_p = r["univ3_twap_300"]["price"]
            u6_p = r["univ3_twap_60"]["price"]
            oi_gap = round(((oi_p - cl_p) / cl_p * 10000), 2) if (cl_p and oi_p) else ""
            u3_gap = round(((u3_p - cl_p) / cl_p * 10000), 2) if (cl_p and u3_p) else ""
            u6_gap = round(((u6_p - cl_p) / cl_p * 10000), 2) if (cl_p and u6_p) else ""
            writer.writerow([
                r["target_index"],
                r["target_timestamp"],
                r["target_utc"],
                r["block_number"],
                r["block_hash"],
                r["block_timestamp"],
                r["block_utc"],
                r["diff_seconds"],
                cl_p,
                oi_p,
                u3_p,
                u6_p,
                oi_gap,
                u3_gap,
                u6_gap,
                r["chainlink"]["eth_age_seconds"],
                r["chainlink"]["usdc_age_seconds"],
                r["chainlink"]["status"],
                r["oneinch"]["status"],
                r["univ3_twap_300"]["status"],
                r["univ3_twap_60"]["status"],
            ])
    print(f"Wrote CSV export: {csv_path}")

    # Write HTML standalone report
    html_path = output_dir / "index.html"
    html_content = build_html_report(manifest, rows, summaries)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"Wrote standalone HTML report: {html_path}")


if __name__ == "__main__":
    main()
