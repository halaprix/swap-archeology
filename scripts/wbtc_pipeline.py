"""End-to-end pipeline runner for historical WBTC connector pools.

Runs:
  1. Inventory overlay generation (12 pools, canonical tokens, strict ordering)
  2. Activation evidence generation (creation logs + start pin factory verification)
  3. Bounded snapshot collection across historical qualification pins
  4. QuoterV2 differential parity verification
  5. Two-leg routing evaluation and full input consumption checks

Usage:
  # Live (serialized with flock):
  flock /tmp/swaparch-source-rpc.lock uv run python scripts/wbtc_pipeline.py

  # Offline verification using cached snapshots and reports:
  uv run python scripts/wbtc_pipeline.py --offline
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wbtc_collect import DEFAULT_PINS, collect_blocks
from wbtc_inventory import (
    DEFAULT_OUT,
    export_activation_evidence,
    export_inventory_overlay,
)
from wbtc_quoter_check import run_differential_checks
from wbtc_route_eval import evaluate_all_pins


def run_pipeline(
    blocks: list[int] | tuple[int, ...] = DEFAULT_PINS,
    output_dir: Path = DEFAULT_OUT,
    offline: bool = False,
) -> dict[str, Any]:
    """Execute the full WBTC source overlay pipeline."""
    print("=== Step 1: Exporting WBTC inventory overlay ===")
    inv_path = export_inventory_overlay(output_dir)
    print(f"Inventory written to: {inv_path}")

    print("\n=== Step 2: Exporting activation bound evidence ===")
    act_path = export_activation_evidence(output_dir)
    print(f"Activation evidence written to: {act_path}")

    print(f"\n=== Step 3: Collecting snapshots for pins {list(blocks)} ===")
    manifest = collect_blocks(blocks, output_dir=output_dir, offline=offline)
    print(
        f"Collected {manifest['blocks_collected']} blocks. Manifest: {output_dir / 'collection_manifest.json'}"
    )

    quoter_report = None
    if not offline:
        print("\n=== Step 4: Differential QuoterV2 checks ===")
        quoter_report = run_differential_checks(blocks, output_dir=output_dir)
        print(
            f"Quoter checks complete. Overall exact match: {quoter_report['overall_exact_match']}"
        )
    else:
        q_file = output_dir / "quoter_checks.json"
        if q_file.is_file():
            quoter_report = json.loads(q_file.read_text())
            print(
                f"Loaded existing quoter checks report. Overall exact match: {quoter_report.get('overall_exact_match')}"
            )

    print("\n=== Step 5: Evaluating two-leg routes and input consumption ===")
    eval_summary = evaluate_all_pins(blocks, output_dir=output_dir)
    print(
        f"Two-leg evaluation complete. Overall 100% input consumed: {eval_summary['overall_full_input_consumed']}"
    )

    summary = {
        "status": "success",
        "scope": "Historical WBTC pool inventory and collection overlay",
        "blocks": list(blocks),
        "inventory_file": str(inv_path),
        "activation_file": str(act_path),
        "collection_manifest": str(output_dir / "collection_manifest.json"),
        "quoter_checks": str(output_dir / "quoter_checks.json") if quoter_report else None,
        "two_leg_eval": str(output_dir / "two_leg_eval.json"),
        "quoter_exact_match": quoter_report.get("overall_exact_match") if quoter_report else None,
        "full_input_consumed": eval_summary.get("overall_full_input_consumed"),
    }

    summary_file = output_dir / "pipeline_summary.json"
    tmp = summary_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=2) + "\n")
    tmp.replace(summary_file)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blocks",
        help="Comma-separated block numbers (defaults to qualification pins: 23549939,23550094,23550192)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUT),
        help="Output directory for WBTC artifacts",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run offline using cached snapshots and records",
    )
    args = parser.parse_args()

    blocks = [int(b.strip()) for b in args.blocks.split(",")] if args.blocks else list(DEFAULT_PINS)
    res = run_pipeline(blocks, output_dir=Path(args.output_dir), offline=args.offline)
    print("\n=== Pipeline Summary ===")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
