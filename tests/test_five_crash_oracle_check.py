"""Targeted tests for scripts/five_crash_oracle_check.py.

Verifies:
1. selection.json schema parsing and 145 target timestamps generation (600s step)
2. Binance ETH/USDC minute candle alignment:
   - Match most recent COMPLETED candle closeTime <= actual block timestamp
   - Record lag (block_timestamp - closeTime)
   - Never match future minute
   - Missing Binance ETHUSDC fails closed to null, never fallback to USDT
3. Divergence calculations vs Chainlink (max absolute, max negative gap pct/bps, feed ages, lag)
4. Shared cached header resolution with exact chosen.timestamp <= target < next.timestamp
5. Fail-closed status and no zero lines in charts
6. Atomic file writing and report generation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from swaparch.core.types import BlockRef

# Attempt import of five_crash_oracle_check
try:
    from scripts import five_crash_oracle_check as fco
except ImportError:
    # Expected during first RED phase of TDD before implementation
    fco = None


class MockRpcClient:
    """Mock RPC client returning blocks from a predefined dictionary."""

    def __init__(self, blocks: dict[int, BlockRef]) -> None:
        self.blocks = blocks
        self._chain_id = 1
        self.call_count = 0

    def chain_id(self) -> int:
        return self._chain_id

    def get_block(self, number: int) -> BlockRef:
        self.call_count += 1
        if number not in self.blocks:
            raise ValueError(f"Block {number} not found in mock timeline")
        return self.blocks[number]


def test_import_module():
    """Module must exist and be importable."""
    assert fco is not None, "scripts.five_crash_oracle_check must be importable"


def test_parse_selection_and_generate_145_targets(tmp_path: Path):
    """Test parsing selection.json schema and generating exactly 145 targets spanning 24h (86400s)."""
    selection_content = {
        "schemaVersion": 1,
        "selection": {
            "source": "ETHUSDT_hourly_crashes",
            "dateRange": ["2024-08-16", "2026-09-08"],
            "minSpacingDays": 7,
            "windowHours": 24,
        },
        "crashes": [
            {
                "id": "crash-1",
                "dateUtc": "2024-08-16T12:00:00Z",
                "crashTimestamp": 1723810000,
                "crashUtc": "2024-08-16T12:06:40Z",
                "hourOpen": 2650.0,
                "hourLow": 2320.0,
                "dropPct": 12.45,
                "windowStart": 1723766800,
                "windowEnd": 1723853200,
                "binanceMinuteFile": "binance/crash_1.json",
            },
            {
                "id": "crash-2",
                "dateUtc": "2024-10-01T15:00:00Z",
                "crashTimestamp": 1727794800,
                "crashUtc": "2024-10-01T15:00:00Z",
                "hourOpen": 2600.0,
                "hourLow": 2400.0,
                "dropPct": 7.69,
                "windowStart": 1727751600,
                "windowEnd": 1727838000,
                "binanceMinuteFile": "binance/crash_2.json",
            },
        ],
    }

    sel_file = tmp_path / "selection.json"
    sel_file.write_text(json.dumps(selection_content), encoding="utf-8")

    parsed = fco.parse_selection_file(sel_file)
    assert parsed["schemaVersion"] == 1
    assert len(parsed["crashes"]) == 2

    c0 = parsed["crashes"][0]
    assert c0["id"] == "crash-1"
    assert c0["dropPct"] == 12.45
    assert c0["windowEnd"] - c0["windowStart"] == 86400

    targets = fco.generate_window_targets(c0["windowStart"], c0["windowEnd"], step_seconds=600)
    assert len(targets) == 145
    assert targets[0] == c0["windowStart"]
    assert targets[-1] == c0["windowEnd"]
    assert targets[1] - targets[0] == 600
    assert targets[144] == targets[0] + 144 * 600


def test_generate_window_targets_rejects_bad_span():
    """Verify generate_window_targets rejects bad span or misaligned windowEnd."""
    with pytest.raises(ValueError, match="Invalid window span"):
        fco.generate_window_targets(1000, 2000)

    with pytest.raises(ValueError, match="Invalid window span"):
        fco.generate_window_targets(1723766800, 1723853201)


def test_binance_completed_candle_matching():
    """Verify Binance ETH/USDC matching: most recent COMPLETED candle closeTime <= block timestamp, lag, never future."""
    raw_data = {
        "symbol": "ETHUSDC",
        "priceUnit": "USDC per ETH",
        "candles": [
            {
                "openTime": 1000,
                "closeTime": 1059,
                "open": 2600.0,
                "high": 2610.0,
                "low": 2590.0,
                "close": 2605.0,
            },
            {
                "openTime": 1060,
                "closeTime": 1119,
                "open": 2605.0,
                "high": 2608.0,
                "low": 2570.0,
                "close": 2575.0,
            },
            {
                "openTime": 1120,
                "closeTime": 1179,
                "open": 2575.0,
                "high": 2580.0,
                "low": 2550.0,
                "close": 2555.0,
            },
            {
                "openTime": 1180,
                "closeTime": 1239,
                "open": 2555.0,
                "high": 2560.0,
                "low": 2540.0,
                "close": 2545.0,
            },
        ],
    }

    # Case 1: EXACT SECOND 59 BOUNDARY TEST (First minute: open 1000, close 1059, completion_time 1060):
    # At block timestamp 1059: the minute is still ongoing until 1059.999... -> completion_time 1060 > 1059.
    # Must NOT match candle 1 (no completed candle yet)!
    res_b59 = fco.match_binance_candle(raw_data, block_timestamp=1059)
    assert res_b59["status"] == "no_completed_candle"
    assert res_b59["price"] is None

    # Case 2: At block timestamp 1060: exactly as the minute completes -> completion_time 1060 <= 1060.
    # Must match candle 1!
    res_b60 = fco.match_binance_candle(raw_data, block_timestamp=1060)
    assert res_b60["status"] == "ok"
    assert res_b60["price"] == 2605.0
    assert res_b60["completion_time"] == 1060
    assert res_b60["close_time"] == 1059
    assert res_b60["lag_seconds"] == 0

    # Case 3: EXACT SECOND 59 BOUNDARY TEST (Second minute: open 1060, close 1119, completion_time 1120):
    # At block timestamp 1119: candle 2 is NOT completed yet (1120 > 1119).
    # Must match candle 1 (1060 <= 1119), lag = 1119 - 1060 = 59s.
    res_b119 = fco.match_binance_candle(raw_data, block_timestamp=1119)
    assert res_b119["status"] == "ok"
    assert res_b119["price"] == 2605.0  # Candle 1, NOT candle 2!
    assert res_b119["completion_time"] == 1060
    assert res_b119["close_time"] == 1059
    assert res_b119["lag_seconds"] == 59

    # Case 4: At block timestamp 1120: candle 2 IS completed (1120 <= 1120).
    # Must match candle 2!
    res_b120 = fco.match_binance_candle(raw_data, block_timestamp=1120)
    assert res_b120["status"] == "ok"
    assert res_b120["price"] == 2575.0  # Candle 2
    assert res_b120["completion_time"] == 1120
    assert res_b120["close_time"] == 1119
    assert res_b120["lag_seconds"] == 0

    # Case 5: At block timestamp 1125: candle 2 lag = 1125 - 1120 = 5s.
    res3 = fco.match_binance_candle(raw_data, block_timestamp=1125)
    assert res3["status"] == "ok"
    assert res3["price"] == 2575.0
    assert res3["completion_time"] == 1120
    assert res3["close_time"] == 1119
    assert res3["lag_seconds"] == 5

    # Case 6: block timestamp is 1020 (before first candle finishes)
    # No completed candle exists yet!
    res4 = fco.match_binance_candle(raw_data, block_timestamp=1020)
    assert res4["status"] == "no_completed_candle"
    assert res4["price"] is None
    assert res4["lag_seconds"] is None


def test_binance_missing_or_mismatched_fails_closed():
    """Missing Binance data or non-ETHUSDC symbol must fail closed to null; NEVER fallback to USDT."""
    # Symbol mismatch: ETHUSDT
    usdt_data = {
        "symbol": "ETHUSDT",
        "priceUnit": "USDT per ETH",
        "candles": [
            {
                "openTime": 1000,
                "closeTime": 1059,
                "open": 2600.0,
                "high": 2610.0,
                "low": 2590.0,
                "close": 2605.0,
            }
        ],
    }
    res_usdt = fco.match_binance_candle(usdt_data, block_timestamp=1100)
    assert res_usdt["status"] == "symbol_mismatch"
    assert res_usdt["price"] is None
    assert res_usdt["lag_seconds"] is None

    # None data (missing file)
    res_none = fco.match_binance_candle(None, block_timestamp=1100)
    assert res_none["status"] == "missing_file"
    assert res_none["price"] is None
    assert res_none["lag_seconds"] is None

    # Empty candles
    empty_data = {"symbol": "ETHUSDC", "priceUnit": "USDC per ETH", "candles": []}
    res_empty = fco.match_binance_candle(empty_data, block_timestamp=1100)
    assert res_empty["status"] == "no_candles"
    assert res_empty["price"] is None


def test_calculate_crash_divergences_with_binance():
    """Verify divergence calculations for 1inch, UniV3 300, UniV3 60, and Binance vs Chainlink."""
    rows = [
        {
            "target_index": 0,
            "target_utc": "2024-08-16T00:00:00Z",
            "block_number": 20540000,
            "diff_seconds": 2,
            "chainlink": {
                "price": 2500.0,
                "eth_age_seconds": 60,
                "usdc_age_seconds": 3600,
                "status": "ok",
            },
            "oneinch": {"price": 2510.0, "status": "ok"},  # +0.40% (+40.0 bps)
            "univ3_twap_300": {"price": 2450.0, "status": "ok"},  # -2.00% (-200.0 bps, >100bps)
            "univ3_twap_60": {"price": 2400.0, "status": "ok"},  # -4.00% (-400.0 bps, >100bps)
            "binance": {"price": 2525.0, "lag_seconds": 15, "status": "ok"},  # +1.00% (+100.0 bps)
        },
        {
            "target_index": 1,
            "target_utc": "2024-08-16T00:10:00Z",
            "block_number": 20540050,
            "diff_seconds": 4,
            "chainlink": {
                "price": 2400.0,
                "eth_age_seconds": 120,
                "usdc_age_seconds": 4200,
                "status": "ok",
            },
            "oneinch": {"price": 2350.0, "status": "ok"},  # -2.083% (-208.33 bps)
            "univ3_twap_300": {"price": 2380.0, "status": "ok"},  # -0.833% (-83.33 bps)
            "univ3_twap_60": {"price": 2390.0, "status": "ok"},  # -0.417% (-41.67 bps)
            "binance": {
                "price": 2280.0,
                "lag_seconds": 20,
                "status": "ok",
            },  # -5.00% (-500.0 bps, worst negative)
        },
    ]

    summaries = fco.calculate_crash_gaps(rows)

    # Verify Binance summary
    assert "binance" in summaries
    bin_sum = summaries["binance"]
    assert bin_sum["valid_count"] == 2
    assert bin_sum["max_gap_pct"] == 1.0
    assert bin_sum["min_gap_pct"] == -5.0
    assert bin_sum["max_negative_gap_pct"] == -5.0
    assert bin_sum["max_abs_gap_pct"] == 5.0
    assert bin_sum["count_gt_100bps"] == 1  # 500 bps is > 100 bps
    assert bin_sum["worst_negative_divergence"]["gap_pct"] == -5.0
    assert bin_sum["worst_negative_divergence"]["target_utc"] == "2024-08-16T00:10:00Z"
    assert bin_sum["worst_negative_divergence"]["binance_lag_seconds"] == 20

    # Verify 1inch summary
    oi_sum = summaries["oneinch"]
    assert oi_sum["valid_count"] == 2
    assert oi_sum["min_gap_bps"] == -208.33
    assert oi_sum["max_gap_bps"] == 40.0

    # Verify UniV3 300 summary
    u3_sum = summaries["univ3_twap_300"]
    assert u3_sum["valid_count"] == 2
    assert u3_sum["count_gt_100bps"] == 1
    assert u3_sum["worst_negative_divergence"]["gap_pct"] == -2.0


def test_shared_header_resolution_satisfies_all_bounds():
    """Verify shared cached header resolution satisfies chosen.timestamp <= target < next.timestamp for all pins."""
    # Synthetic blocks every 12 seconds
    start_ts = 1000000
    blocks: dict[int, BlockRef] = {}
    for i in range(-50, 200):
        b_num = 1000 + i
        b_ts = start_ts + i * 12
        blocks[b_num] = BlockRef(chain=1, number=b_num, hash=f"0x{b_num:04x}", timestamp=b_ts)

    mock_client = MockRpcClient(blocks)
    shared_cache: dict[int, BlockRef] = {}

    targets = [start_ts + i * 60 for i in range(10)]  # 10 targets, 60s step

    resolved = fco.resolve_target_blocks_shared(
        client=mock_client,
        targets=targets,
        cache=shared_cache,
        initial_block_hint=1000,
    )

    assert len(resolved) == 10
    for r in resolved:
        assert r["verified_bounds"] is True
        assert r["block_timestamp"] <= r["target_timestamp"] < r["next_block_timestamp"]
        assert r["diff_seconds"] == r["target_timestamp"] - r["block_timestamp"]

    # Verify shared cache was populated and reused
    assert len(shared_cache) > 0
    initial_call_count = mock_client.call_count

    # Second pass over same targets should make zero new get_block calls
    resolved_again = fco.resolve_target_blocks_shared(
        client=mock_client,
        targets=targets,
        cache=shared_cache,
        initial_block_hint=1000,
    )
    assert mock_client.call_count == initial_call_count
    assert len(resolved_again) == 10


def test_html_report_rendering_no_zero_lines():
    """Verify HTML chart generation contains no zero coordinate lines for null prices and includes disclaimers."""
    manifest = {
        "crash_id": "test-crash",
        "run_timestamp_utc": "2026-09-09T22:00:00Z",
        "window_start_utc": "2024-08-16T00:00:00Z",
        "window_end_utc": "2024-08-17T00:00:00Z",
        "target_count": 2,
    }
    rows = [
        {
            "target_index": 0,
            "target_utc": "2024-08-16T00:00:00Z",
            "block_number": 20000000,
            "diff_seconds": 4,
            "chainlink": {
                "price": 2500.0,
                "eth_age_seconds": 30,
                "usdc_age_seconds": 1200,
                "status": "ok",
            },
            "oneinch": {"price": 2505.0, "status": "ok"},
            "univ3_twap_300": {"price": None, "status": "reverted"},  # Missing price!
            "univ3_twap_60": {"price": 2502.0, "status": "ok"},
            "binance": {"price": 2504.0, "lag_seconds": 10, "status": "ok"},
        },
        {
            "target_index": 1,
            "target_utc": "2024-08-16T00:10:00Z",
            "block_number": 20000050,
            "diff_seconds": 2,
            "chainlink": {
                "price": 2480.0,
                "eth_age_seconds": 40,
                "usdc_age_seconds": 1260,
                "status": "ok",
            },
            "oneinch": {"price": 2482.0, "status": "ok"},
            "univ3_twap_300": {"price": 2479.0, "status": "ok"},
            "univ3_twap_60": {"price": 2480.0, "status": "ok"},
            "binance": {
                "price": None,
                "lag_seconds": None,
                "status": "missing_candle",
            },  # Missing Binance!
        },
    ]
    summaries = fco.calculate_crash_gaps(rows)

    html = fco.build_crash_html_report(manifest, rows, summaries)

    # Assert no 0.0 price points in SVG path points (no zero line diving)
    path_lines = [line for line in html.splitlines() if "<path" in line and "d=" in line]
    assert len(path_lines) > 0
    for pl in path_lines:
        assert ",0.0" not in pl
        assert ",0 " not in pl

    # Check that caveats are present
    assert "Sample counts (145 points per 24h window) are discrete snapshots" in html
    assert "size-independent" in html
    # Check that Binance is present in legend and table
    assert "Binance ETH/USDC" in html
    assert "ETHUSDC" in html


def test_svg_path_emits_separate_m_for_gaps():
    """Verify SVG emits separate M path commands over missing data gaps to prevent misleading continuity."""
    manifest = {
        "crash_id": "test-gaps",
        "run_timestamp_utc": "2026-09-09T22:00:00Z",
        "window_start_utc": "2024-08-16T00:00:00Z",
        "window_end_utc": "2024-08-17T00:00:00Z",
        "target_count": 4,
    }
    # Series with a gap: point 0 valid, point 1 missing (None), point 2 valid, point 3 valid
    rows = [
        {
            "target_index": 0,
            "target_utc": "2024-08-16T00:00:00Z",
            "block_number": 1,
            "diff_seconds": 0,
            "chainlink": {"price": 2500.0, "status": "ok"},
            "oneinch": {"price": 2500.0},
            "univ3_twap_300": {"price": None},
            "univ3_twap_60": {"price": None},
            "binance": {"price": 2500.0, "status": "ok"},
        },
        {
            "target_index": 1,
            "target_utc": "2024-08-16T00:10:00Z",
            "block_number": 2,
            "diff_seconds": 0,
            "chainlink": {"price": 2490.0, "status": "ok"},
            "oneinch": {"price": 2490.0},
            "univ3_twap_300": {"price": None},
            "univ3_twap_60": {"price": None},
            "binance": {"price": None, "status": "missing_candle"},  # Gap!
        },
        {
            "target_index": 2,
            "target_utc": "2024-08-16T00:20:00Z",
            "block_number": 3,
            "diff_seconds": 0,
            "chainlink": {"price": 2480.0, "status": "ok"},
            "oneinch": {"price": 2480.0},
            "univ3_twap_300": {"price": None},
            "univ3_twap_60": {"price": None},
            "binance": {"price": 2485.0, "status": "ok"},
        },
        {
            "target_index": 3,
            "target_utc": "2024-08-16T00:30:00Z",
            "block_number": 4,
            "diff_seconds": 0,
            "chainlink": {"price": 2470.0, "status": "ok"},
            "oneinch": {"price": 2470.0},
            "univ3_twap_300": {"price": None},
            "univ3_twap_60": {"price": None},
            "binance": {"price": 2475.0, "status": "ok"},
        },
    ]
    summaries = fco.calculate_crash_gaps(rows)
    html = fco.build_crash_html_report(manifest, rows, summaries)

    # Find the binance path line (color #06b6d4)
    binance_path_line = next(
        line for line in html.splitlines() if "<path" in line and "#06b6d4" in line
    )
    # Extract d attribute
    d_attr = binance_path_line.split('d="')[1].split('"')[0]
    m_count = d_attr.count("M")
    # Must emit 2 separate M segments (point 0, then after gap point 2..3)
    assert m_count == 2, f"Expected 2 separate M segments across gap, got {m_count}: {d_attr}"


def test_master_index_derives_counts_single_crash():
    """Verify master index HTML derives counts dynamically when run with single crash."""
    manifest = {
        "run_timestamp_utc": "2026-09-09T23:00:00Z",
        "crash_count": 1,
        "total_pins": 145,
    }
    crashes_meta = [
        {"id": "crash-single", "dateUtc": "2024-08-16", "crashUtc": "12:00", "dropPct": 10.0}
    ]
    html = fco.build_master_index_html(manifest, crashes_meta)
    assert "Historical 1 Binance ETH Crash:" in html
    assert "Total Target Pins:</strong> 145 blocks" in html
    assert "Download Full JSON (145 Pins)" in html


def test_run_crash_window_end_to_end_synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify run_crash_window end-to-end with synthetic blocks, mocked queries, and Binance data."""
    start_ts = 1723766800
    w_end = start_ts + 144 * 600

    # Create 145 synthetic targets with blocks
    blocks: dict[int, BlockRef] = {}
    for i in range(-50, 7500):
        b_num = 20540000 + i
        b_ts = start_ts + i * 12
        blocks[b_num] = BlockRef(chain=1, number=b_num, hash=f"0x{b_num:06x}", timestamp=b_ts)

    class FullMockClient:
        def __init__(self):
            self.blocks = blocks
            self.call_count = 0

        def chain_id(self):
            return 1

        def get_block(self, number: int):
            self.call_count += 1
            if number in self.blocks:
                return self.blocks[number]
            # Return synthetic block for calibration probes
            ts = start_ts + (number - 20540000) * 12
            return BlockRef(chain=1, number=number, hash=f"0x{number:06x}", timestamp=ts)

        def _rpc(self, method: str, params: list):
            if method == "eth_getCode":
                return "0x608060405234801561001057600080fd5b50"
            raise NotImplementedError(method)

    # Mock query_block_oracle_references
    def mock_query(multicall, block_ref):
        decoded = {
            "chainlink": {
                "price": 2500.0,
                "eth_usd": 2500.0,
                "usdc_usd": 1.0,
                "eth_age_seconds": 45,
                "usdc_age_seconds": 3600,
                "status": "ok",
                "reason": None,
            },
            "oneinch": {"price": 2502.0, "status": "ok", "reason": None},
            "univ3_twap_300": {"price": 2498.0, "status": "ok", "reason": None},
            "univ3_twap_60": {"price": 2499.0, "status": "ok", "reason": None},
        }
        raw_evidence = {"block": block_ref.number, "calls": {}}
        return decoded, raw_evidence

    monkeypatch.setattr(fco, "query_block_oracle_references", mock_query)

    # Create Binance minute file
    bin_file = tmp_path / "binance_crash.json"
    candles = []
    for step in range(145):
        c_ts = start_ts + step * 600
        candles.append(
            {
                "openTime": c_ts - 60,
                "closeTime": c_ts - 1,
                "open": 2501.0,
                "high": 2505.0,
                "low": 2495.0,
                "close": 2500.5,
            }
        )
    bin_data = {
        "symbol": "ETHUSDC",
        "priceUnit": "USDC per ETH",
        "candles": candles,
    }
    bin_file.write_text(json.dumps(bin_data), encoding="utf-8")

    crash_meta = {
        "id": "crash-synthetic",
        "dateUtc": "2024-08-16T12:00:00Z",
        "crashTimestamp": start_ts + 72 * 600,
        "crashUtc": "2024-08-16T12:00:00Z",
        "hourOpen": 2600.0,
        "hourLow": 2300.0,
        "dropPct": 11.54,
        "windowStart": start_ts,
        "windowEnd": w_end,
        "binanceMinuteFile": str(bin_file),
    }

    client = FullMockClient()
    shared_cache: dict[int, BlockRef] = {}

    manifest, rows, raw_evidences = fco.run_crash_window(
        client=client,
        crash_meta=crash_meta,
        shared_cache=shared_cache,
        workers=2,
        binance_base_dir=tmp_path,
    )

    assert manifest["crash_id"] == "crash-synthetic"
    assert manifest["target_count"] == 145
    assert len(rows) == 145
    assert len(raw_evidences) == 145

    # Check first row
    r0 = rows[0]
    assert r0["verified_bounds"] is True
    assert "next_block_number" in r0
    assert "next_block_timestamp" in r0
    assert r0["block_timestamp"] <= r0["target_timestamp"] < r0["next_block_timestamp"]
    assert r0["chainlink"]["price"] == 2500.0
    assert r0["binance"]["price"] == 2500.5
    assert r0["binance"]["status"] == "ok"
    assert "completion_time" in r0["binance"]
    assert "close_time" in r0["binance"]
    assert r0["binance"]["lag_seconds"] >= 0

    # Check summaries
    sums = manifest["summaries"]
    assert "oneinch" in sums
    assert "univ3_twap_300" in sums
    assert "binance" in sums
    assert sums["binance"]["valid_count"] == 145
