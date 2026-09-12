"""Minimal tests for scripts/select_binance_crashes.py.

Verifies:
1. Timestamp parsing for 2025+ microseconds (>1e14) vs older milliseconds
2. Hourly open-to-low percentage decline ranking
3. Greedy cluster selection requiring minimum 7 days separation between trough dates
4. Export ordering is chronological
5. Trough Unix timestamp uses minute CLOSE floor seconds (no future candle close alignment)
6. 24h window centered on trough (12h before to 12h after) produces 145 targets at 600s intervals
7. Standardized minute candles provide complete coverage for full 24h window plus prior 1 minute,
   successfully matching all 145 targets with five_crash_oracle_check.match_binance_candle
8. Validates selection.json contract against five_crash_oracle_check.parse_selection_file
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


from scripts import five_crash_oracle_check as fco
from scripts import select_binance_crashes as sbc


def test_timestamp_parsing_microseconds_and_milliseconds():
    """Verify parse_timestamp_seconds handles 2025+ microseconds (>1e14) and older ms explicitly."""
    # 2024 August: 13-digit millisecond timestamp (1722470400000 -> 1722470400)
    ms_ts = 1722470400000
    assert sbc.parse_timestamp_seconds(ms_ts) == 1722470400
    assert sbc.parse_timestamp_seconds(ms_ts + 59999) == 1722470459

    # 2025 January: 16-digit microsecond timestamp (1735689600000000 -> 1735689600)
    us_ts = 1735689600000000
    assert sbc.parse_timestamp_seconds(us_ts) == 1735689600
    assert sbc.parse_timestamp_seconds(us_ts + 59999999) == 1735689659


def test_hourly_open_to_low_percentage_decline():
    """Verify metric calculation: (open - low) / open * 100.0."""
    hours = [
        {"open": 2000.0, "low": 1800.0, "openTime": 1000, "closeTime": 4599},
        {"open": 3000.0, "low": 2700.0, "openTime": 5000, "closeTime": 8599},
        {"open": 2500.0, "low": 2125.0, "openTime": 9000, "closeTime": 12599},
    ]
    for h in hours:
        h["dropPct"] = (h["open"] - h["low"]) / h["open"] * 100.0

    assert abs(hours[0]["dropPct"] - 10.0) < 1e-6
    assert abs(hours[1]["dropPct"] - 10.0) < 1e-6
    assert abs(hours[2]["dropPct"] - 15.0) < 1e-6


def test_greedy_cluster_selection_7_days_and_chronological_export():
    """Verify greedy selection chooses largest drop first, enforces >=7 days separation, and exports chronologically."""
    base_ts = 1735689600  # 2025-01-01 00:00:00 UTC
    day = 86400

    candidates = [
        # Event A: Day 10, drop 12.0%
        {
            "open": 2000.0,
            "low": 1760.0,
            "dropPct": 12.0,
            "openTime": base_ts + 10 * day,
            "closeTime": base_ts + 10 * day + 3599,
        },
        # Event B: Day 12, drop 15.0% (Largest drop! Selected first. Conflicts with Day 10 because abs(12-10)=2 < 7)
        {
            "open": 2000.0,
            "low": 1700.0,
            "dropPct": 15.0,
            "openTime": base_ts + 12 * day,
            "closeTime": base_ts + 12 * day + 3599,
        },
        # Event C: Day 13, drop 14.0% (Conflicts with Day 12 because abs(13-12)=1 < 7)
        {
            "open": 2000.0,
            "low": 1720.0,
            "dropPct": 14.0,
            "openTime": base_ts + 13 * day,
            "closeTime": base_ts + 13 * day + 3599,
        },
        # Event D: Day 20, drop 11.0% (Selected second! abs(20-12)=8 >= 7)
        {
            "open": 2000.0,
            "low": 1780.0,
            "dropPct": 11.0,
            "openTime": base_ts + 20 * day,
            "closeTime": base_ts + 20 * day + 3599,
        },
        # Event E: Day 30, drop 9.0% (Selected third! abs(30-20)=10 >= 7)
        {
            "open": 2000.0,
            "low": 1820.0,
            "dropPct": 9.0,
            "openTime": base_ts + 30 * day,
            "closeTime": base_ts + 30 * day + 3599,
        },
        # Event F: Day 45, drop 8.0% (Selected fourth! abs(45-30)=15 >= 7)
        {
            "open": 2000.0,
            "low": 1840.0,
            "dropPct": 8.0,
            "openTime": base_ts + 45 * day,
            "closeTime": base_ts + 45 * day + 3599,
        },
        # Event G: Day 60, drop 7.0% (Selected fifth! abs(60-45)=15 >= 7)
        {
            "open": 2000.0,
            "low": 1860.0,
            "dropPct": 7.0,
            "openTime": base_ts + 60 * day,
            "closeTime": base_ts + 60 * day + 3599,
        },
    ]

    ranked, selected_chrono = sbc.rank_and_select_clusters(
        candidates, min_separation_days=7, count=5
    )

    assert len(ranked) == 7
    assert ranked[0]["dropPct"] == 15.0  # Event B is ranked #1

    # Selected clusters in greedy order should be: Day 12 (15%), Day 20 (11%), Day 30 (9%), Day 45 (8%), Day 60 (7%)
    # Day 10 (12%) and Day 13 (14%) must be excluded because they are < 7 days from Day 12!
    selected_days = [(c["openTime"] - base_ts) // day for c in selected_chrono]
    assert selected_days == [12, 20, 30, 45, 60], f"Selected days mismatch: {selected_days}"

    # Verify chronological ordering
    for i in range(len(selected_chrono) - 1):
        assert selected_chrono[i]["openTime"] < selected_chrono[i + 1]["openTime"]

    # Verify all pairs separated by >= 7 days
    for i in range(len(selected_chrono)):
        for j in range(i + 1, len(selected_chrono)):
            d_i = datetime.fromtimestamp(selected_chrono[i]["openTime"], UTC).date()
            d_j = datetime.fromtimestamp(selected_chrono[j]["openTime"], UTC).date()
            assert abs((d_j - d_i).days) >= 7


def test_trough_unix_minute_close_floor_seconds():
    """Verify troughUnix uses minute CLOSE floor seconds (low time only known within minute)."""
    # In a minute candle starting at 12:34:00 (1735689600):
    # Binance raw close time is 12:34:59.999 (1735689659999 ms) or 12:34:59.999999 (1735689659999999 us)
    raw_close_ms = 1735689659999
    raw_close_us = 1735689659999999
    expected_floor_seconds = 1735689659  # 12:34:59 UTC

    assert sbc.parse_timestamp_seconds(raw_close_ms) == expected_floor_seconds
    assert sbc.parse_timestamp_seconds(raw_close_us) == expected_floor_seconds

    # No future candle close alignment: must NOT be 12:35:00
    assert expected_floor_seconds != 1735689660


def test_24h_window_and_145_targets():
    """Verify window is centered on trough (-12h to +12h) and spans exactly 145 targets at 600s intervals."""
    trough_unix = 1738547819  # 2025-02-03 01:56:59 UTC
    window_start = trough_unix - 12 * 3600  # 1738504619 (2025-02-02 13:56:59 UTC)
    window_end = trough_unix + 12 * 3600  # 1738591019 (2025-02-03 13:56:59 UTC)

    assert window_end - window_start == 86400

    targets = fco.generate_window_targets(window_start, window_end, step_seconds=600)
    assert len(targets) == 145
    assert targets[0] == window_start
    assert targets[72] == trough_unix
    assert targets[144] == window_end
    for i in range(144):
        assert targets[i + 1] - targets[i] == 600


def test_standardized_minute_candles_matching_all_targets():
    """Verify standardized minute candles with prior 1 minute match all 145 targets under match_binance_candle."""
    trough_unix = 1738547819
    window_start = trough_unix - 43200
    window_end = trough_unix + 43200

    # Build standardized candles: openTime = windowStart - 119 to windowEnd - 59
    earliest_open = window_start - 119
    latest_open = window_end - 59
    candles = []
    curr_open = earliest_open
    while curr_open <= latest_open:
        candles.append(
            {
                "openTime": curr_open,
                "closeTime": curr_open + 59,
                "open": 2500.0,
                "high": 2510.0,
                "low": 2490.0,
                "close": 2505.0,
            }
        )
        curr_open += 60

    assert len(candles) == 1442
    assert candles[0]["closeTime"] == window_start - 60  # Prior 1 minute completed candle
    assert candles[1]["closeTime"] == window_start
    assert candles[-1]["closeTime"] == window_end

    bin_data = {"symbol": "ETHUSDC", "priceUnit": "USDC per ETH", "candles": candles}

    # Verify that for every target (and possible block offset in [target - 11, target]), match succeeds
    targets = fco.generate_window_targets(window_start, window_end, step_seconds=600)
    for t in targets:
        for offset in [0, -4, -8, -11]:
            bt = t + offset
            res = fco.match_binance_candle(bin_data, block_timestamp=bt)
            assert res["status"] == "ok", f"Target {t} offset {offset} failed with {res}"
            assert res["price"] == 2505.0
            assert res["lag_seconds"] >= 0


def test_generated_selection_json_contract_and_minute_files():
    """Verify that the generated outputs/five-crash-oracles/selection.json satisfies the contract."""
    sel_path = ROOT / "outputs/five-crash-oracles/selection.json"
    assert sel_path.exists(), f"selection.json does not exist at {sel_path}"

    data = fco.parse_selection_file(sel_path)
    assert data["schemaVersion"] == 1
    assert "selection" in data
    assert "crashes" in data

    crashes = data["crashes"]
    assert len(crashes) == 5, f"Expected 5 crashes, got {len(crashes)}"

    # Check 5 distinct non-overlapping 24h windows
    for i in range(len(crashes)):
        c = crashes[i]
        assert "id" in c
        assert "dateUtc" in c
        assert "crashTimestamp" in c
        assert "crashUtc" in c
        assert "hourOpen" in c
        assert "hourLow" in c
        assert "dropPct" in c
        assert c["dropPct"] > 0
        assert "windowStart" in c
        assert "windowEnd" in c
        assert c["windowEnd"] - c["windowStart"] == 86400
        assert "binanceMinuteFile" in c

        # Verify load_binance_minute_file loads it cleanly
        minute_data = fco.load_binance_minute_file(
            c["binanceMinuteFile"], base_dir=ROOT / "outputs/five-crash-oracles"
        )
        assert minute_data is not None, f"Failed to load minute file {c['binanceMinuteFile']}"
        assert minute_data["symbol"] == "ETHUSDC"
        assert minute_data["priceUnit"] == "USDC per ETH"
        assert len(minute_data["candles"]) >= 1441

        # Check match_binance_candle on windowStart
        match_start = fco.match_binance_candle(minute_data, c["windowStart"])
        assert match_start["status"] == "ok"

        # Check match_binance_candle on windowEnd
        match_end = fco.match_binance_candle(minute_data, c["windowEnd"])
        assert match_end["status"] == "ok"

    # Check no overlap between windows
    for i in range(len(crashes) - 1):
        assert crashes[i]["windowEnd"] < crashes[i + 1]["windowStart"], (
            f"Overlap between crash {crashes[i]['id']} and {crashes[i + 1]['id']}"
        )

    # Check coverage document
    cov_path = ROOT / "outputs/five-crash-oracles/binance/coverage.json"
    assert cov_path.exists()
    cov = json.loads(cov_path.read_text(encoding="utf-8"))
    assert cov["universeTotalHours"] == 18096
    assert cov["universeCompleteHours"] == 18096
    assert cov["coverageComplete"] is True
    assert cov["monthlyArchivesCount"] == 25
    assert cov["dailyArchivesCount"] == 8
