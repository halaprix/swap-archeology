"""Tests for historical WBTC pool state collection and snapshot persistence."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest
from wbtc_collect import load_block_reference
from wbtc_inventory import DEFAULT_OUT

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import pool_record_from_json

PINS_DATA = [
    (23_549_939, "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12"),
    (23_550_094, "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d"),
    (23_550_192, "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c"),
]


@pytest.mark.parametrize("block_num,expected_hash", PINS_DATA)
def test_block_references_match_pinned_hashes(block_num, expected_hash):
    """Verify block references resolve to expected historical hashes."""
    block = load_block_reference(block_num)
    assert block.number == block_num
    assert block.hash.lower() == expected_hash.lower()


def test_snapshots_and_records_exist():
    """Verify that snapshots and supplement record files are generated for the 3 pins."""
    store = SnapshotStore(DEFAULT_OUT / "snapshots")
    adapter = UniswapV3Adapter(word_radius=8)

    records_dir = DEFAULT_OUT / "records"
    assert records_dir.is_dir(), "Records directory does not exist"

    manifest_file = DEFAULT_OUT / "collection_manifest.json"
    if not manifest_file.is_file():
        pytest.skip("Collection manifest not yet generated (collection in progress)")

    manifest = json.loads(manifest_file.read_text())
    assert manifest["blocks_collected"] == 3

    for block_num, block_hash in PINS_DATA:
        # Verify snapshot load
        snapshot = store.load(1, block_hash)
        assert snapshot.block.number == block_num
        assert snapshot.block.hash.lower() == block_hash.lower()

        # Verify supplement record file
        rec_path = records_dir / f"{block_hash.lower()}.json"
        assert rec_path.is_file(), f"Missing supplement record {rec_path}"
        rec_doc = json.loads(rec_path.read_text())
        assert rec_doc["blockHash"].lower() == block_hash.lower()
        assert len(rec_doc["records"]) == 12

        # Verify adapter loads state for all 12 pools
        records = [pool_record_from_json(r) for r in rec_doc["records"]]
        states = [adapter.load_state(r, snapshot) for r in records]
        assert len(states) == 12

        for s in states:
            assert s.token0.symbol == "WBTC"
            assert s.token1.symbol in {"WETH", "USDC", "USDT"}
            assert s.liquidity > 0
            assert s.sqrt_price_x96 > 0
            assert s.word_lo <= s.word_hi
            assert len(s.tick_bitmap) > 0
            # At least one initialized tick must be loaded in the window
            assert len(s.tick_liquidity_net) >= 1
