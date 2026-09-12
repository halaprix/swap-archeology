"""Offline arithmetic consistency checks for saved Uniswap V3 Swap events.

Driver for the check that also lives as a test in ``tests/test_uniswap_v3_math.py``.
Reads the copied evidence CSVs (never modified) and writes a JSON summary under
``data/adapters-evidence/uniswap_v3/``.

Method
------
Take consecutive ``Swap`` events in the same block for which the pool liquidity
recorded on both events is identical.  This is a pre-state assumption for the
later event: the earlier event's post-swap ``sqrtPriceX96`` is its pre-swap
price.  Equal endpoint liquidity does not exclude intervening liquidity events.
Feed the later event's positive (pool-input) amount through the ported
``SwapMath.computeSwapStep`` and compare.

A ``Swap`` event does not record which call shape produced it.  The three
candidate models are therefore tested independently and every matching model
is reported:

``exact_in``   ``computeSwapStep(P0, farLimit, L, +amountIn, fee)`` reproduces
               both ``amountOut`` and the post-swap price.
``exact_out``  ``computeSwapStep(P0, farLimit, L, -amountOut, fee)`` reproduces
               both ``amountIn`` (``amountIn + feeAmount``) and the post-swap
               price.  This is not proof that the caller used ``exactOutput``.
``observed_terminal``
               ``computeSwapStep(P0, P1observed, L, +amountIn, fee)`` reproduces
               amounts and price.  This is arithmetic consistency with the
               observed terminal price; it does not identify a caller price
               limit or caller swap mode.

``classify`` retains the old first-match string API for existing checks.  Its
``limit`` value is a compatibility label for ``observed_terminal`` and must not
be read as a recovered caller mode.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
from itertools import pairwise

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from swaparch.adapters.uniswap_v3 import math as m

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "evidence" / "crash-rescue-simulation" / "saved-data"
OUT = ROOT / "data" / "adapters-evidence" / "uniswap_v3" / "swap-event-replay.json"

FEE_PIPS = 500  # the 0.05% WETH/USDC pool 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640


def read_rows(path: pathlib.Path) -> list[dict[str, int]]:
    with path.open() as fh:
        return [{k: int(v) for k, v in r.items()} for r in csv.DictReader(fh)]


def candidate_pairs(rows: list[dict[str, int]]):
    for prev, cur in pairwise(rows):
        if prev["block"] != cur["block"]:
            continue
        if prev["liquidity"] != cur["liquidity"]:
            continue
        yield prev, cur


def matching_models(
    prev: dict[str, int], cur: dict[str, int], fee_pips: int = FEE_PIPS
) -> tuple[str, ...]:
    """Return every candidate model that reproduces this pair exactly."""
    zero_for_one = cur["amount0"] > 0
    amount_in = cur["amount0"] if zero_for_one else cur["amount1"]
    amount_out = -(cur["amount1"] if zero_for_one else cur["amount0"])
    p0, liq, p1 = prev["sqrtPriceX96"], prev["liquidity"], cur["sqrtPriceX96"]
    far = m.MIN_SQRT_RATIO + 1 if zero_for_one else m.MAX_SQRT_RATIO - 1
    matches: list[str] = []

    nxt, s_in, s_out, fee = m.compute_swap_step(p0, far, liq, amount_in, fee_pips)
    if nxt == p1 and s_out == amount_out and s_in + fee == amount_in:
        matches.append("exact_in")

    nxt, s_in, s_out, fee = m.compute_swap_step(p0, far, liq, -amount_out, fee_pips)
    if nxt == p1 and s_out == amount_out and s_in + fee == amount_in:
        matches.append("exact_out")

    nxt, s_in, s_out, fee = m.compute_swap_step(p0, p1, liq, amount_in, fee_pips)
    if nxt == p1 and s_out == amount_out and s_in + fee == amount_in:
        matches.append("observed_terminal")

    return tuple(matches)


def classify(prev: dict[str, int], cur: dict[str, int], fee_pips: int = FEE_PIPS) -> str:
    """Return the legacy first-match label used by existing replay checks."""
    for model in matching_models(prev, cur, fee_pips):
        return "limit" if model == "observed_terminal" else model

    return "other"


def main() -> int:
    per_file: dict[str, dict[str, int]] = {}
    total = {"pairs": 0, "exact_in": 0, "exact_out": 0, "limit": 0, "other": 0}
    model_names = ("exact_in", "exact_out", "observed_terminal")
    model_matches = dict.fromkeys(model_names, 0)
    overlap_counts: dict[str, int] = {}
    unexplained: list[dict[str, int]] = []

    for path in sorted(CSV_DIR.glob("swaps_crash*_weth_usdc_500.csv")):
        rows = read_rows(path)
        counts = {"rows": len(rows), "pairs": 0, "exact_in": 0, "exact_out": 0, "limit": 0,
                  "other": 0}
        file_model_matches = dict.fromkeys(model_names, 0)
        file_overlap_counts: dict[str, int] = {}
        for prev, cur in candidate_pairs(rows):
            models = matching_models(prev, cur)
            kind = "other" if not models else (
                "limit" if models[0] == "observed_terminal" else models[0]
            )
            overlap = "+".join(models) if models else "other"
            counts["pairs"] += 1
            counts[kind] += 1
            total["pairs"] += 1
            total[kind] += 1
            for model in models:
                file_model_matches[model] += 1
                model_matches[model] += 1
            file_overlap_counts[overlap] = file_overlap_counts.get(overlap, 0) + 1
            overlap_counts[overlap] = overlap_counts.get(overlap, 0) + 1
            if not models and len(unexplained) < 40:
                unexplained.append({**cur, "prev_sqrtPriceX96": prev["sqrtPriceX96"]})
        counts["matching_model_counts"] = file_model_matches
        counts["overlap_counts"] = file_overlap_counts
        per_file[path.name] = counts

    summary = {
        "pool": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        "fee_pips": FEE_PIPS,
        "source_csvs": sorted(p.name for p in CSV_DIR.glob("swaps_crash*_weth_usdc_500.csv")),
        "method": __doc__.strip(),
        "per_file": per_file,
        "total": total,
        "matching_model_counts": model_matches,
        "overlap_counts": overlap_counts,
        "unexplained_sample": unexplained,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1))
    print(json.dumps({"total": total, "per_file": per_file}, indent=1))
    print(f"written: {OUT}")
    return 0 if total["other"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
