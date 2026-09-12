"""Build supplemental records and snapshot stores for qualified USDS and Pancake pools.

This offline utility converts the pinned qualification evidence into standard
collection_supplement overlay artifacts:
- outputs/source-expansion/usds/records/<block_hash>.json + snapshots/1/<block_hash>/
- outputs/source-expansion/pancake/records/<block_hash>.json + snapshots/1/<block_hash>/

Uses frozen cached evidence by default. No live RPC required.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from swaparch.core.types import BlockRef

ROOT = Path(__file__).resolve().parents[1]
USDS_DIR = ROOT / "outputs/source-expansion/usds"
PANCAKE_DIR = ROOT / "outputs/source-expansion/pancake"

PIN_BLOCKS = (23549939, 23550094, 23550192)


def _write_snapshot_store(
    snapshot_root: Path,
    block: BlockRef,
    calls: list[dict[str, Any]],
) -> None:
    dir_path = snapshot_root / str(block.chain) / block.hash
    dir_path.mkdir(parents=True, exist_ok=True)

    header_path = dir_path / "header.json"
    header_data = {
        "chain": block.chain,
        "number": block.number,
        "hash": block.hash,
        "timestamp": block.timestamp,
    }
    header_path.write_text(json.dumps(header_data, indent=2) + "\n")

    # Format calls as list of call objects conforming to SnapshotStore
    calls_formatted = []
    seen = set()
    for c in calls:
        key = (c["to"].lower(), c["data"].lower())
        if key in seen:
            continue
        seen.add(key)
        calls_formatted.append(
            {
                "to": c["to"],
                "data": c["data"],
                "success": bool(c["success"]),
                "raw": c["raw"],
                "tag": c.get("tag", ""),
            }
        )

    calls_path = dir_path / "calls.json.gz"
    with gzip.open(calls_path, "wt", encoding="utf-8") as gz:
        json.dump(calls_formatted, gz)


def build_usds_overlay(blocks: set[int] | None = None) -> dict[str, Any]:
    if blocks is None:
        blocks = set(PIN_BLOCKS)
    evidence_path = USDS_DIR / "pinned_evidence.json"
    overlay_path = USDS_DIR / "usds_inventory_overlay.json"

    evidence = json.loads(evidence_path.read_text())
    overlay = json.loads(overlay_path.read_text())

    # Only DaiUsdsConverter is qualified for active routing
    conv_raw = next(r for r in overlay["pools"] if r["config"].get("model") == "DaiUsdsConverter")

    records_dir = USDS_DIR / "records"
    snapshots_dir = USDS_DIR / "snapshots"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for item in evidence:
        b = item["block"]
        block_num = b["number"]
        if block_num not in blocks:
            continue
        block = BlockRef(b["chain"], block_num, b["hash"], b["timestamp"])

        # 7 converter calls
        conv_calls = [c for c in item["calls"] if "usds_conv" in c.get("tag", "")]
        _write_snapshot_store(snapshots_dir, block, conv_calls)

        rec_file = records_dir / f"{block.hash}.json"
        record_doc = {
            "blockHash": block.hash,
            "blockNumber": block.number,
            "timestamp": block.timestamp,
            "records": [conv_raw],
        }
        rec_file.write_text(json.dumps(record_doc, indent=2) + "\n")
        results.append({"block": block_num, "block_hash": block.hash, "calls": len(conv_calls)})

    return {"status": "success", "overlay": "usds", "results": results}


def build_pancake_overlay(blocks: set[int] | None = None) -> dict[str, Any]:
    if blocks is None:
        blocks = set(PIN_BLOCKS)
    evidence_path = PANCAKE_DIR / "qualification-evidence.json"
    discovery_path = PANCAKE_DIR / "discovery.json"

    evidence = json.loads(evidence_path.read_text())
    discovery = json.loads(discovery_path.read_text())

    records_by_pool = {
        r["pool_record"]["pool"].lower(): r["pool_record"] for r in discovery["records"]
    }

    records_dir = PANCAKE_DIR / "records"
    snapshots_dir = PANCAKE_DIR / "snapshots"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for pin_data in evidence["results"]:
        b = pin_data["block"]
        block_num = b["number"]
        if block_num not in blocks:
            continue
        block = BlockRef(b["chain"], block_num, b["hash"], b.get("timestamp", 0))

        # Filter to the 3 qualified pools (excluding thin / partial fill)
        all_calls = []
        qualified_records = []
        for p in pin_data["pools"]:
            if "thin" in p["name"]:
                continue
            pool_addr = p["pool"].lower()
            if pool_addr in records_by_pool:
                rec_json = dict(records_by_pool[pool_addr])
                rec_json["status"] = "supported"
                qualified_records.append(rec_json)
                all_calls.extend(p["snapshot_calls"])

        _write_snapshot_store(snapshots_dir, block, all_calls)

        rec_file = records_dir / f"{block.hash}.json"
        record_doc = {
            "blockHash": block.hash,
            "blockNumber": block.number,
            "timestamp": block.timestamp,
            "records": qualified_records,
        }
        rec_file.write_text(json.dumps(record_doc, indent=2) + "\n")
        results.append(
            {
                "block": block_num,
                "block_hash": block.hash,
                "pools": len(qualified_records),
                "calls": len(all_calls),
            }
        )

    return {"status": "success", "overlay": "pancake_v3", "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", help="comma-separated blocks (default: 3 qualification pins)")
    args = parser.parse_args()

    blocks = set(PIN_BLOCKS) if not args.blocks else {int(x) for x in args.blocks.split(",")}
    usds_res = build_usds_overlay(blocks)
    pancake_res = build_pancake_overlay(blocks)
    print("USDS overlay build:", json.dumps(usds_res))
    print("Pancake overlay build:", json.dumps(pancake_res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
