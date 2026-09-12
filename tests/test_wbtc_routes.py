"""Tests for two-leg WBTC routing, full input consumption, and quoter parity."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest
from wbtc_collect import DEFAULT_PINS, load_block_reference
from wbtc_inventory import DEFAULT_OUT
from wbtc_route_eval import evaluate_block_routes

from swaparch.snapshot.store import SnapshotStore


def test_quoter_differential_report_exact():
    """Verify QuoterV2 differential validation report exists and has 100% exact matches."""
    report_file = DEFAULT_OUT / "quoter_checks.json"
    if not report_file.is_file():
        pytest.skip("Quoter differential report not yet generated")

    data = json.loads(report_file.read_text())
    assert data["overall_exact_match"] is True
    assert len(data["blocks_tested"]) == 3

    for block_rep in data["block_reports"]:
        assert block_rep["all_exact_matches"] is True
        for check in block_rep["checks"]:
            assert check["all_legs_exact_match"] is True
            assert check["leg1_weth_wbtc"]["exact_match"] is True
            assert check["leg2_wbtc_usdc"]["exact_match"] is True
            assert check["leg2_wbtc_usdt"]["exact_match"] is True
            assert check["leg1_weth_wbtc"]["out_diff_wei"] == 0
            assert check["leg2_wbtc_usdc"]["out_diff_wei"] == 0
            assert check["leg2_wbtc_usdt"]["out_diff_wei"] == 0


@pytest.mark.parametrize("block_num", DEFAULT_PINS)
def test_two_leg_full_input_consumption(block_num):
    """Verify that two-leg WETH -> WBTC -> USDC routes fully consume 1, 10, 100 WETH input."""
    snapshot_dir = DEFAULT_OUT / "snapshots"
    block = load_block_reference(block_num)

    try:
        store = SnapshotStore(snapshot_dir)
        store.load(block.chain, block.hash)
    except (FileNotFoundError, KeyError):
        pytest.skip(f"Snapshot for block {block_num} not yet collected")

    res = evaluate_block_routes(block_num, output_dir=DEFAULT_OUT)
    assert res["block"] == block_num

    for route in res["routes"]:
        assert route["all_sizes_consumed_fully"] is True
        for sr in route["sizes"]:
            assert sr["full_input_consumed"] is True
            assert sr["residual_in"] == 0
            assert sr["amount_in_spent"] == sr["size_weth"] * 10**18
            assert sr["intermediate_wbtc_out_raw"] > 0
            assert sr["final_out_raw"] > 0
            assert sr["effective_price_per_weth"] > 0

        # Verify price deterioration (price per WETH drops with larger size)
        p1 = route["sizes"][0]["effective_price_per_weth"]
        p10 = route["sizes"][1]["effective_price_per_weth"]
        p100 = route["sizes"][2]["effective_price_per_weth"]
        assert p1 >= p10 >= p100, (
            f"Monotonic slippage violated in {route['route']}: {p1}, {p10}, {p100}"
        )


def test_offline_two_leg_eval_manifest():
    """Verify the combined two-leg evaluation artifact."""
    eval_file = DEFAULT_OUT / "two_leg_eval.json"
    if not eval_file.is_file():
        pytest.skip("two_leg_eval.json not yet generated")

    data = json.loads(eval_file.read_text())
    assert data["overall_full_input_consumed"] is True
    assert len(data["block_results"]) == 3
