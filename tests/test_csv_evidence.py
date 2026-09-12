"""Regression checks for the CSV model-overlap report."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import uniswap_v3_csv_check as replay


def test_cached_pair_reports_exact_input_and_observed_terminal_overlap():
    rows = replay.read_rows(
        ROOT / "evidence" / "crash-rescue-simulation" / "saved-data"
        / "swaps_crash1_weth_usdc_500.csv"
    )
    prev, cur = rows[0], rows[1]

    assert replay.matching_models(prev, cur) == ("exact_in", "observed_terminal")
    assert replay.classify(prev, cur) == "exact_in"  # legacy first-match API
