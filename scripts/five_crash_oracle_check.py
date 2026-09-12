#!/usr/bin/env python3
"""Collector and report generator for five Binance ETH crashes.

Compares oracle and reference prices:
1. Chainlink ETH/USD divided by USDC/USD (USDC per WETH) with separate feed ages
2. 1inch OffchainOracle getRate(WETH, USDC, false) (USDC per WETH)
3. Uniswap V3 USDC/WETH fee 500 TWAP 300s & 60s (USDC per WETH)
4. Binance ETH/USDC completed minute candle close price (USDC per ETH)

Operates across 5 independent ETH crash windows (min 7 days apart, 2024-08-16..2026-09-08).
Each window consists of 145 targets every 600s spanning 24h (-12h before to +12h after trough).
Shared cached header resolution guarantees chosen.timestamp <= target < next.timestamp for all 725 pins.
Global bounded 4 RPC workers batch read 5 calls per block via Multicall3.
Fail-closed null logic: missing Binance ETHUSDC explicitly null, never USDT fallback. No zero lines in charts.
Atomic writes preserve existing datasets.
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
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project root and src are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scripts.recent_oracle_check import (
    find_block_for_target,
    query_block_oracle_references,
    verify_contract_deployments,
)
from swaparch.core.types import BlockRef
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

POST_MERGE_BLOCK = 15537393
POST_MERGE_TIMESTAMP = 1663224162

CAVEATS = [
    "Sample counts (145 points per 24h window) are discrete snapshots, not continuous duration monitoring. 10-minute (600s) intervals can miss intra-sample spikes or transient liquidations.",
    "Prices represent size-independent oracle and spot reference rates (Chainlink feeds, 1inch spot aggregator, UniV3 geometric TWAPs, Binance minute closes), not executable routes with size-dependent slippage or market impact.",
    "Binance ETH/USDC minute closes are aligned to the most recent completed candle with closeTime <= actual block timestamp. Missing ETH/USDC data fails closed as null with no USDT fallback.",
]


def parse_selection_file(path: Path | str) -> dict[str, Any]:
    """Parses and validates outputs/five-crash-oracles/selection.json."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Selection file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if "schemaVersion" not in data or data.get("schemaVersion") != 1:
        raise ValueError(f"Unsupported schemaVersion in selection: {data.get('schemaVersion')}")
    if "crashes" not in data or not isinstance(data["crashes"], list):
        raise ValueError("selection.json must contain 'crashes' list")
    return data


def generate_window_targets(
    window_start: int, window_end: int, step_seconds: int = 600
) -> list[int]:
    """Generates exactly 145 target timestamps from window_start to window_end inclusive.

    Rejects any window where (window_end - window_start) does not equal exactly 144 * step_seconds.
    """
    expected_span = 144 * step_seconds
    actual_span = window_end - window_start
    if actual_span != expected_span:
        raise ValueError(
            f"Invalid window span: window_end ({window_end}) - window_start ({window_start}) = "
            f"{actual_span}s, expected exactly {expected_span}s (144 steps of {step_seconds}s)"
        )
    return [window_start + i * step_seconds for i in range(145)]


def load_binance_minute_file(
    file_path: Path | str | None,
    base_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Loads and validates a normalized Binance minute file."""
    if not file_path:
        return None

    path_obj = Path(file_path)
    candidates: list[Path] = []
    if path_obj.is_absolute():
        candidates.append(path_obj)
    else:
        if base_dir:
            candidates.append(base_dir / path_obj)
            candidates.append(base_dir / "binance" / path_obj.name)
        candidates.append(PROJECT_ROOT / path_obj)
        candidates.append(PROJECT_ROOT / "outputs/five-crash-oracles" / path_obj)
        candidates.append(PROJECT_ROOT / "outputs/five-crash-oracles/binance" / path_obj.name)

    resolved: Path | None = None
    for c in candidates:
        if c.exists():
            resolved = c
            break

    if not resolved:
        return None

    try:
        content = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            return None
        return content
    except (json.JSONDecodeError, OSError):
        return None


def match_binance_candle(
    binance_data: dict[str, Any] | None,
    block_timestamp: int,
) -> dict[str, Any]:
    """Matches the most recent COMPLETED Binance minute candle where completion_time <= block_timestamp.

    Guarantees:
    - Matches completed minute using completion_time = openTime + 60 (or closeTime + 1 for floored seconds).
    - Prevents leaking ~1s future data at exact second :59 when closeTime is floored from second 59.999.
    - Preserves recorded original closeTime.
    - Returns/persists completion_time, completion_utc, close_time, close_utc, and lag_seconds.
    - Missing Binance ETHUSDC fails closed as null; never falls back to USDT.
    """
    if binance_data is None:
        return {
            "price": None,
            "completion_time": None,
            "completion_utc": None,
            "close_time": None,
            "close_utc": None,
            "lag_seconds": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "status": "missing_file",
            "reason": "Binance minute file not found or empty",
        }

    symbol = binance_data.get("symbol", "").upper()
    if symbol != "ETHUSDC":
        return {
            "price": None,
            "completion_time": None,
            "completion_utc": None,
            "close_time": None,
            "close_utc": None,
            "lag_seconds": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "status": "symbol_mismatch",
            "reason": f"Expected ETHUSDC, got {symbol}. Fail closed: no USDT fallback",
        }

    candles = binance_data.get("candles", [])
    if not candles:
        return {
            "price": None,
            "completion_time": None,
            "completion_utc": None,
            "close_time": None,
            "close_utc": None,
            "lag_seconds": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "status": "no_candles",
            "reason": "Empty candle list in Binance minute file",
        }

    # Normalize candle timestamps and calculate completion_time
    completion_times: list[int] = []
    close_times: list[int] = []
    for c in candles:
        ct = c.get("closeTime", 0)
        if ct > 10_000_000_000:
            ct = ct // 1000
        ot = c.get("openTime")
        if ot is not None and ot > 10_000_000_000:
            ot = ot // 1000
        comp_t = (ot + 60) if ot is not None else (ct + 1)
        completion_times.append(comp_t)
        close_times.append(ct)

    # Use binary search to find the greatest index where completion_time <= block_timestamp
    idx = bisect_right(completion_times, block_timestamp) - 1
    if idx < 0:
        return {
            "price": None,
            "completion_time": None,
            "completion_utc": None,
            "close_time": None,
            "close_utc": None,
            "lag_seconds": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "status": "no_completed_candle",
            "reason": f"No completed candle with completion_time <= {block_timestamp}",
        }

    matched = candles[idx]
    c_comp_time = completion_times[idx]
    c_close_time = close_times[idx]
    if c_comp_time > block_timestamp:
        # Strict defensive check: never future minute
        return {
            "price": None,
            "completion_time": None,
            "completion_utc": None,
            "close_time": None,
            "close_utc": None,
            "lag_seconds": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "status": "future_candle_rejected",
            "reason": f"Matched candle completion_time {c_comp_time} > block {block_timestamp}",
        }

    lag = block_timestamp - c_comp_time
    price = float(matched["close"]) if matched.get("close") is not None else None

    return {
        "price": round(price, 6) if price is not None else None,
        "completion_time": c_comp_time,
        "completion_utc": datetime.fromtimestamp(c_comp_time, UTC).isoformat(),
        "close_time": c_close_time,
        "close_utc": datetime.fromtimestamp(c_close_time, UTC).isoformat(),
        "lag_seconds": lag,
        "open": float(matched.get("open", 0.0)) if matched.get("open") is not None else None,
        "high": float(matched.get("high", 0.0)) if matched.get("high") is not None else None,
        "low": float(matched.get("low", 0.0)) if matched.get("low") is not None else None,
        "close": price,
        "status": "ok",
        "reason": None,
    }


def resolve_target_blocks_shared(
    client: RpcClient,
    targets: list[int],
    cache: dict[int, BlockRef] | None = None,
    initial_block_hint: int | None = None,
) -> list[dict[str, Any]]:
    """Resolves target blocks for a window using a shared header cache.

    Guarantees exact chosen.timestamp <= target < next.timestamp for all pins.
    """
    if cache is None:
        cache = {}

    results: list[dict[str, Any]] = []

    def _estimate_bracket(ts: int) -> tuple[int, int]:
        if cache:
            closest_num, closest_block = min(
                cache.items(), key=lambda kv: abs(kv[1].timestamp - ts)
            )
            dt = ts - closest_block.timestamp
            guess = max(1, closest_num + int(dt / 12))
        elif initial_block_hint is not None:
            guess = initial_block_hint
        else:
            dt = ts - POST_MERGE_TIMESTAMP
            guess = max(1, POST_MERGE_BLOCK + int(dt / 12))

        if guess not in cache:
            b_probe = client.get_block(guess)
            cache[guess] = b_probe
        else:
            b_probe = cache[guess]

        dt_cal = ts - b_probe.timestamp
        calibrated = max(1, guess + int(dt_cal / 12))
        return max(1, calibrated - 40), calibrated + 40

    current_low: int | None = None

    for i, target_ts in enumerate(targets):
        if current_low is None:
            low_bound, high_bound = _estimate_bracket(target_ts)
        else:
            low_bound = current_low
            high_bound = current_low + 100

        chosen, chosen_next = find_block_for_target(
            client=client,
            target_ts=target_ts,
            low_bound=low_bound,
            high_bound=high_bound,
            cache=cache,
        )

        if chosen_next is None:
            # If search didn't return next, fetch chosen.number + 1
            if chosen.number + 1 not in cache:
                cache[chosen.number + 1] = client.get_block(chosen.number + 1)
            chosen_next = cache[chosen.number + 1]

        verified = chosen.timestamp <= target_ts < chosen_next.timestamp
        if not verified:
            raise RuntimeError(
                f"Block bounds violated: block #{chosen.number} ts {chosen.timestamp} "
                f"<= target {target_ts} < next #{chosen_next.number} ts {chosen_next.timestamp}"
            )

        diff_seconds = target_ts - chosen.timestamp
        current_low = chosen.number

        results.append(
            {
                "target_index": i,
                "target_timestamp": target_ts,
                "target_utc": datetime.fromtimestamp(target_ts, UTC).isoformat(),
                "block_number": chosen.number,
                "block_hash": chosen.hash,
                "block_timestamp": chosen.timestamp,
                "block_utc": datetime.fromtimestamp(chosen.timestamp, UTC).isoformat(),
                "diff_seconds": diff_seconds,
                "next_block_number": chosen_next.number,
                "next_block_timestamp": chosen_next.timestamp,
                "verified_bounds": verified,
                "block_ref": chosen,
            }
        )

    return results


def calculate_crash_gaps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculates divergences vs Chainlink for 1inch, UniV3 300s, UniV3 60s, and Binance ETH/USDC."""
    sources = ["oneinch", "univ3_twap_300", "univ3_twap_60", "binance"]
    summaries: dict[str, Any] = {}

    for src in sources:
        gaps_signed_bps: list[float] = []
        gaps_abs_bps: list[float] = []
        gaps_signed_pct: list[float] = []
        gaps_abs_pct: list[float] = []

        worst_abs_point: dict[str, Any] | None = None
        worst_negative_point: dict[str, Any] | None = None
        worst_positive_point: dict[str, Any] | None = None

        worst_abs_gap = -1.0
        most_negative_gap = 1e9
        most_positive_gap = -1e9
        count_gt_100 = 0
        count_gt_500 = 0

        for r in rows:
            cl_info = r.get("chainlink", {})
            cl_price = cl_info.get("price")
            src_info = r.get(src, {})
            src_price = src_info.get("price")

            if cl_price is not None and cl_price > 0 and src_price is not None and src_price > 0:
                gap_pct = ((src_price - cl_price) / cl_price) * 100.0
                gap_bps = gap_pct * 100.0
                abs_gap_bps = abs(gap_bps)
                abs_gap_pct = abs(gap_pct)

                gaps_signed_bps.append(gap_bps)
                gaps_abs_bps.append(abs_gap_bps)
                gaps_signed_pct.append(gap_pct)
                gaps_abs_pct.append(abs_gap_pct)

                if abs_gap_bps > 100.0:
                    count_gt_100 += 1
                if abs_gap_bps > 500.0:
                    count_gt_500 += 1

                point_record = {
                    "target_index": r["target_index"],
                    "target_utc": r["target_utc"],
                    "block_number": r["block_number"],
                    "gap_pct": round(gap_pct, 4),
                    "gap_bps": round(gap_bps, 2),
                    "abs_gap_pct": round(abs_gap_pct, 4),
                    "abs_gap_bps": round(abs_gap_bps, 2),
                    "chainlink_price": cl_price,
                    "source_price": src_price,
                    "eth_age_seconds": cl_info.get("eth_age_seconds"),
                    "usdc_age_seconds": cl_info.get("usdc_age_seconds"),
                    "binance_lag_seconds": src_info.get("lag_seconds")
                    if src == "binance"
                    else None,
                }

                if abs_gap_bps > worst_abs_gap:
                    worst_abs_gap = abs_gap_bps
                    worst_abs_point = point_record

                if gap_pct < most_negative_gap:
                    most_negative_gap = gap_pct
                    worst_negative_point = point_record

                if gap_pct > most_positive_gap:
                    most_positive_gap = gap_pct
                    worst_positive_point = point_record

        if gaps_signed_bps:
            summaries[src] = {
                "valid_count": len(gaps_signed_bps),
                "min_gap_bps": round(min(gaps_signed_bps), 2),
                "max_gap_bps": round(max(gaps_signed_bps), 2),
                "median_gap_bps": round(statistics.median(gaps_signed_bps), 2),
                "min_abs_gap_bps": round(min(gaps_abs_bps), 2),
                "max_abs_gap_bps": round(max(gaps_abs_bps), 2),
                "median_abs_gap_bps": round(statistics.median(gaps_abs_bps), 2),
                "min_gap_pct": round(min(gaps_signed_pct), 4),
                "max_gap_pct": round(max(gaps_signed_pct), 4),
                "median_gap_pct": round(statistics.median(gaps_signed_pct), 4),
                "max_negative_gap_pct": round(min(gaps_signed_pct), 4),
                "max_positive_gap_pct": round(max(gaps_signed_pct), 4),
                "max_abs_gap_pct": round(max(gaps_abs_pct), 4),
                "count_gt_100bps": count_gt_100,
                "count_gt_500bps": count_gt_500,
                "worst_divergence": worst_abs_point,
                "worst_negative_divergence": worst_negative_point,
                "worst_positive_divergence": worst_positive_point,
            }
        else:
            summaries[src] = {
                "valid_count": 0,
                "min_gap_bps": None,
                "max_gap_bps": None,
                "median_gap_bps": None,
                "min_abs_gap_bps": None,
                "max_abs_gap_bps": None,
                "median_abs_gap_bps": None,
                "min_gap_pct": None,
                "max_gap_pct": None,
                "median_gap_pct": None,
                "max_negative_gap_pct": None,
                "max_positive_gap_pct": None,
                "max_abs_gap_pct": None,
                "count_gt_100bps": 0,
                "count_gt_500bps": 0,
                "worst_divergence": None,
                "worst_negative_divergence": None,
                "worst_positive_divergence": None,
            }

    return summaries


def build_crash_html_report(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    summaries: dict[str, Any],
) -> str:
    """Builds a standalone HTML report with responsive SVG chart and table for a single crash."""
    crash_id = manifest.get("crash_id", "crash")
    date_utc = manifest.get("date_utc", "")
    drop_pct = manifest.get("drop_pct", "")

    all_prices: list[float] = []
    for r in rows:
        for k in ["chainlink", "oneinch", "univ3_twap_300", "univ3_twap_60", "binance"]:
            p = r.get(k, {}).get("price")
            if p is not None and p > 0:
                all_prices.append(p)

    p_min = min(all_prices) if all_prices else 2000.0
    p_max = max(all_prices) if all_prices else 3000.0
    pad = (p_max - p_min) * 0.08 or 10.0
    y_min = p_min - pad
    y_max = p_max + pad

    width = 1100
    height = 430
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
        if val is None or val <= 0 or y_max == y_min:
            return None
        return margin_t + plot_h - ((val - y_min) / (y_max - y_min)) * plot_h

    series_config = {
        "chainlink": {
            "color": "#2563eb",
            "width": 2.5,
            "dash": "",
            "name": "Chainlink (ETH/USD ÷ USDC/USD)",
        },
        "oneinch": {"color": "#10b981", "width": 2.0, "dash": "", "name": "1inch Spot"},
        "univ3_twap_300": {
            "color": "#f59e0b",
            "width": 2.0,
            "dash": "4,2",
            "name": "UniV3 TWAP 300s",
        },
        "univ3_twap_60": {"color": "#8b5cf6", "width": 1.2, "dash": "", "name": "UniV3 TWAP 60s"},
        "binance": {
            "color": "#06b6d4",
            "width": 2.0,
            "dash": "2,2",
            "name": "Binance ETH/USDC (Completed)",
        },
    }

    svg_paths: dict[str, str] = {}
    for s_id in series_config:
        d_parts: list[str] = []
        in_segment = False
        for r in rows:
            p = r.get(s_id, {}).get("price")
            y = get_y(p)
            if y is not None:
                x = get_x(r["target_index"])
                if not in_segment:
                    d_parts.append(f"M {x:.1f},{y:.1f} L {x:.1f},{y:.1f}")
                    in_segment = True
                else:
                    d_parts.append(f"L {x:.1f},{y:.1f}")
            else:
                in_segment = False
        svg_paths[s_id] = " ".join(d_parts)

    y_ticks_html = []
    num_y_ticks = 6
    for i in range(num_y_ticks):
        val = y_min + (i / (num_y_ticks - 1)) * (y_max - y_min)
        y_pos = margin_t + plot_h - (i / (num_y_ticks - 1)) * plot_h
        y_ticks_html.append(
            f'<line x1="{margin_l}" y1="{y_pos:.1f}" x2="{width - margin_r}" y2="{y_pos:.1f}" stroke="#334155" stroke-dasharray="3,3" stroke-width="0.7"/>'
            f'<text x="{margin_l - 10}" y="{y_pos + 4:.1f}" fill="#94a3b8" font-size="11" text-anchor="end">${val:.1f}</text>'
        )

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

    table_rows_html = []
    for r in rows:
        cl_p = r["chainlink"]["price"]
        oi_p = r["oneinch"]["price"]
        u3_p = r["univ3_twap_300"]["price"]
        u6_p = r["univ3_twap_60"]["price"]
        bin_info = r.get("binance", {})
        bin_p = bin_info.get("price")

        oi_gap = f"{((oi_p - cl_p) / cl_p * 100):+.2f}%" if (cl_p and oi_p) else "-"
        u3_gap = f"{((u3_p - cl_p) / cl_p * 100):+.2f}%" if (cl_p and u3_p) else "-"
        bin_gap = f"{((bin_p - cl_p) / cl_p * 100):+.2f}%" if (cl_p and bin_p) else "-"

        eth_age = (
            f"{r['chainlink']['eth_age_seconds']}s"
            if r["chainlink"].get("eth_age_seconds") is not None
            else "-"
        )
        usdc_age = (
            f"{r['chainlink']['usdc_age_seconds']}s"
            if r["chainlink"].get("usdc_age_seconds") is not None
            else "-"
        )
        bin_lag = (
            f"{bin_info.get('lag_seconds')}s" if bin_info.get("lag_seconds") is not None else "-"
        )

        table_rows_html.append(
            f"<tr>"
            f"<td>{r['target_index']}</td>"
            f"<td><code>{r['target_utc'][11:19]}</code></td>"
            f"<td><a href='https://etherscan.io/block/{r['block_number']}' target='_blank'>#{r['block_number']}</a></td>"
            f"<td>{r['diff_seconds']}s</td>"
            f"<td><strong>${cl_p:.2f}</strong></td>"
            if cl_p
            else f"<td>-</td><td>${oi_p:.2f}</td>"
            if oi_p
            else f"<td>-</td><td>${u3_p:.2f}</td>"
            if u3_p
            else f"<td>-</td><td>${u6_p:.2f}</td>"
            if u6_p
            else f"<td>-</td><td>${bin_p:.2f}</td>"
            if bin_p
            else "<td>-</td>"
            f"<td><code>{oi_gap}</code></td>"
            f"<td><code>{u3_gap}</code></td>"
            f"<td><code>{bin_gap}</code></td>"
            f"<td>{eth_age}</td>"
            f"<td>{usdc_age}</td>"
            f"<td>{bin_lag}</td>"
            f"</tr>"
        )

    oi_sum = summaries.get("oneinch", {})
    u3_sum = summaries.get("univ3_twap_300", {})
    bin_sum = summaries.get("binance", {})

    def _fmt_pct(val: float | None) -> str:
        return f"{val:+.2f}%" if val is not None else "-"

    def _fmt_sub(s: dict[str, Any]) -> str:
        max_neg = _fmt_pct(s.get("max_negative_gap_pct"))
        max_abs = (
            f"{s.get('max_abs_gap_pct'):.2f}%"
            if s.get("max_abs_gap_pct") is not None
            else "-"
        )
        cnt = s.get("count_gt_100bps", 0)
        return f"Max Neg: {max_neg} | Max Abs: {max_abs} | &gt;100bps: {cnt}"

    oi_worst = oi_sum.get("worst_divergence") or {}
    oi_worst_pct = _fmt_pct(oi_worst.get("gap_pct"))
    oi_worst_sub = (
        f"1inch at {oi_worst.get('target_utc', '')[11:19]} UTC (Block #{oi_worst.get('block_number', 0)})"
        if oi_worst
        else "No divergence recorded"
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETH Crash Oracle Analysis: {crash_id} ({date_utc})</title>
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
    --accent-bin: #06b6d4;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: var(--bg-dark);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .container {{ max-width: 1240px; margin: 0 auto; }}
  header {{ margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #fff; }}
  .meta {{ color: var(--text-muted); font-size: 13px; display: flex; gap: 16px; flex-wrap: wrap; }}
  .caveat-box {{
    background: rgba(245, 158, 11, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.3);
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 20px;
    font-size: 12px;
    color: #fcd34d;
  }}
  .caveat-box strong {{ color: #fbbf24; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .card h3 {{ margin: 0 0 8px 0; font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .val {{ font-size: 24px; font-weight: 700; color: #fff; }}
  .card .sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .chart-box {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
  .chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }}
  .legend {{ display: flex; gap: 16px; font-size: 12px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 2px; }}
  svg {{ width: 100%; height: auto; display: block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: #182234; color: var(--text-muted); font-weight: 600; position: sticky; top: 0; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .table-box {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; max-height: 480px; margin-bottom: 24px; }}
  .btn-group {{ display: flex; gap: 8px; margin-top: 12px; }}
  .btn {{ background: #334155; color: #fff; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 12px; border: 1px solid var(--border); }}
  .btn:hover {{ background: #475569; }}
  code {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; }}
  a {{ color: var(--primary); text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <a href="index.html" class="btn" style="margin-bottom: 12px; display: inline-block;">&larr; Back to Crash Overview</a>
    <h1>ETH Crash: {crash_id} ({date_utc}) — Drop: {drop_pct}%</h1>
    <div class="meta">
      <span><strong>Window:</strong> {manifest.get("window_start_utc")} &rarr; {manifest.get("window_end_utc")}</span>
      <span><strong>Targets:</strong> 145 points (600s intervals)</span>
      <span><strong>Duration:</strong> {manifest.get("duration_seconds", 0)}s</span>
    </div>
    <div class="btn-group">
      <a class="btn" href="{crash_id}.json" download>Download JSON</a>
      <a class="btn" href="{crash_id}.csv" download>Download CSV</a>
    </div>
  </header>

  <div class="caveat-box">
    <strong>Methodological Caveats:</strong>
    <ul style="margin: 4px 0 0 16px; padding: 0;">
      <li>Sample counts (145 points per 24h window) are discrete snapshots, not continuous duration monitoring; 10-minute sampling intervals can miss intra-sample spikes.</li>
      <li>Prices are size-independent reference/oracle rates, not executable routes with size-dependent slippage or market impact.</li>
      <li>Binance ETHUSDC minute candles match the most recent completed candle (completion_time = openTime + 60 &le; block.timestamp); missing data fails closed to null without USDT fallback.</li>
    </ul>
  </div>

  <div class="grid">
    <div class="card">
      <h3>1inch vs Chainlink</h3>
      <div class="val">{_fmt_pct(oi_sum.get("median_gap_pct"))}</div>
      <div class="sub">{_fmt_sub(oi_sum)}</div>
    </div>
    <div class="card">
      <h3>UniV3 300s vs Chainlink</h3>
      <div class="val">{_fmt_pct(u3_sum.get("median_gap_pct"))}</div>
      <div class="sub">{_fmt_sub(u3_sum)}</div>
    </div>
    <div class="card">
      <h3>Binance ETH/USDC vs Chainlink</h3>
      <div class="val">{_fmt_pct(bin_sum.get("median_gap_pct"))}</div>
      <div class="sub">{_fmt_sub(bin_sum)}</div>
    </div>
    <div class="card">
      <h3>Worst Divergence (Abs)</h3>
      <div class="val">{oi_worst_pct}</div>
      <div class="sub">{oi_worst_sub}</div>
    </div>
  </div>

  <div class="chart-box">
    <div class="chart-header">
      <div><strong>Price Trajectory (USDC per ETH)</strong></div>
      <div class="legend">
        <div class="legend-item"><div class="dot" style="background: {series_config["chainlink"]["color"]}"></div>{series_config["chainlink"]["name"]}</div>
        <div class="legend-item"><div class="dot" style="background: {series_config["oneinch"]["color"]}"></div>{series_config["oneinch"]["name"]}</div>
        <div class="legend-item"><div class="dot" style="background: {series_config["univ3_twap_300"]["color"]}"></div>{series_config["univ3_twap_300"]["name"]}</div>
        <div class="legend-item"><div class="dot" style="background: {series_config["univ3_twap_60"]["color"]}"></div>{series_config["univ3_twap_60"]["name"]}</div>
        <div class="legend-item"><div class="dot" style="background: {series_config["binance"]["color"]}"></div>{series_config["binance"]["name"]}</div>
      </div>
    </div>
    <svg viewBox="0 0 {width} {height}">
      {"".join(y_ticks_html)}
      {"".join(x_ticks_html)}
      <path fill="none" stroke="{series_config["chainlink"]["color"]}" stroke-width="{series_config["chainlink"]["width"]}" stroke-linecap="round" stroke-linejoin="round" d="{svg_paths["chainlink"]}"/>
      <path fill="none" stroke="{series_config["oneinch"]["color"]}" stroke-width="{series_config["oneinch"]["width"]}" stroke-linecap="round" stroke-linejoin="round" d="{svg_paths["oneinch"]}"/>
      <path fill="none" stroke="{series_config["univ3_twap_300"]["color"]}" stroke-width="{series_config["univ3_twap_300"]["width"]}" stroke-dasharray="{series_config["univ3_twap_300"]["dash"]}" stroke-linecap="round" stroke-linejoin="round" d="{svg_paths["univ3_twap_300"]}"/>
      <path fill="none" stroke="{series_config["univ3_twap_60"]["color"]}" stroke-width="{series_config["univ3_twap_60"]["width"]}" stroke-opacity="0.5" stroke-linecap="round" stroke-linejoin="round" d="{svg_paths["univ3_twap_60"]}"/>
      <path fill="none" stroke="{series_config["binance"]["color"]}" stroke-width="{series_config["binance"]["width"]}" stroke-dasharray="{series_config["binance"]["dash"]}" stroke-linecap="round" stroke-linejoin="round" d="{svg_paths["binance"]}"/>
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
          <th>Binance Close</th>
          <th>1i vs CL</th>
          <th>U3 vs CL</th>
          <th>Bin vs CL</th>
          <th>ETH Age</th>
          <th>USDC Age</th>
          <th>Bin Lag</th>
        </tr>
      </thead>
      <tbody>
        {"".join(table_rows_html)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    return html_content


def build_master_index_html(
    manifest: dict[str, Any],
    crashes_meta: list[dict[str, Any]],
) -> str:
    """Builds the standalone HTML index overview linking all 5 crashes."""
    table_rows = []
    for c in crashes_meta:
        c_id = c["id"]
        d_utc = c["dateUtc"]
        t_utc = c["crashUtc"]
        drop = c["dropPct"]
        h_open = c.get("hourOpen", 0.0)
        h_low = c.get("hourLow", 0.0)

        sums = c.get("summaries", {})
        oi_max_neg = sums.get("oneinch", {}).get("max_negative_gap_pct")
        u3_max_neg = sums.get("univ3_twap_300", {}).get("max_negative_gap_pct")
        bin_max_neg = sums.get("binance", {}).get("max_negative_gap_pct")

        oi_str = f"{oi_max_neg:+.2f}%" if oi_max_neg is not None else "-"
        u3_str = f"{u3_max_neg:+.2f}%" if u3_max_neg is not None else "-"
        bin_str = f"{bin_max_neg:+.2f}%" if bin_max_neg is not None else "-"

        table_rows.append(
            f"<tr>"
            f"<td><strong><a href='{c_id}.html'>{c_id}</a></strong></td>"
            f"<td>{d_utc[:10]}</td>"
            f"<td><code>{t_utc}</code></td>"
            f"<td>${h_open:.2f} &rarr; ${h_low:.2f}</td>"
            f"<td><span class='badge-drop'>-{drop:.2f}%</span></td>"
            f"<td><code>{oi_str}</code></td>"
            f"<td><code>{u3_str}</code></td>"
            f"<td><code>{bin_str}</code></td>"
            f"<td><a class='btn-sm' href='{c_id}.html'>View Report &rarr;</a></td>"
            f"</tr>"
        )

    crash_count = manifest.get("crash_count", len(crashes_meta))
    total_pins = manifest.get("total_pins", crash_count * 145)
    plural = "es" if crash_count != 1 else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Binance ETH Crashes: Oracle & Reference Sweep</title>
<style>
  :root {{
    --bg-dark: #0f172a;
    --card-bg: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: var(--bg-dark);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .container {{ max-width: 1240px; margin: 0 auto; }}
  header {{ margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }}
  h1 {{ margin: 0 0 8px 0; font-size: 26px; color: #fff; }}
  .meta {{ color: var(--text-muted); font-size: 13px; display: flex; gap: 16px; flex-wrap: wrap; }}
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
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: #182234; color: var(--text-muted); font-weight: 600; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .table-box {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
  .badge-drop {{ background: #7f1d1d; color: #fca5a5; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }}
  .btn-group {{ display: flex; gap: 8px; margin-top: 12px; }}
  .btn {{ background: #334155; color: #fff; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 12px; border: 1px solid var(--border); }}
  .btn:hover {{ background: #475569; }}
  .btn-sm {{ background: #1e3a8a; color: #bfdbfe; padding: 4px 8px; border-radius: 4px; text-decoration: none; font-size: 11px; }}
  .btn-sm:hover {{ background: #1d4ed8; }}
  code {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; }}
  a {{ color: var(--primary); text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Historical {crash_count} Binance ETH Crash{plural}: Oracle Sweep</h1>
    <div class="meta">
      <span><strong>Scope:</strong> {crash_count} ranked independent ETH crash{plural} (2024-08-16..2026-09-08, min 7d apart)</span>
      <span><strong>Windows:</strong> 24h (-12h to +12h around trough) &bull; 145 targets every 600s</span>
      <span><strong>Total Target Pins:</strong> {total_pins} blocks</span>
      <span><strong>Generated UTC:</strong> {manifest.get("run_timestamp_utc", "")}</span>
    </div>
    <div class="btn-group">
      <a class="btn" href="five-crash-oracles.json" download>Download Full JSON ({total_pins} Pins)</a>
      <a class="btn" href="five-crash-oracles.csv" download>Download Full CSV</a>
      <a class="btn" href="raw-evidence.json" download>Download Raw Multicall Evidence</a>
    </div>
  </header>

  <div class="caveat-box">
    <strong>Methodological Caveats:</strong>
    <ul style="margin: 4px 0 0 16px; padding: 0;">
      <li>Sample counts (145 discrete points per 24h window, {total_pins} total) do not constitute continuous duration monitoring; 10-minute sampling can miss transient spikes or intra-block drops.</li>
      <li>Prices are size-independent reference/oracle rates, not executable routes with size-dependent slippage or market impact.</li>
      <li>Binance ETHUSDC minute candles match the most recent completed candle (completion_time = openTime + 60 &le; block.timestamp); missing data fails closed to null without USDT fallback.</li>
    </ul>
  </div>

  <div class="table-box">
    <table>
      <thead>
        <tr>
          <th>Crash ID</th>
          <th>Date</th>
          <th>Trough (UTC)</th>
          <th>Hour Span</th>
          <th>Drop %</th>
          <th>1i vs CL (Max Neg)</th>
          <th>UniV3 300s (Max Neg)</th>
          <th>Binance (Max Neg)</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {"".join(table_rows)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    return html


def write_atomic_json(path: Path, data: Any) -> str:
    """Atomically writes JSON payload to disk and returns its SHA-256 hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    encoded = json.dumps(data, indent=2).encode("utf-8")
    tmp.write_bytes(encoded)
    tmp.replace(path)
    return hashlib.sha256(encoded).hexdigest()


def write_atomic_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Atomically writes CSV rows to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def write_atomic_text(path: Path, text: str) -> None:
    """Atomically writes text file to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run_crash_window(
    client: RpcClient,
    crash_meta: dict[str, Any],
    shared_cache: dict[int, BlockRef],
    workers: int = 4,
    cache_root: Path | None = None,
    binance_base_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Executes target block resolution and multicall queries for a single crash window."""
    crash_id = crash_meta["id"]
    w_start = crash_meta["windowStart"]
    w_end = crash_meta["windowEnd"]
    targets = generate_window_targets(w_start, w_end, step_seconds=600)

    # 1. Resolve 145 target blocks using shared cache
    t_res_start = time.perf_counter()
    resolved_blocks = resolve_target_blocks_shared(client, targets, cache=shared_cache)
    t_res_end = time.perf_counter()

    # 2. Mainnet 1 deployment getCode verification at start of window
    start_block = resolved_blocks[0]["block_ref"]
    deployments = verify_contract_deployments(client, start_block)

    # 3. Load Binance minute file
    binance_file = crash_meta.get("binanceMinuteFile")
    binance_data = load_binance_minute_file(binance_file, base_dir=binance_base_dir)

    if cache_root is None:
        cache_root = PROJECT_ROOT / "outputs/five-crash-oracles/rpc-cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    # 4. Multicall3 batch query with bounded workers
    _local = threading.local()

    def worker_query(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not hasattr(_local, "client"):
            if isinstance(client, RpcClient):
                _local.client = RpcClient(cache_root=cache_root)
            else:
                _local.client = client
            _local.multicall = Multicall3(_local.client)
        block_ref = entry["block_ref"]
        decoded_row, raw_evidence = query_block_oracle_references(_local.multicall, block_ref)
        return decoded_row, raw_evidence

    t_query_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        batch_results = list(executor.map(worker_query, resolved_blocks))
    t_query_end = time.perf_counter()

    # 5. Assemble rows
    rows: list[dict[str, Any]] = []
    raw_evidences: list[dict[str, Any]] = []

    for entry, (decoded_data, raw_data) in zip(resolved_blocks, batch_results, strict=True):
        bin_match = match_binance_candle(binance_data, entry["block_timestamp"])

        row = {
            "crash_id": crash_id,
            "target_index": entry["target_index"],
            "target_timestamp": entry["target_timestamp"],
            "target_utc": entry["target_utc"],
            "block_number": entry["block_number"],
            "block_hash": entry["block_hash"],
            "block_timestamp": entry["block_timestamp"],
            "block_utc": entry["block_utc"],
            "diff_seconds": entry["diff_seconds"],
            "next_block_number": entry["next_block_number"],
            "next_block_timestamp": entry["next_block_timestamp"],
            "verified_bounds": entry["verified_bounds"],
            "chainlink": decoded_data["chainlink"],
            "oneinch": decoded_data["oneinch"],
            "univ3_twap_300": decoded_data["univ3_twap_300"],
            "univ3_twap_60": decoded_data["univ3_twap_60"],
            "binance": bin_match,
        }
        rows.append(row)
        raw_evidences.append(
            {
                "crash_id": crash_id,
                "target_index": entry["target_index"],
                "target_timestamp": entry["target_timestamp"],
                "raw_evidence": raw_data,
                "binance_match": bin_match,
            }
        )

    # 6. Calculate summary divergences
    summaries = calculate_crash_gaps(rows)

    manifest = {
        "crash_id": crash_id,
        "date_utc": crash_meta.get("dateUtc"),
        "crash_utc": crash_meta.get("crashUtc"),
        "crash_timestamp": crash_meta.get("crashTimestamp"),
        "hour_open": crash_meta.get("hourOpen"),
        "hour_low": crash_meta.get("hourLow"),
        "drop_pct": crash_meta.get("dropPct"),
        "window_start_utc": rows[0]["target_utc"],
        "window_end_utc": rows[-1]["target_utc"],
        "target_count": len(rows),
        "duration_seconds": round((t_res_end - t_res_start) + (t_query_end - t_query_start), 2),
        "timings": {
            "header_resolution_seconds": round(t_res_end - t_res_start, 2),
            "multicall_queries_seconds": round(t_query_end - t_query_start, 2),
        },
        "deployments_at_window_start": deployments,
        "caveats": CAVEATS,
        "summaries": summaries,
    }

    return manifest, rows, raw_evidences


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collector and report generator for five Binance ETH crashes."
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        default=PROJECT_ROOT / "outputs/five-crash-oracles/selection.json",
        help="Path to peer selection.json file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs/five-crash-oracles",
        help="Directory to write output artifacts",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of bounded concurrent RPC workers (default 4)",
    )
    parser.add_argument(
        "--crash-id",
        type=str,
        default=None,
        help="Optional crash ID to run singly (e.g. benchmark one event)",
    )
    args = parser.parse_args()

    selection_path = args.selection_file
    output_dir = args.output_dir

    if not selection_path.exists():
        print(f"STATUS: Selection file not found at {selection_path}")
        print("READY TO RUN promptly once peer selection is available.")
        sys.exit(0)

    start_perf = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_root = output_dir / "rpc-cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    master_client = RpcClient(cache_root=cache_root)
    chain_id = master_client.chain_id()
    if chain_id != 1:
        raise RuntimeError(f"Configured RPC chain ID is {chain_id}, expected 1 (Ethereum Mainnet)")

    selection_data = parse_selection_file(selection_path)
    crashes = selection_data.get("crashes", [])
    if args.crash_id:
        crashes = [c for c in crashes if c["id"] == args.crash_id]
        if not crashes:
            raise ValueError(f"Crash ID '{args.crash_id}' not found in selection file")

    print(f"=== Running Five Crash Oracle Check across {len(crashes)} crash windows ===")
    shared_cache: dict[int, BlockRef] = {}

    all_crashes_meta: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_raw_evidences: list[dict[str, Any]] = []

    csv_flat_rows: list[dict[str, Any]] = []
    fieldnames = [
        "crash_id",
        "target_index",
        "target_timestamp",
        "target_utc",
        "block_number",
        "block_hash",
        "block_timestamp",
        "block_utc",
        "diff_seconds",
        "next_block_number",
        "next_block_timestamp",
        "chainlink_price",
        "oneinch_price",
        "univ3_twap_300_price",
        "univ3_twap_60_price",
        "binance_price",
        "oneinch_gap_pct",
        "univ3_300_gap_pct",
        "binance_gap_pct",
        "chainlink_eth_age_seconds",
        "chainlink_usdc_age_seconds",
        "binance_completion_time",
        "binance_close_time",
        "binance_lag_seconds",
        "chainlink_status",
        "oneinch_status",
        "univ3_300_status",
        "binance_status",
    ]

    for crash_meta in crashes:
        c_id = crash_meta["id"]
        print(
            f"\nProcessing crash {c_id} ({crash_meta.get('dateUtc')}, drop -{crash_meta.get('dropPct')}%)..."
        )
        c_manifest, c_rows, c_evidences = run_crash_window(
            client=master_client,
            crash_meta=crash_meta,
            shared_cache=shared_cache,
            workers=args.workers,
            cache_root=cache_root,
            binance_base_dir=selection_path.parent,
        )

        all_crashes_meta.append(
            {**crash_meta, "manifest": c_manifest, "summaries": c_manifest["summaries"]}
        )
        all_rows.extend(c_rows)
        all_raw_evidences.extend(c_evidences)

        # Write per-crash JSON, CSV, HTML
        crash_json_path = output_dir / f"{c_id}.json"
        write_atomic_json(crash_json_path, {"manifest": c_manifest, "rows": c_rows})

        crash_html_path = output_dir / f"{c_id}.html"
        html_code = build_crash_html_report(c_manifest, c_rows, c_manifest["summaries"])
        write_atomic_text(crash_html_path, html_code)

        per_crash_flat: list[dict[str, Any]] = []
        for r in c_rows:
            cl_p = r["chainlink"]["price"]
            oi_p = r["oneinch"]["price"]
            u3_p = r["univ3_twap_300"]["price"]
            u6_p = r["univ3_twap_60"]["price"]
            bin_p = r["binance"]["price"]

            oi_gap_pct = round(((oi_p - cl_p) / cl_p * 100.0), 4) if (cl_p and oi_p) else ""
            u3_gap_pct = round(((u3_p - cl_p) / cl_p * 100.0), 4) if (cl_p and u3_p) else ""
            bin_gap_pct = round(((bin_p - cl_p) / cl_p * 100.0), 4) if (cl_p and bin_p) else ""

            row_dict = {
                "crash_id": c_id,
                "target_index": r["target_index"],
                "target_timestamp": r["target_timestamp"],
                "target_utc": r["target_utc"],
                "block_number": r["block_number"],
                "block_hash": r["block_hash"],
                "block_timestamp": r["block_timestamp"],
                "block_utc": r["block_utc"],
                "diff_seconds": r["diff_seconds"],
                "next_block_number": r.get("next_block_number", ""),
                "next_block_timestamp": r.get("next_block_timestamp", ""),
                "chainlink_price": cl_p if cl_p is not None else "",
                "oneinch_price": oi_p if oi_p is not None else "",
                "univ3_twap_300_price": u3_p if u3_p is not None else "",
                "univ3_twap_60_price": u6_p if u6_p is not None else "",
                "binance_price": bin_p if bin_p is not None else "",
                "oneinch_gap_pct": oi_gap_pct,
                "univ3_300_gap_pct": u3_gap_pct,
                "binance_gap_pct": bin_gap_pct,
                "chainlink_eth_age_seconds": r["chainlink"].get("eth_age_seconds", ""),
                "chainlink_usdc_age_seconds": r["chainlink"].get("usdc_age_seconds", ""),
                "binance_completion_time": r["binance"].get("completion_time", ""),
                "binance_close_time": r["binance"].get("close_time", ""),
                "binance_lag_seconds": r["binance"].get("lag_seconds", ""),
                "chainlink_status": r["chainlink"]["status"],
                "oneinch_status": r["oneinch"]["status"],
                "univ3_300_status": r["univ3_twap_300"]["status"],
                "binance_status": r["binance"]["status"],
            }
            per_crash_flat.append(row_dict)
            csv_flat_rows.append(row_dict)

        crash_csv_path = output_dir / f"{c_id}.csv"
        write_atomic_csv(crash_csv_path, per_crash_flat, fieldnames)
        print(f"  Wrote {c_id}.json, {c_id}.csv, {c_id}.html")

    total_duration = round(time.perf_counter() - start_perf, 2)
    master_manifest = {
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "chain_id": chain_id,
        "crash_count": len(crashes),
        "total_pins": len(all_rows),
        "duration_seconds": total_duration,
        "selection_source": selection_data.get("selection"),
        "caveats": CAVEATS,
        "crashes": [c["manifest"] for c in all_crashes_meta],
    }

    # Write master outputs
    master_json_path = output_dir / "five-crash-oracles.json"
    json_cid = write_atomic_json(master_json_path, {"manifest": master_manifest, "rows": all_rows})
    print(f"\nWrote Master JSON: {master_json_path} (SHA-256: {json_cid})")

    master_csv_path = output_dir / "five-crash-oracles.csv"
    write_atomic_csv(master_csv_path, csv_flat_rows, fieldnames)
    print(f"Wrote Master CSV: {master_csv_path}")

    raw_path = output_dir / "raw-evidence.json"
    write_atomic_json(raw_path, {"manifest": master_manifest, "evidence": all_raw_evidences})
    print(f"Wrote Raw Evidence: {raw_path}")

    index_html_path = output_dir / "index.html"
    index_html = build_master_index_html(master_manifest, all_crashes_meta)
    write_atomic_text(index_html_path, index_html)
    print(f"Wrote Master Index HTML: {index_html_path}")

    print(f"\nCompleted all {len(crashes)} crashes in {total_duration}s.")


if __name__ == "__main__":
    main()
