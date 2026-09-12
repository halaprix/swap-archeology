"""Historical WBTC pool state collector for Uniswap V3 connector pools.

Collects full tick and pool state for the 12 verified WBTC connector pools at historical
blocks (defaulting to the three historical qualification pins: start 23549939, stress 23550094,
end 23550192).

Features:
  - Serializes RPC queries with flock (/tmp/swaparch-source-rpc.lock)
  - Uses SnapshotStore and existing UniswapV3Adapter
  - Emits records/<block_hash>.json for seamless integration with collection_supplement
  - Saves collection_manifest.json with block hashes, specs count, and pool state summaries
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wbtc_inventory import (
    DEFAULT_OUT,
    build_wbtc_pool_records,
    export_activation_evidence,
    export_inventory_overlay,
    pool_record_to_dict,
)

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.types import BlockRef
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire

ROOT = Path(__file__).resolve().parents[1]
REFERENCES_FILE = ROOT / "outputs/dense-crash/references.json"
DEFAULT_PINS = (23_549_939, 23_550_094, 23_550_192)


def load_block_reference(number: int, client: RpcClient | None = None) -> BlockRef:
    """Resolve BlockRef from cached references or directly from RPC."""
    if REFERENCES_FILE.is_file():
        data = json.loads(REFERENCES_FILE.read_text())
        for row in data.get("rows", []):
            if row.get("block") == number:
                return BlockRef(
                    chain=1,
                    number=number,
                    hash=str(row["blockHash"]).lower(),
                    timestamp=int(row["timestamp"]),
                )
    if client is None:
        client = RpcClient()
    return client.get_block(number)


def collect_block(
    block_num: int,
    output_dir: Path = DEFAULT_OUT,
    word_radius: int = 8,
    client: RpcClient | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Collect and verify snapshot for the 12 WBTC pools at a given block."""
    snapshots_dir = output_dir / "snapshots"
    records_dir = output_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    store = SnapshotStore(snapshots_dir)
    if client is None:
        client = RpcClient()

    block = load_block_reference(block_num, client)
    records = build_wbtc_pool_records(validated_block_hashes=[block.hash])

    adapter = UniswapV3Adapter(word_radius=word_radius)
    adapters = {"uniswap_v3": adapter}

    if offline:
        snapshot = store.load(block.chain, block.hash)
        report = None
    else:
        snapshot, report = acquire(adapters, records, block, store, client)

    # Verify every pool loads successfully
    loaded_states = []
    pool_summaries = []
    for rec in records:
        state = adapter.load_state(rec, snapshot)
        loaded_states.append(state)
        pool_summaries.append(
            {
                "pool": rec.pool,
                "tokens": f"{state.token0.symbol}/{state.token1.symbol}",
                "fee": state.fee,
                "tick": state.tick,
                "sqrt_price_x96": str(state.sqrt_price_x96),
                "liquidity": str(state.liquidity),
                "word_range": [state.word_lo, state.word_hi],
                "initialized_ticks_count": len(state.tick_liquidity_net),
            }
        )

    # Save records document for downstream supplement_context
    rec_doc = {
        "blockHash": block.hash,
        "blockNumber": block.number,
        "timestamp": block.timestamp,
        "records": [pool_record_to_dict(r) for r in records],
    }
    rec_target = records_dir / f"{block.hash}.json"
    tmp = rec_target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec_doc, indent=2) + "\n")
    tmp.replace(rec_target)

    return {
        "block": block.number,
        "block_hash": block.hash,
        "timestamp": block.timestamp,
        "specs_total": report.specs_total if report else len(snapshot.calls),
        "phases": report.phases if report else 0,
        "network_requests": getattr(client, "network_requests", 0),
        "pools_loaded": len(loaded_states),
        "pools": pool_summaries,
    }


def collect_blocks(
    blocks: list[int] | tuple[int, ...],
    output_dir: Path = DEFAULT_OUT,
    word_radius: int = 8,
    client: RpcClient | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Collect states across multiple blocks and emit manifest."""
    export_inventory_overlay(output_dir)
    export_activation_evidence(output_dir, client)

    if client is None:
        client = RpcClient()

    results = []
    for b in blocks:
        res = collect_block(
            b,
            output_dir=output_dir,
            word_radius=word_radius,
            client=client,
            offline=offline,
        )
        results.append(res)

    manifest = {
        "scope": "Historical WBTC Uniswap V3 connector pools collection",
        "blocks_requested": list(blocks),
        "blocks_collected": len(results),
        "word_radius": word_radius,
        "output_directory": str(output_dir),
        "results": results,
    }

    manifest_file = output_dir / "collection_manifest.json"
    tmp = manifest_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp.replace(manifest_file)
    return manifest


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
        "--word-radius",
        type=int,
        default=8,
        help="Tick bitmap word radius around current tick (default: 8)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Load cached snapshot offline without RPC calls",
    )
    args = parser.parse_args()

    blocks = [int(b.strip()) for b in args.blocks.split(",")] if args.blocks else list(DEFAULT_PINS)
    manifest = collect_blocks(
        blocks,
        output_dir=Path(args.output_dir),
        word_radius=args.word_radius,
        offline=args.offline,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "blocks_collected": manifest["blocks_collected"],
                "manifest": str(Path(args.output_dir) / "collection_manifest.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
