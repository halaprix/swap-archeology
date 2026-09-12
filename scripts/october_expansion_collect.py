"""Bounded October expanded-source collection runner for Ethereum mainnet (blocks 23549939..23550192).

Collects and persists 20 expanded pool records per block across all 254 consecutive blocks:
  - 12 WBTC Uniswap V3 connector pools (WETH/WBTC, WBTC/USDC, WBTC/USDT)
  - 4 USDS AMM Uniswap V3 connector pools (USDS/USDC 1/5/30 bps, USDS/DAI 30 bps)
  - 3 admitted PancakeSwap V3 pools (USDC/WETH 500, WETH/USDT 500, WBTC/WETH 2500)
  - 1 DAI-USDS converter (DaiUsdsConverter 1:1 zero-fee connector; NO UsdsPsmWrapper)

Outputs a standard collection_supplement root at outputs/october-expansion/ containing:
  - records/<blockHash>.json
  - snapshots/1/<blockHash>/header.json
  - snapshots/1/<blockHash>/calls.json.gz
Fully compatible with supplement_contexts / supplement_context.

Safety & Operations:
  - Preserves exact canonical hashes from outputs/dense-crash/references.json.
  - Reuses existing three-pin evidence (23549939, 23550094, 23550192) without redundant RPC queries.
  - Serializes live RPC queries with flock (/tmp/swaparch-source-rpc.lock).
  - Idempotent checkpoints: skips already-collected and verified blocks.
  - Bounded tick coverage (word_radius=8), fail-closed state verification.
  - Never reads .env or prints credentials/endpoints.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from usds_amm_collect import build_usds_amm_pool_records
from wbtc_inventory import build_wbtc_pool_records

from swaparch.adapters.pancake_v3 import PANCAKE_FACTORY, PancakeV3Adapter
from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import (
    BlockRef,
    PoolRecord,
    SupportStatus,
    Token,
    norm_address,
)
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import (
    MakerSkyPsmAdapter,
    acquire,
    implemented_adapters,
    pool_record_from_json,
)

EXPANSION_START_BLOCK = 23549939
EXPANSION_END_BLOCK = 23550192
EXPANSION_BLOCK_COUNT = 254
PIN_BLOCKS = (23549939, 23550094, 23550192)
DEFAULT_WORD_RADIUS = 8

DEFAULT_OUTPUT_DIR = ROOT / "outputs/october-expansion"
REFERENCES_FILE = ROOT / "outputs/dense-crash/references.json"
RPC_LOCK_PATH = "/tmp/swaparch-source-rpc.lock"

# 3 admitted PancakeSwap V3 pools
PANCAKE_ADMITTED_POOLS: list[dict[str, Any]] = [
    {
        "pool": "0x1ac1a8feaaea1900c4166deeed0c11cc10669d36",
        "tokens": (
            Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6),
            Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
        ),
        "fee": 500,
        "tick_spacing": 10,
        "created_block": 16954933,
        "notes": "PancakeSwap V3 USDC/WETH 500 pips (0.05%)",
    },
    {
        "pool": "0x6ca298d2983ab03aa1da7679389d955a4efee15c",
        "tokens": (
            Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
            Token(1, "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6),
        ),
        "fee": 500,
        "tick_spacing": 10,
        "created_block": 16954896,
        "notes": "PancakeSwap V3 WETH/USDT 500 pips (0.05%)",
    },
    {
        "pool": "0x9b5699d18dff51fc65fb8ad6f70d93287c36349f",
        "tokens": (
            Token(1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8),
            Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
        ),
        "fee": 2500,
        "tick_spacing": 50,
        "created_block": 16954980,
        "notes": "PancakeSwap V3 WBTC/WETH 2500 pips (0.25%)",
    },
]

# 1 DAI-USDS converter pool definition
DAI_USDS_CONVERTER_ADDR = "0x3225737a9bbb6473cb4a45b7244aca2befdb276a"
DAI_ADDR = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDS_ADDR = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"


def pool_record_to_dict(rec: PoolRecord) -> dict[str, Any]:
    return {
        "family": rec.family,
        "chain": rec.chain,
        "pool_id": rec.pool_id,
        "deployment": rec.deployment,
        "pool": rec.pool,
        "tokens": [
            {
                "chain": rec.chain,
                "address": t.address,
                "symbol": t.symbol,
                "decimals": t.decimals,
            }
            for t in rec.tokens
        ],
        "config": dict(rec.config),
        "created_block": rec.created_block,
        "discovered_by": dict(rec.discovered_by),
        "status": rec.status.value,
        "notes": rec.notes,
    }


def build_pancake_admitted_records(validated_block_hashes: list[str] | None = None) -> list[PoolRecord]:
    hashes = list(validated_block_hashes or [])
    records: list[PoolRecord] = []
    for meta in PANCAKE_ADMITTED_POOLS:
        pool_addr = norm_address(meta["pool"])
        records.append(
            PoolRecord(
                family="pancake_v3",
                chain=1,
                pool_id=f"pancake_v3:{norm_address(PANCAKE_FACTORY)}:{pool_addr}",
                deployment=norm_address(PANCAKE_FACTORY),
                pool=pool_addr,
                tokens=meta["tokens"],
                config={
                    "fee": meta["fee"],
                    "tick_spacing": meta["tick_spacing"],
                    "factory": norm_address(PANCAKE_FACTORY),
                    "validated_block_hashes": hashes,
                },
                created_block=meta["created_block"],
                discovered_by={
                    "method": "factory:getPool",
                    "factory": norm_address(PANCAKE_FACTORY),
                    "deployed_by_block": meta["created_block"],
                },
                status=SupportStatus.SUPPORTED,
                notes=meta["notes"],
            )
        )
    return records


def build_dai_usds_converter_record(validated_block_hashes: list[str] | None = None) -> PoolRecord:
    hashes = list(validated_block_hashes or [])
    return PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id=f"maker_sky_psm:{norm_address(DAI_USDS_CONVERTER_ADDR)}:{norm_address(DAI_USDS_CONVERTER_ADDR)}",
        deployment=norm_address(DAI_USDS_CONVERTER_ADDR),
        pool=norm_address(DAI_USDS_CONVERTER_ADDR),
        tokens=(
            Token(1, DAI_ADDR, "DAI", 18),
            Token(1, USDS_ADDR, "USDS", 18),
        ),
        config={
            "kind": "converter",
            "model": "DaiUsdsConverter",
            "rate": "1:1 exact, no fee",
            "directions": [
                {
                    "name": "daiToUsds",
                    "token_in": DAI_ADDR,
                    "token_out": USDS_ADDR,
                    "rate": "1:1 exact integer",
                },
                {
                    "name": "usdsToDai",
                    "token_in": USDS_ADDR,
                    "token_out": DAI_ADDR,
                    "rate": "1:1 exact integer",
                },
            ],
            "validated_block_hashes": hashes,
        },
        created_block=None,
        discovered_by={
            "method": "curated",
            "deployed_by_block": EXPANSION_START_BLOCK,
            "evidence": ["https://github.com/sky-ecosystem/usds"],
        },
        status=SupportStatus.SUPPORTED,
        notes="1:1 zero-fee connector between DAI and USDS via DSS DaiJoin/UsdsJoin.",
    )


def build_expansion_records(block_hash: str) -> list[PoolRecord]:
    """Construct the exact 20 expanded pool records for a given block."""
    norm_hash = block_hash.lower()
    hashes = [norm_hash]

    wbtc_records = build_wbtc_pool_records(validated_block_hashes=hashes)
    assert len(wbtc_records) == 12

    usds_amm_records = build_usds_amm_pool_records(validated_block_hashes=hashes)
    assert len(usds_amm_records) == 4

    pancake_records = build_pancake_admitted_records(validated_block_hashes=hashes)
    assert len(pancake_records) == 3

    converter_record = build_dai_usds_converter_record(validated_block_hashes=hashes)

    records = list(wbtc_records) + list(usds_amm_records) + list(pancake_records) + [converter_record]
    assert len(records) == 20
    assert len({r.pool_id for r in records}) == 20
    return records


def load_october_blocks(references_file: Path | None = None) -> list[BlockRef]:
    """Load all 254 consecutive block references from references.json."""
    ref_path = references_file or REFERENCES_FILE
    if not ref_path.is_file():
        raise FileNotFoundError(f"References file not found: {ref_path}")
    data = json.loads(ref_path.read_text())
    rows_by_block = {r["block"]: r for r in data.get("rows", [])}

    blocks: list[BlockRef] = []
    for num in range(EXPANSION_START_BLOCK, EXPANSION_END_BLOCK + 1):
        if num not in rows_by_block:
            raise KeyError(f"Block {num} not found in {ref_path}")
        row = rows_by_block[num]
        blocks.append(
            BlockRef(
                chain=1,
                number=num,
                hash=str(row["blockHash"]).lower(),
                timestamp=int(row["timestamp"]),
            )
        )
    return blocks


@contextmanager
def rpc_lock(lock_path: str = RPC_LOCK_PATH):
    """File lock for serializing RPC collection across processes."""
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def is_block_collected(output_dir: Path, block: BlockRef) -> bool:
    """Verify if a block has already been collected, stored, and states load cleanly.

    Performs strict validation:
      - Compares complete BlockRef (chain, number, hash, timestamp) across records and snapshot header.
      - Asserts exactly 20 records matching the unique expected expanded pool IDs.
      - Validates snapshot loads non-empty calls and all 20 states load without error.
      - Refuses any malformed or corrupted checkpoint.
    """
    rec_path = output_dir / "records" / f"{block.hash}.json"
    snapshot_dir = output_dir / "snapshots" / str(block.chain) / block.hash
    header_path = snapshot_dir / "header.json"
    calls_path = snapshot_dir / "calls.json.gz"

    if not (rec_path.is_file() and header_path.is_file() and calls_path.is_file()):
        return False

    try:
        doc = json.loads(rec_path.read_text())
        if (
            doc.get("blockHash") != block.hash
            or doc.get("blockNumber") != block.number
            or doc.get("timestamp") != block.timestamp
        ):
            return False

        raw_records = doc.get("records", [])
        if len(raw_records) != 20:
            return False

        records = [pool_record_from_json(r) for r in raw_records]
        actual_pool_ids = [r.pool_id for r in records]
        if len(set(actual_pool_ids)) != 20:
            return False

        expected_pool_ids = {r.pool_id for r in build_expansion_records(block.hash)}
        if set(actual_pool_ids) != expected_pool_ids:
            return False

        # Verify snapshot loads with complete matching BlockRef
        store = SnapshotStore(output_dir / "snapshots")
        snap = store.load(block.chain, block.hash)
        if (
            snap.block.chain != block.chain
            or snap.block.number != block.number
            or snap.block.hash != block.hash
            or snap.block.timestamp != block.timestamp
            or len(snap.calls) == 0
        ):
            return False

        # Verify all 20 states load cleanly
        adapters = implemented_adapters()
        for r in records:
            adapters[r.family].load_state(r, snap)
        return True
    except (
        json.JSONDecodeError,
        KeyError,
        ValueError,
        OSError,
        gzip.BadGzipFile,
        TypeError,
        Unsupported,
    ):
        return False


def seed_pinned_evidence(output_dir: Path) -> int:
    """Seed existing three-pin evidence into outputs/october-expansion without RPC."""
    source_expansion = ROOT / "outputs/source-expansion"
    overlay_sources = [
        ("wbtc", source_expansion / "wbtc"),
        ("usds", source_expansion / "usds"),
        ("pancake", source_expansion / "pancake"),
        ("usds-amm", source_expansion / "usds-amm"),
    ]

    records_dir = output_dir / "records"
    snapshots_dir = output_dir / "snapshots"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    blocks = {b.number: b for b in load_october_blocks()}
    seeded_count = 0

    for pin in PIN_BLOCKS:
        block = blocks[pin]
        if is_block_collected(output_dir, block):
            seeded_count += 1
            continue

        all_calls: dict[tuple[str, str], dict[str, Any]] = {}
        for name, src_dir in overlay_sources:
            call_file = src_dir / "snapshots/1" / block.hash / "calls.json.gz"
            if not call_file.is_file():
                continue
            with gzip.open(call_file, "rt") as gz:
                calls = json.load(gz)
                for c in calls:
                    key = (c["to"].lower(), c["data"].lower())
                    if key not in all_calls:
                        all_calls[key] = {
                            "to": c["to"],
                            "data": c["data"],
                            "tag": c.get("tag", ""),
                            "success": bool(c["success"]),
                            "raw": str(c["raw"]).lower(),
                            "via": c.get("via", "pin_evidence"),
                        }

        if not all_calls:
            continue

        # Write snapshot header & calls
        dir_path = snapshots_dir / "1" / block.hash
        dir_path.mkdir(parents=True, exist_ok=True)
        header_path = dir_path / "header.json"
        header_data = {
            "chain": block.chain,
            "number": block.number,
            "hash": block.hash,
            "timestamp": block.timestamp,
        }
        header_path.write_text(json.dumps(header_data, indent=2) + "\n")

        calls_path = dir_path / "calls.json.gz"
        tmp_calls = dir_path / "calls.json.gz.tmp"
        with gzip.open(tmp_calls, "wt", encoding="utf-8") as gz:
            json.dump(list(all_calls.values()), gz)
        tmp_calls.replace(calls_path)

        # Write 20 records
        records = build_expansion_records(block.hash)
        rec_doc = {
            "blockHash": block.hash,
            "blockNumber": block.number,
            "timestamp": block.timestamp,
            "records": [pool_record_to_dict(r) for r in records],
        }
        rec_file = records_dir / f"{block.hash}.json"
        tmp_rec = rec_file.with_suffix(".json.tmp")
        tmp_rec.write_text(json.dumps(rec_doc, indent=2) + "\n")
        tmp_rec.replace(rec_file)

        # Validate
        if is_block_collected(output_dir, block):
            seeded_count += 1

    return seeded_count


def collect_block(
    block: BlockRef,
    output_dir: Path,
    word_radius: int = 8,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Collect full snapshot for all 20 expanded pools at a single block via aggregated Multicall."""
    if client is None:
        client = RpcClient()

    snapshots_dir = output_dir / "snapshots"
    records_dir = output_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    store = SnapshotStore(snapshots_dir)
    records = build_expansion_records(block.hash)

    adapters = {
        "uniswap_v3": UniswapV3Adapter(word_radius=word_radius),
        "pancake_v3": PancakeV3Adapter(word_radius=word_radius),
        "maker_sky_psm": MakerSkyPsmAdapter(),
    }

    req_before = getattr(client, "network_requests", 0)
    t0 = time.perf_counter()
    snapshot, report = acquire(adapters, records, block, store, client)
    elapsed = time.perf_counter() - t0
    req_after = getattr(client, "network_requests", 0)
    network_reqs = req_after - req_before

    # Verify all 20 states load cleanly
    loaded_states = []
    for r in records:
        st = adapters[r.family].load_state(r, snapshot)
        loaded_states.append(st)

    # Write records file
    rec_doc = {
        "blockHash": block.hash,
        "blockNumber": block.number,
        "timestamp": block.timestamp,
        "records": [pool_record_to_dict(r) for r in records],
    }
    rec_file = records_dir / f"{block.hash}.json"
    tmp_rec = rec_file.with_suffix(".json.tmp")
    tmp_rec.write_text(json.dumps(rec_doc, indent=2) + "\n")
    tmp_rec.replace(rec_file)

    return {
        "block": block.number,
        "block_hash": block.hash,
        "timestamp": block.timestamp,
        "specs_total": report.specs_total,
        "phases": report.phases,
        "network_requests": network_reqs,
        "elapsed_seconds": round(elapsed, 3),
        "pools_loaded": len(loaded_states),
    }


def collect_expansion(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    word_radius: int = 8,
    benchmark_count: int = 3,
    rate_limit_delay: float = 0.05,
    max_blocks: int | None = None,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Collect all 254 blocks in the October crash window with benchmarking and manifest emission."""
    t_start = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    blocks = load_october_blocks()
    print(f"Loaded {len(blocks)} blocks ({blocks[0].number}..{blocks[-1].number})")

    # Step 1: Seed existing 3-pin evidence
    seeded = seed_pinned_evidence(output_dir)
    print(f"Seeded {seeded} qualification pin blocks from existing evidence.")

    # Identify blocks needing collection
    pending_blocks: list[BlockRef] = []
    for b in blocks:
        if not is_block_collected(output_dir, b):
            pending_blocks.append(b)

    print(f"Blocks already collected: {len(blocks) - len(pending_blocks)} / {len(blocks)}")
    print(f"Blocks pending collection: {len(pending_blocks)}")

    if max_blocks is not None and max_blocks < len(pending_blocks):
        pending_blocks = pending_blocks[:max_blocks]
        print(f"Bounded to first {len(pending_blocks)} pending blocks via --max-blocks")

    results: list[dict[str, Any]] = []

    if pending_blocks:
        if client is None:
            client = RpcClient()

        # Step 2: Acquire RPC lock for live collection
        print(f"Acquiring RPC lock on {RPC_LOCK_PATH}...")
        with rpc_lock(RPC_LOCK_PATH):
            print("RPC lock acquired.")

            # Step 3: Benchmark first few blocks
            bench_target = min(benchmark_count, len(pending_blocks))
            bench_times: list[float] = []
            print(f"--- Benchmarking first {bench_target} blocks ---")

            for i in range(bench_target):
                b = pending_blocks[i]
                res = collect_block(b, output_dir, word_radius=word_radius, client=client)
                bench_times.append(res["elapsed_seconds"])
                results.append(res)
                print(
                    f"[Benchmark {i+1}/{bench_target}] Block {b.number}: "
                    f"{res['specs_total']} specs in {res['phases']} phases, "
                    f"{res['network_requests']} RPC reqs, {res['elapsed_seconds']}s"
                )
                if rate_limit_delay > 0:
                    time.sleep(rate_limit_delay)

            avg_time = sum(bench_times) / len(bench_times) if bench_times else 0.0
            remaining_count = len(pending_blocks) - bench_target
            est_seconds = remaining_count * (avg_time + rate_limit_delay)
            est_minutes = est_seconds / 60.0
            print(
                f"Benchmark summary: avg {avg_time:.2f}s/block. "
                f"Estimated time for remaining {remaining_count} blocks: "
                f"{est_seconds:.1f}s ({est_minutes:.1f}m)"
            )

            # Step 4: Collect remaining blocks
            for i, b in enumerate(pending_blocks[bench_target:], start=bench_target + 1):
                res = collect_block(b, output_dir, word_radius=word_radius, client=client)
                results.append(res)
                if i % 10 == 0 or i == len(pending_blocks):
                    print(
                        f"[{i}/{len(pending_blocks)}] Block {b.number}: "
                        f"{res['specs_total']} specs, {res['network_requests']} RPC reqs, "
                        f"{res['elapsed_seconds']}s"
                    )
                if rate_limit_delay > 0:
                    time.sleep(rate_limit_delay)

    total_elapsed = time.perf_counter() - t_start

    # Validate all 254 blocks
    missing = [b.number for b in blocks if not is_block_collected(output_dir, b)]
    collected_count = len(blocks) - len(missing)

    if missing and max_blocks is None:
        raise RuntimeError(f"Collection incomplete! Missing {len(missing)} blocks: {missing[:10]}...")

    status = "complete" if not missing else "partial"

    manifest = {
        "scope": "Historical October expanded-source collection (20 pools / block)",
        "start_block": EXPANSION_START_BLOCK,
        "end_block": EXPANSION_END_BLOCK,
        "total_blocks": len(blocks),
        "blocks_collected": collected_count,
        "word_radius": word_radius,
        "pools_per_block": 20,
        "families": ["uniswap_v3", "pancake_v3", "maker_sky_psm"],
        "pool_breakdown": {
            "uniswap_v3_wbtc": 12,
            "uniswap_v3_usds_amm": 4,
            "pancake_v3": 3,
            "maker_sky_psm_converter": 1,
        },
        "output_directory": str(output_dir),
        "total_elapsed_seconds": round(total_elapsed, 2),
        "status": status,
        "missing_blocks": missing,
    }

    manifest_file = output_dir / "collection_manifest.json"
    tmp_m = manifest_file.with_suffix(".json.tmp")
    tmp_m.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp_m.replace(manifest_file)

    if not missing:
        print(f"Collection complete: 254/254 blocks collected in {total_elapsed:.1f}s.")
    else:
        print(f"Collection progress: {collected_count}/254 blocks collected ({len(missing)} pending).")
    print(f"Manifest written to {manifest_file}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--word-radius",
        type=int,
        default=DEFAULT_WORD_RADIUS,
        help="Tick bitmap word radius (default: 8)",
    )
    parser.add_argument(
        "--benchmark-count",
        type=int,
        default=3,
        help="Number of initial blocks to benchmark (default: 3)",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.05,
        help="Pause in seconds between blocks (default: 0.05)",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=None,
        help="Maximum number of pending blocks to collect (default: all)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only seed existing three-pin evidence without running live RPC",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    if args.seed_only:
        seeded = seed_pinned_evidence(out_dir)
        print(f"Seeded {seeded} pins to {out_dir}")
        return

    manifest = collect_expansion(
        output_dir=out_dir,
        word_radius=args.word_radius,
        benchmark_count=args.benchmark_count,
        rate_limit_delay=args.rate_limit_delay,
        max_blocks=args.max_blocks,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
