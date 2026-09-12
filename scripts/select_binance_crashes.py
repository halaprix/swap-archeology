#!/usr/bin/env python3
"""Objective selection and artifact generation for Five Binance ETH Crashes.

Selects five Ethereum Binance crashes for oracle and reference price comparison:
- Date universe: 2024-08-16 00:00:00 UTC to 2026-09-08 23:59:59 UTC inclusive (754 complete days, 18,096 hours)
  Excludes prior dates because 1inch spot aggregator OffchainOracle deployed Aug 15, 2024 (block 20535992).
- Metric: Hourly open-to-low percentage decline ((open - low) / open * 100.0)
- Cluster selection: Greedy selection by largest drop first with minimum 7 days separation between trough dates.
  Exported in chronological order.
- Trough identification: Fetch 1m ETHUSDT for selected hours to find the minute containing the hourly low.
  troughUnix uses minute CLOSE floor seconds (low time only known within minute; no future candle close alignment).
- 24h centered windows: 12 hours before to 12 hours after trough (145 targets at 600s intervals).
- Standardized minute candle files: ETHUSDC 1m candles for the full 24h window PLUS prior 1 minute
  (1,442 candles covering openTime = windowStart - 119 to closeTime = windowEnd), ensuring completed candle matching
  for all 145 targets. ETHUSDT 1m candles saved separately for clear non-pegged provenance.
- Raw archive integrity: Official Binance Vision public data archives verified with SHA256 checksums.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BINANCE_VISION_BASE = "https://data.binance.vision/data/spot"

UNIVERSE_START_DATE = date(2024, 8, 16)
UNIVERSE_END_DATE = date(2026, 9, 8)
UNIVERSE_START_TS = int(datetime(2024, 8, 16, 0, 0, 0, tzinfo=UTC).timestamp())
UNIVERSE_END_TS = int(datetime(2026, 9, 8, 23, 59, 59, tzinfo=UTC).timestamp())
UNIVERSE_TOTAL_DAYS = (UNIVERSE_END_DATE - UNIVERSE_START_DATE).days + 1  # 754
UNIVERSE_TOTAL_HOURS = UNIVERSE_TOTAL_DAYS * 24  # 18,096


def parse_timestamp_seconds(raw_ts: int) -> int:
    """Converts raw Binance timestamp (microseconds in 2025+ or milliseconds earlier) to integer seconds."""
    if raw_ts > 10**14:
        # Microseconds (16 digits)
        return raw_ts // 1_000_000
    # Milliseconds (13 digits)
    return raw_ts // 1_000


def download_with_sha256(
    url: str,
    dest_path: Path,
    checksum_path: Path,
    timeout: int = 60,
) -> tuple[bytes, str]:
    """Downloads an archive and its .CHECKSUM file, verifies SHA256, and caches on disk."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    chk_url = url + ".CHECKSUM"

    # Check if already cached and valid
    if dest_path.exists() and checksum_path.exists():
        content = dest_path.read_bytes()
        chk_text = checksum_path.read_text(encoding="utf-8").strip()
        expected_sha = chk_text.split()[0].lower()
        actual_sha = hashlib.sha256(content).hexdigest().lower()
        if expected_sha == actual_sha:
            return content, actual_sha

    # Download archive
    req = urllib.request.Request(url, headers={"User-Agent": "swaparch-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read()

    # Download checksum
    chk_req = urllib.request.Request(chk_url, headers={"User-Agent": "swaparch-research/1.0"})
    with urllib.request.urlopen(chk_req, timeout=timeout) as resp:
        chk_bytes = resp.read()

    chk_text = chk_bytes.decode("utf-8").strip()
    expected_sha = chk_text.split()[0].lower()
    actual_sha = hashlib.sha256(content).hexdigest().lower()

    if actual_sha != expected_sha:
        raise ValueError(f"SHA256 mismatch for {url}: expected {expected_sha}, got {actual_sha}")

    dest_path.write_bytes(content)
    checksum_path.write_bytes(chk_bytes)
    return content, actual_sha


def parse_kline_rows(content: bytes) -> list[dict[str, Any]]:
    """Parses kline rows from a zip archive bytes into normalized candles."""
    candles = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    if not row or not row[0].isdigit():
                        continue
                    raw_open_time = int(row[0])
                    raw_close_time = int(row[6])
                    open_time = parse_timestamp_seconds(raw_open_time)
                    close_time = parse_timestamp_seconds(raw_close_time)
                    o = float(row[1])
                    h = float(row[2])
                    l = float(row[3])
                    c = float(row[4])
                    candles.append(
                        {
                            "openTime": open_time,
                            "closeTime": close_time,
                            "open": o,
                            "high": h,
                            "low": l,
                            "close": c,
                            "rawOpenTime": raw_open_time,
                            "rawCloseTime": raw_close_time,
                        }
                    )
    candles.sort(key=lambda x: x["openTime"])
    return candles


def get_monthly_symbols_urls(
    symbol: str,
    start_year_month: tuple[int, int],
    end_year_month: tuple[int, int],
) -> list[tuple[str, str]]:
    """Generates (filename, url) list for monthly archives."""
    archives = []
    y, m = start_year_month
    end_y, end_m = end_year_month
    while (y < end_y) or (y == end_y and m <= end_m):
        ym_str = f"{y:04d}-{m:02d}"
        name = f"{symbol}-1h-{ym_str}.zip"
        url = f"{BINANCE_VISION_BASE}/monthly/klines/{symbol}/1h/{name}"
        archives.append((name, url))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return archives


def get_daily_symbols_urls(
    symbol: str,
    interval: str,
    start_d: date,
    end_d: date,
) -> list[tuple[str, str]]:
    """Generates (filename, url) list for daily archives."""
    archives = []
    curr = start_d
    while curr <= end_d:
        day_str = curr.strftime("%Y-%m-%d")
        name = f"{symbol}-{interval}-{day_str}.zip"
        url = f"{BINANCE_VISION_BASE}/daily/klines/{symbol}/{interval}/{name}"
        archives.append((name, url))
        curr = date.fromordinal(curr.toordinal() + 1)
    return archives


def load_universe_1h_candles(
    archives_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Downloads and parses all 1h archives for the universe 2024-08-16..2026-09-08.

    Returns:
    - universe_hours: exactly 18,096 continuous hourly candles
    - archive_manifests: metadata and SHA256 checksums for each archive
    """
    archives_dir.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    all_raw_candles: list[dict[str, Any]] = []

    # 1. Monthly archives: 2024-08 through 2026-08 (25 months)
    monthly_specs = get_monthly_symbols_urls("ETHUSDT", (2024, 8), (2026, 8))
    for name, url in monthly_specs:
        zip_path = archives_dir / name
        chk_path = archives_dir / f"{name}.CHECKSUM"
        content, sha = download_with_sha256(url, zip_path, chk_path)
        candles = parse_kline_rows(content)
        manifests.append(
            {
                "name": name,
                "url": url,
                "type": "monthly_1h",
                "sizeBytes": len(content),
                "sha256": sha,
                "checksumVerified": True,
                "candleCount": len(candles),
            }
        )
        all_raw_candles.extend(candles)

    # 2. Daily archives: 2026-09-01 through 2026-09-08 (8 days)
    daily_specs = get_daily_symbols_urls("ETHUSDT", "1h", date(2026, 9, 1), date(2026, 9, 8))
    for name, url in daily_specs:
        zip_path = archives_dir / name
        chk_path = archives_dir / f"{name}.CHECKSUM"
        content, sha = download_with_sha256(url, zip_path, chk_path)
        candles = parse_kline_rows(content)
        manifests.append(
            {
                "name": name,
                "url": url,
                "type": "daily_1h",
                "sizeBytes": len(content),
                "sha256": sha,
                "checksumVerified": True,
                "candleCount": len(candles),
            }
        )
        all_raw_candles.extend(candles)

    # Deduplicate and sort by openTime
    unique_by_open: dict[int, dict[str, Any]] = {}
    for c in all_raw_candles:
        unique_by_open[c["openTime"]] = c

    sorted_candles = [unique_by_open[k] for k in sorted(unique_by_open.keys())]

    # Filter to universe [UNIVERSE_START_TS, UNIVERSE_END_TS]
    universe_hours = [
        c for c in sorted_candles if UNIVERSE_START_TS <= c["openTime"] <= UNIVERSE_END_TS
    ]

    if len(universe_hours) != UNIVERSE_TOTAL_HOURS:
        raise ValueError(
            f"Universe hours count mismatch: expected {UNIVERSE_TOTAL_HOURS}, got {len(universe_hours)}"
        )

    # Verify continuity: every consecutive candle must be exactly 3600 seconds apart
    for i in range(len(universe_hours) - 1):
        delta = universe_hours[i + 1]["openTime"] - universe_hours[i]["openTime"]
        if delta != 3600:
            dt_curr = datetime.fromtimestamp(universe_hours[i]["openTime"], UTC).isoformat()
            dt_next = datetime.fromtimestamp(universe_hours[i + 1]["openTime"], UTC).isoformat()
            raise ValueError(f"Hour continuity gap at {dt_curr} -> {dt_next} (delta {delta}s)")

    # Compute drop percentage for each hour
    for c in universe_hours:
        o = c["open"]
        l = c["low"]
        drop_pct = (o - l) / o * 100.0
        c["dropPct"] = drop_pct
        c["openUtc"] = datetime.fromtimestamp(c["openTime"], UTC).isoformat().replace("+00:00", "Z")
        c["closeUtc"] = (
            datetime.fromtimestamp(c["closeTime"], UTC).isoformat().replace("+00:00", "Z")
        )

    return universe_hours, manifests


def rank_and_select_clusters(
    universe_hours: list[dict[str, Any]],
    min_separation_days: int = 7,
    count: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ranks all universe hours by dropPct descending and greedily selects 5 clusters >= 7 days apart.

    Returns:
    - ranked_candidates: all 18,096 hours sorted by dropPct descending
    - selected_crashes_chrono: the 5 selected crashes exported in chronological order
    """
    ranked = sorted(universe_hours, key=lambda x: x["dropPct"], reverse=True)

    selected: list[dict[str, Any]] = []
    for cand in ranked:
        cand_date = datetime.fromtimestamp(cand["openTime"], UTC).date()
        too_close = False
        for s in selected:
            s_date = datetime.fromtimestamp(s["openTime"], UTC).date()
            if abs((cand_date - s_date).days) < min_separation_days:
                too_close = True
                break
        if not too_close:
            selected.append(cand)
            if len(selected) == count:
                break

    if len(selected) != count:
        raise ValueError(f"Failed to find {count} clusters separated by {min_separation_days} days")

    # Export in chronological order: "(largestfirstthenchronologicalexport)"
    selected_chrono = sorted(selected, key=lambda x: x["openTime"])
    return ranked, selected_chrono


def identify_minute_trough(
    crash_hour: dict[str, Any],
    archives_dir: Path,
) -> dict[str, Any]:
    """Fetches 1m ETHUSDT for the selected crash hour and identifies the minute containing the low.

    Returns trough metadata:
    - troughUnix: minute CLOSE floor seconds (low time only known within minute; no future candle close alignment)
    - crashTimestamp: troughUnix
    - crashUtc: ISO UTC string
    - troughMinuteLow: lowest price observed in that minute
    - minuteOpenTime: open of the minute containing the low
    - minuteCloseTime: close of the minute containing the low
    """
    dt_hour = datetime.fromtimestamp(crash_hour["openTime"], UTC)
    date_str = dt_hour.strftime("%Y-%m-%d")
    hour_target = dt_hour.hour

    name = f"ETHUSDT-1m-{date_str}.zip"
    url = f"{BINANCE_VISION_BASE}/daily/klines/ETHUSDT/1m/{name}"
    zip_path = archives_dir / name
    chk_path = archives_dir / f"{name}.CHECKSUM"
    content, sha = download_with_sha256(url, zip_path, chk_path)

    candles = parse_kline_rows(content)
    # Filter to the selected crash hour
    hour_candles = [
        c for c in candles if datetime.fromtimestamp(c["openTime"], UTC).hour == hour_target
    ]
    if not hour_candles:
        raise ValueError(f"No minute candles found for {date_str} hour {hour_target:02d}")

    # Find the minute containing the low
    min_candle = min(hour_candles, key=lambda x: (x["low"], x["close"]))

    # troughUnix uses minute CLOSE floor seconds
    # In Binance 1m candles, closeTime is openTime + 59 seconds.
    trough_unix = min_candle["closeTime"]
    crash_utc = datetime.fromtimestamp(trough_unix, UTC).isoformat().replace("+00:00", "Z")

    return {
        "troughUnix": trough_unix,
        "crashTimestamp": trough_unix,
        "crashUtc": crash_utc,
        "troughMinuteLow": min_candle["low"],
        "minuteOpenTime": min_candle["openTime"],
        "minuteCloseTime": min_candle["closeTime"],
        "minuteOpenUtc": datetime.fromtimestamp(min_candle["openTime"], UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "archiveName": name,
        "archiveUrl": url,
        "archiveSha256": sha,
    }


def extract_standardized_minute_candles(
    symbol: str,
    window_start: int,
    window_end: int,
    archives_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extracts standardized 1m candles for FULL 24h window PLUS prior 1 minute.

    Spans from openTime = windowStart - 119 (closeTime = windowStart - 60, the prior 1 minute)
    up to closeTime = windowEnd (openTime = windowEnd - 59).
    This ensures completed candle matching succeeds for all 145 targets every 600s,
    even when Ethereum block timestamps are slightly before target timestamps.
    """
    dt_start = datetime.fromtimestamp(window_start - 120, UTC).date()
    dt_end = datetime.fromtimestamp(window_end + 60, UTC).date()

    daily_specs = get_daily_symbols_urls(symbol, "1m", dt_start, dt_end)
    manifests: list[dict[str, Any]] = []
    all_candles: list[dict[str, Any]] = []

    for name, url in daily_specs:
        zip_path = archives_dir / name
        chk_path = archives_dir / f"{name}.CHECKSUM"
        content, sha = download_with_sha256(url, zip_path, chk_path)
        candles = parse_kline_rows(content)
        manifests.append(
            {
                "name": name,
                "url": url,
                "sha256": sha,
                "checksumVerified": True,
                "candleCount": len(candles),
            }
        )
        all_candles.extend(candles)

    # Deduplicate and sort
    by_open: dict[int, dict[str, Any]] = {}
    for c in all_candles:
        by_open[c["openTime"]] = c

    # Required candle range:
    # First candle: opens at windowStart - 119, closes at windowStart - 60 (prior 1 minute)
    # Last candle: opens at windowEnd - 59, closes at windowEnd
    earliest_open = window_start - 119
    latest_open = window_end - 59

    filtered_candles = []
    curr_open = earliest_open
    while curr_open <= latest_open:
        if curr_open not in by_open:
            raise ValueError(f"Missing {symbol} 1m candle at openTime {curr_open}")
        c = by_open[curr_open]
        filtered_candles.append(
            {
                "openTime": c["openTime"],
                "closeTime": c["closeTime"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
            }
        )
        curr_open += 60

    return filtered_candles, manifests


def build_selection_and_minute_artifacts(
    output_dir: Path,
    min_separation_days: int = 7,
    crash_count: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Executes the complete selection pipeline, writes all artifacts, and returns selection document."""
    output_dir.mkdir(parents=True, exist_ok=True)
    binance_dir = output_dir / "binance"
    archives_dir = binance_dir / "archives"
    binance_dir.mkdir(parents=True, exist_ok=True)
    archives_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load universe hours
    print(f"Loading universe 1h archives ({UNIVERSE_START_DATE} to {UNIVERSE_END_DATE})...")
    universe_hours, archive_manifests = load_universe_1h_candles(archives_dir)
    print(f"Loaded and validated {len(universe_hours)} continuous hourly candles.")

    # 2. Rank candidates and greedily select clusters
    print("Ranking candidates and selecting clusters...")
    ranked_candidates, selected_chrono = rank_and_select_clusters(
        universe_hours, min_separation_days=min_separation_days, count=crash_count
    )

    # Save ranked candidates
    ranked_candidates_file = binance_dir / "ranked-candidates.json"
    candidates_export = [
        {
            "rank": i + 1,
            "openTime": c["openTime"],
            "closeTime": c["closeTime"],
            "openUtc": c["openUtc"],
            "closeUtc": c["closeUtc"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "dropPct": round(c["dropPct"], 4),
        }
        for i, c in enumerate(ranked_candidates)
    ]
    ranked_candidates_file.write_text(
        json.dumps(candidates_export, indent=2) + "\n", encoding="utf-8"
    )

    # 3. For each selected crash, find 1m trough and extract 24h+1m windows
    crashes_list: list[dict[str, Any]] = []

    for idx, crash_hour in enumerate(selected_chrono, start=1):
        crash_id = f"crash-{idx}"
        date_utc = datetime.fromtimestamp(crash_hour["openTime"], UTC).strftime("%Y-%m-%d")
        print(f"Processing {crash_id} ({date_utc}, drop -{crash_hour['dropPct']:.2f}%)...")

        trough_info = identify_minute_trough(crash_hour, archives_dir)
        crash_timestamp = trough_info["crashTimestamp"]
        crash_utc = trough_info["crashUtc"]

        # 24h centered trough: 12h before and 12h after
        window_start = crash_timestamp - 12 * 3600
        window_end = crash_timestamp + 12 * 3600
        window_start_utc = (
            datetime.fromtimestamp(window_start, UTC).isoformat().replace("+00:00", "Z")
        )
        window_end_utc = datetime.fromtimestamp(window_end, UTC).isoformat().replace("+00:00", "Z")

        # Extract standardized ETHUSDC minute candles
        usdc_candles, usdc_manifests = extract_standardized_minute_candles(
            "ETHUSDC", window_start, window_end, archives_dir
        )
        # Extract ETHUSDT minute candles for separate provenance
        usdt_candles, usdt_manifests = extract_standardized_minute_candles(
            "ETHUSDT", window_start, window_end, archives_dir
        )

        usdc_filename = f"ETHUSDC-1m-{crash_id}.json"
        usdt_filename = f"ETHUSDT-1m-{crash_id}.json"
        usdc_file_rel = f"binance/{usdc_filename}"
        usdt_file_rel = f"binance/{usdt_filename}"

        usdc_payload = {
            "symbol": "ETHUSDC",
            "priceUnit": "USDC per ETH",
            "candles": usdc_candles,
            "provenance": {
                "crashId": crash_id,
                "dateUtc": date_utc,
                "crashTimestamp": crash_timestamp,
                "crashUtc": crash_utc,
                "hourOpen": crash_hour["open"],
                "hourLow": crash_hour["low"],
                "dropPct": round(crash_hour["dropPct"], 4),
                "windowStart": window_start,
                "windowEnd": window_end,
                "windowStartUtc": window_start_utc,
                "windowEndUtc": window_end_utc,
                "windowDurationSeconds": window_end - window_start,
                "candleCount": len(usdc_candles),
                "hasPriorMinute": True,
                "targetCount": 145,
                "targetIntervalSeconds": 600,
                "sourceArchives": usdc_manifests,
                "notes": (
                    "Standardized ETHUSDC 1m candles for full 24h window plus prior 1 minute. "
                    "No future candle close alignment."
                ),
            },
        }
        (binance_dir / usdc_filename).write_text(
            json.dumps(usdc_payload, indent=2) + "\n", encoding="utf-8"
        )

        usdt_payload = {
            "symbol": "ETHUSDT",
            "priceUnit": "USDT per ETH",
            "candles": usdt_candles,
            "provenance": {
                "crashId": crash_id,
                "dateUtc": date_utc,
                "crashTimestamp": crash_timestamp,
                "crashUtc": crash_utc,
                "windowStart": window_start,
                "windowEnd": window_end,
                "candleCount": len(usdt_candles),
                "sourceArchives": usdt_manifests,
                "notes": "Raw ETHUSDT spot 1m candles from Binance Vision. Clear USDT asset, no assumed 1:1 USDC peg.",
            },
        }
        (binance_dir / usdt_filename).write_text(
            json.dumps(usdt_payload, indent=2) + "\n", encoding="utf-8"
        )

        crash_obj = {
            "id": crash_id,
            "dateUtc": date_utc,
            "crashTimestamp": crash_timestamp,
            "crashUtc": crash_utc,
            "hourOpen": crash_hour["open"],
            "hourLow": crash_hour["low"],
            "dropPct": round(crash_hour["dropPct"], 4),
            "windowStart": window_start,
            "windowEnd": window_end,
            "binanceMinuteFile": usdc_file_rel,
            "provenance": {
                "symbol": "ETHUSDC",
                "priceUnit": "USDC per ETH",
                "binanceUsdcMinuteFile": usdc_file_rel,
                "binanceUsdtMinuteFile": usdt_file_rel,
                "hourOpenUtc": crash_hour["openUtc"],
                "hourCloseUtc": crash_hour["closeUtc"],
                "troughMinuteOpenUtc": trough_info["minuteOpenUtc"],
                "troughMinuteCloseUtc": crash_utc,
                "troughMinuteLow": trough_info["troughMinuteLow"],
                "windowStartUtc": window_start_utc,
                "windowEndUtc": window_end_utc,
                "targetCount": 145,
                "targetIntervalSeconds": 600,
                "minuteCandleCount": len(usdc_candles),
                "hasPriorMinute": True,
                "troughDiscoveryArchive": trough_info["archiveName"],
            },
        }
        crashes_list.append(crash_obj)

    # 4. Build coverage manifest
    coverage_doc = {
        "universeStartUtc": "2024-08-16T00:00:00Z",
        "universeEndUtc": "2026-09-08T23:59:59Z",
        "universeTotalDays": UNIVERSE_TOTAL_DAYS,
        "universeTotalHours": UNIVERSE_TOTAL_HOURS,
        "universeCompleteHours": len(universe_hours),
        "coverageComplete": True,
        "leadingPartialMonth": {
            "month": "2024-08",
            "startDateUtc": "2024-08-16T00:00:00Z",
            "hoursIncluded": 384,
            "hoursExcludedPrior": 360,
            "exclusionReason": (
                "Deployment compatibility: 1inch spot aggregator OffchainOracle deployed "
                "Aug 15 2024 (block 20535992); prior dates lack uniform deployment coverage"
            ),
        },
        "trailingPartialMonth": {
            "month": "2026-09",
            "endDateUtc": "2026-09-08T23:59:59Z",
            "daysIncluded": 8,
            "hoursIncluded": 192,
            "sourceType": "daily_archives",
        },
        "monthlyArchivesCount": 25,
        "dailyArchivesCount": 8,
        "totalHourlyArchivesCount": len(archive_manifests),
        "archiveVerification": "SHA256 verified against official .CHECKSUM files from data.binance.vision",
        "archiveTimestampHandling": (
            "Microsecond timestamps (>=1e14) converted to integer seconds via raw // 1_000_000; "
            "millisecond timestamps converted via raw // 1_000"
        ),
        "archives": archive_manifests,
    }
    (binance_dir / "coverage.json").write_text(
        json.dumps(coverage_doc, indent=2) + "\n", encoding="utf-8"
    )

    # 5. Build master selection.json
    selection_doc = {
        "schemaVersion": 1,
        "selection": {
            "startUtc": "2024-08-16T00:00:00Z",
            "endUtc": "2026-09-08T23:59:59Z",
            "metric": "hourly_open_to_low_percentage_decline",
            "minimumSeparationDays": min_separation_days,
            "source": "Binance_ETHUSDT_hourly_crashes",
            "dateRange": ["2024-08-16", "2026-09-08"],
            "minSpacingDays": min_separation_days,
            "windowHours": 24,
            "ranking": "hourly_open_low_drop_pct",
            "universeTotalDays": UNIVERSE_TOTAL_DAYS,
            "universeTotalHours": UNIVERSE_TOTAL_HOURS,
            "universeCompleteHours": len(universe_hours),
            "coverageComplete": True,
            "leadingPartialMonth": coverage_doc["leadingPartialMonth"],
            "trailingPartialMonth": coverage_doc["trailingPartialMonth"],
            "monthlyArchivesCount": coverage_doc["monthlyArchivesCount"],
            "dailyArchivesCount": coverage_doc["dailyArchivesCount"],
            "totalArchivesCount": coverage_doc["totalHourlyArchivesCount"],
            "selectionMethod": (
                "Rank all 18,096 hourly candles by (open - low) / open descending; "
                "greedily select candidate hours with trough dates >= 7 days apart from previously selected events; "
                "export in chronological order"
            ),
            "selectionLimitNote": (
                "Objectively selected by Binance ETH spot open-to-low percentage decline. "
                "Excludes pre-2024-08-16 crashes (e.g. 2024-08-05). Not all-time worst nor oracle-selected."
            ),
        },
        "crashes": crashes_list,
    }

    selection_file = output_dir / "selection.json"
    selection_file.write_text(json.dumps(selection_doc, indent=2) + "\n", encoding="utf-8")
    print(f"Saved master selection contract to {selection_file}")

    return selection_doc, crashes_list


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select five Binance ETH crashes and build artifacts for oracle reference check."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs/five-crash-oracles",
        help="Directory to write output artifacts",
    )
    parser.add_argument(
        "--min-separation-days",
        type=int,
        default=7,
        help="Minimum separation in days between crash trough clusters (default 7)",
    )
    parser.add_argument(
        "--crash-count",
        type=int,
        default=5,
        help="Number of crashes to select (default 5)",
    )
    args = parser.parse_args()

    _selection_doc, crashes = build_selection_and_minute_artifacts(
        output_dir=args.output_dir,
        min_separation_days=args.min_separation_days,
        crash_count=args.crash_count,
    )

    print("\nREADY")
    print("=" * 60)
    print("FIVE OBJECTIVELY SELECTED BINANCE ETH CRASHES:")
    for c in crashes:
        print(
            f"- {c['id']}: Date {c['dateUtc']}, Trough {c['crashUtc']} (Unix {c['crashTimestamp']}), "
            f"Open {c['hourOpen']:.2f}, Low {c['hourLow']:.2f}, Drop -{c['dropPct']:.2f}%, "
            f"Window [{c['windowStart']}..{c['windowEnd']}], File {c['binanceMinuteFile']}"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
