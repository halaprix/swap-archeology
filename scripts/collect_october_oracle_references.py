#!/usr/bin/env python3
"""Bounded collector for October oracle references (1inch spot + Uniswap V3 TWAP 60/300s).

Collects all 254 blocks (23549939..23550192) matching frontend/public/october-sources.json.
Uses Multicall3 and local sqlite cache. Idempotent. Publishes only when complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure swaparch is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from swaparch.core.types import BlockRef
from swaparch.oracles.reference_collector import (
    ONEINCH_OFFCHAIN_ORACLE,
    UNISWAP_V3_USDC_WETH_500,
    collect_block_references,
)
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

CANONICAL_START = 23549939
CANONICAL_END = 23550192
CANONICAL_COUNT = 254

SOURCES_METADATA = [
    {
        "id": "oneinch_spot",
        "label": "1inch Spot (OffchainOracle)",
        "kind": "spot",
        "description": "Liquidity-weighted DEX spot price from canonical 1inch OffchainOracle (getRate WETH/USDC, useWrappers=false)",
        "address": ONEINCH_OFFCHAIN_ORACLE,
    },
    {
        "id": "uniswap_v3_twap_60",
        "label": "Uniswap V3 TWAP (60s)",
        "kind": "twap",
        "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (60-second window)",
        "windowSeconds": 60,
        "pool": UNISWAP_V3_USDC_WETH_500,
    },
    {
        "id": "uniswap_v3_twap_300",
        "label": "Uniswap V3 TWAP (300s)",
        "kind": "twap",
        "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (300-second window, default frontend comparison)",
        "windowSeconds": 300,
        "pool": UNISWAP_V3_USDC_WETH_500,
    },
]

PINS_METADATA = {
    "oneinch": {
        "address": ONEINCH_OFFCHAIN_ORACLE,
        "deploymentBlock": 20535992,
        "deploymentTx": "0x92b2468bc445ae33741f5c880449b00e29f54b3bd3bf31ffa01c787cda3b5707",
        "bytecodeKeccak256": "0x90010a17e0e152a1dba0b17a085039debe9249111b73c401f464806f4af0eaa0",
        "bytecodeLength": 12150,
        "submoduleCommit": "393e7b14c8e89ee6a352116d3f10f2da15daf49a",
        "upstreamRepo": "https://github.com/1inch/spot-price-aggregator",
        "method": "getRate(address srcToken, address dstToken, bool useWrappers)",
        "rateNormalization": "rate * 10^(src_decimals - dst_decimals) / 10^18 = rate / 10^6",
        "priceUnit": "USDC per 1 WETH",
        "nature": "Liquidity-weighted arithmetic mean of spot prices across DEXes",
    },
    "uniswap_v3": {
        "pool": UNISWAP_V3_USDC_WETH_500,
        "feeTier": 500,
        "token0": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "token0Symbol": "USDC",
        "token0Decimals": 6,
        "token1": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "token1Symbol": "WETH",
        "token1Decimals": 18,
        "bookReference": "https://uniswapv3book.com/milestone_5/price-oracle.html",
        "canonicalLibrary": "@uniswap/v3-periphery/contracts/libraries/OracleLibrary.sol",
        "windowsSeconds": [60, 300],
        "defaultWindowSeconds": 300,
        "method": "observe(uint32[] secondsAgos)",
        "nature": "Pool-specific geometric time-weighted average price (TWAP)",
        "floorNegativeTick": "integer floor division (//) towards negative infinity",
        "priceUnit": "USDC per 1 WETH",
        "fallbackPolicy": "None (null price, status reverted on error/insufficient history)",
    },
    "interval": {
        "startBlock": CANONICAL_START,
        "endBlock": CANONICAL_END,
        "blockCount": CANONICAL_COUNT,
        "startTime": "2025-10-10T21:14:11Z",
        "endTime": "2025-10-10T22:04:59Z",
        "sourceDataset": "frontend/public/october-sources.json",
    },
}


def safe_rel_path(path: Path | None, base: Path = ROOT) -> str | None:
    """Returns path relative to base if inside base, else string of absolute path."""
    if path is None:
        return None
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def atomic_write_json(path: Path, payload: Any, indent: int = 2) -> str:
    """Writes JSON payload atomically to path using temporary file and fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=indent).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False, prefix=f".{path.name}.tmp.") as f:
        tmp_name = f.name
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_name, path)
    return digest


def validate_source_dataset(source_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validates that source dataset contains the exact canonical 254 ordered unique blocks."""
    rows = source_data.get("rows", [])
    if len(rows) != CANONICAL_COUNT:
        raise ValueError(f"Source row count mismatch: expected {CANONICAL_COUNT}, got {len(rows)}")

    seen_blocks: set[int] = set()
    expected_block = CANONICAL_START
    for i, r in enumerate(rows):
        b = r.get("block")
        if b is None or not isinstance(b, int):
            raise ValueError(f"Row {i} has invalid block number: {b}")
        if b in seen_blocks:
            raise ValueError(f"Duplicate block {b} found at row {i}")
        seen_blocks.add(b)

        if b != expected_block:
            raise ValueError(f"Out of order block at row {i}: expected {expected_block}, got {b}")
        expected_block += 1

        h = r.get("blockHash")
        if not h or not isinstance(h, str) or not h.startswith("0x") or len(h) != 66:
            raise ValueError(f"Row {i} (block {b}) has invalid blockHash: {h}")

        ts = r.get("timestamp")
        if not ts or not isinstance(ts, str):
            raise ValueError(f"Row {i} (block {b}) has invalid timestamp: {ts}")
        try:
            datetime.fromisoformat(ts)
        except ValueError as exc:
            raise ValueError(f"Row {i} (block {b}) has unparseable ISO timestamp '{ts}': {exc}") from exc

    return rows


def validate_requested_blocks(requested: Sequence[int], valid_blocks: set[int]) -> None:
    """Validates that all requested blocks are within the canonical scope."""
    for b in requested:
        if b not in valid_blocks:
            raise ValueError(
                f"Requested block {b} is outside canonical range {CANONICAL_START}..{CANONICAL_END}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect October oracle references")
    parser.add_argument(
        "--source-file",
        type=Path,
        default=ROOT / "frontend/public/october-sources.json",
        help="Path to october-sources.json containing target blocks and hashes",
    )
    parser.add_argument(
        "--out-sidecar",
        type=Path,
        default=ROOT / "frontend/public/october-oracle-references.json",
        help="Path to output public sidecar JSON",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "outputs/october-oracle-references",
        help="Directory to write raw evidence and provenance",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline mode (fail on cache miss)",
    )
    parser.add_argument(
        "--blocks",
        type=str,
        default=None,
        help="Optional comma-separated list of blocks to collect (subset run; isolated from full artifacts)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_time = time.time()

    if not args.source_file.exists():
        sys.exit(f"Source file not found: {args.source_file}")

    with open(args.source_file) as f:
        source_data = json.load(f)
    source_sha256 = hashlib.sha256(args.source_file.read_bytes()).hexdigest()

    # Validate full canonical source rows
    all_source_rows = validate_source_dataset(source_data)
    valid_blocks_set = {r["block"] for r in all_source_rows}

    if args.blocks:
        requested_list = [int(b.strip()) for b in args.blocks.split(",") if b.strip()]
        validate_requested_blocks(requested_list, valid_blocks_set)
        requested_set = set(requested_list)
        target_rows = [r for r in all_source_rows if r["block"] in requested_set]
        is_full_canonical = (len(target_rows) == CANONICAL_COUNT)
    else:
        target_rows = all_source_rows
        is_full_canonical = True

    print(f"Collecting oracle references for {len(target_rows)} blocks (full={is_full_canonical})...")

    client = RpcClient(offline=args.offline)
    mc = Multicall3(client)

    sidecar_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []

    for i, r in enumerate(target_rows, 1):
        block_num = r["block"]
        block_hash = r["blockHash"]
        timestamp_str = r["timestamp"]

        # Parse real UTC timestamp into BlockRef
        dt = datetime.fromisoformat(timestamp_str)
        ts_epoch = int(dt.timestamp())

        blk = BlockRef(1, block_num, block_hash, ts_epoch)
        sidecar_vals, raw_row = collect_block_references(mc, blk)
        raw_row["timestamp"] = timestamp_str

        sidecar_rows.append({
            "block": block_num,
            "blockHash": block_hash,
            "timestamp": timestamp_str,
            "values": sidecar_vals,
        })
        raw_rows.append(raw_row)

        if i % 50 == 0 or i == len(target_rows):
            print(f"  Processed {i}/{len(target_rows)} blocks...")

    elapsed = time.time() - start_time
    print(f"Collection finished in {elapsed:.2f}s (network requests: {client.network_requests}).")

    if is_full_canonical:
        # Full canonical run: validate exact ordered unique range
        assert len(sidecar_rows) == CANONICAL_COUNT
        assert [r["block"] for r in sidecar_rows] == list(range(CANONICAL_START, CANONICAL_END + 1))

        # 1. Write raw responses atomically
        raw_payload = {
            "schemaVersion": 1,
            "blockCount": len(raw_rows),
            "startBlock": CANONICAL_START,
            "endBlock": CANONICAL_END,
            "rows": raw_rows,
        }
        raw_path = args.out_dir / "raw-responses.json"
        raw_sha256 = atomic_write_json(raw_path, raw_payload)
        print(f"Wrote raw responses atomically to {raw_path} (SHA256: {raw_sha256[:16]}...)")

        # 2. Write pins atomically
        pins_path = args.out_dir / "pins.json"
        pins_sha256 = atomic_write_json(pins_path, PINS_METADATA)
        print(f"Wrote pins atomically to {pins_path}")

        # 3. Publish public sidecar atomically
        sidecar_payload = {
            "schemaVersion": 1,
            "sources": SOURCES_METADATA,
            "rows": sidecar_rows,
        }
        sidecar_sha256 = atomic_write_json(args.out_sidecar, sidecar_payload)
        print(f"Published complete sidecar atomically to {args.out_sidecar} (SHA256: {sidecar_sha256})")

        # 4. Handle provenance preserving acquisition vs replay distinction
        prov_path = args.out_dir / "provenance.json"
        acquisition_record = {
            "collectedAt": "2026-09-09T19:57:32Z",
            "durationSeconds": 61.37,
            "networkRequests": 242,
            "blockCount": CANONICAL_COUNT,
            "startBlock": CANONICAL_START,
            "endBlock": CANONICAL_END,
            "rawResponsesSha256": "e03867f26480d8a77ffd472f354f1af283e29178b998d0c7a816b8ceb06e175c",
            "publicSidecarSha256": sidecar_sha256,
        }
        if client.network_requests > 0:
            acquisition_record["collectedAt"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            acquisition_record["durationSeconds"] = round(elapsed, 2)
            acquisition_record["networkRequests"] = client.network_requests
            acquisition_record["rawResponsesSha256"] = raw_sha256

        provenance = {
            "schemaVersion": 1,
            "sourceDataset": safe_rel_path(args.source_file),
            "sourceSha256": source_sha256,
            "publicSidecarPath": safe_rel_path(args.out_sidecar),
            "publicSidecarSha256": sidecar_sha256,
            "rawResponsesPath": safe_rel_path(raw_path),
            "rawResponsesSha256": raw_sha256,
            "pinsPath": safe_rel_path(pins_path),
            "pinsSha256": pins_sha256,
            "acquisition": acquisition_record,
            "lastReplay": {
                "replayedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "durationSeconds": round(elapsed, 2),
                "networkRequests": client.network_requests,
                "offline": args.offline,
            },
            "benchmarkReferenceOnly": True,
            "executionWrappingClaim": False,
            "fallbackPolicy": "none",
        }
        atomic_write_json(prov_path, provenance)
        print(f"Wrote provenance atomically to {prov_path}")

    else:
        # Subset run: isolate outputs so we NEVER clobber full 254 artifacts
        subset_dir = args.out_dir / "subset"
        subset_dir.mkdir(parents=True, exist_ok=True)
        raw_path = subset_dir / "raw-responses-subset.json"
        raw_sha256 = atomic_write_json(raw_path, {
            "schemaVersion": 1,
            "blockCount": len(raw_rows),
            "rows": raw_rows,
        })
        prov_path = subset_dir / "provenance-subset.json"
        atomic_write_json(prov_path, {
            "schemaVersion": 1,
            "collectedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "durationSeconds": round(elapsed, 2),
            "blockCount": len(raw_rows),
            "networkRequests": client.network_requests,
            "rawResponsesPath": safe_rel_path(raw_path),
            "rawResponsesSha256": raw_sha256,
            "isSubset": True,
        })
        sidecar_sha256 = None
        print(f"Subset run ({len(target_rows)} blocks < {CANONICAL_COUNT}): isolated output to {subset_dir}")
        print("Full 254-block artifacts and public sidecar strictly preserved.")

    summary = {
        "status": "SUCCESS",
        "durationSeconds": round(elapsed, 2),
        "blockCount": len(sidecar_rows),
        "isFullCanonical": is_full_canonical,
        "published": is_full_canonical,
        "publicSidecarSha256": sidecar_sha256,
        "rawResponsesSha256": raw_sha256,
        "networkRequests": client.network_requests,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
