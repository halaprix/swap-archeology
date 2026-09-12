"""Targeted tests for October expanded-source collection (scripts/october_expansion_collect.py).

Verifies:
1. 20 pool records per block: 12 WBTC UniV3, 4 USDS AMM UniV3, 3 Pancake V3, 1 DAI-USDS converter.
2. Zero duplicate pool IDs and NO UsdsPsmWrapper.
3. Accurate block reference resolution for all 254 blocks (23549939..23550192).
4. Reusing existing three-pin evidence produces a valid collection_supplement root.
5. Compatibility with supplement_contexts (77 baseline + 20 expansion = 97 states).
6. Idempotent checkpoint detection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts.october_expansion_collect import (
    EXPANSION_BLOCK_COUNT,
    EXPANSION_END_BLOCK,
    EXPANSION_START_BLOCK,
    build_expansion_records,
    is_block_collected,
    load_october_blocks,
    seed_pinned_evidence,
)
from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_contexts
from swaparch.core.types import BlockRef, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver


def test_block_range_and_references():
    blocks = load_october_blocks()
    assert len(blocks) == 254
    assert len(blocks) == EXPANSION_BLOCK_COUNT
    assert blocks[0].number == EXPANSION_START_BLOCK == 23549939
    assert blocks[-1].number == EXPANSION_END_BLOCK == 23550192

    # Verify consecutive block numbers
    for i in range(len(blocks) - 1):
        assert blocks[i + 1].number == blocks[i].number + 1

    # Verify pin block hashes match canonical hashes
    pin_hashes = {
        23549939: "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
        23550094: "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d",
        23550192: "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c",
    }
    block_map = {b.number: b for b in blocks}
    for pin, expected_hash in pin_hashes.items():
        assert block_map[pin].hash == expected_hash


def test_build_expansion_records_structure():
    dummy_hash = "0x" + "aa" * 32
    records = build_expansion_records(dummy_hash)

    assert len(records) == 20

    # Count by family
    univ3_records = [r for r in records if r.family == "uniswap_v3"]
    pancake_records = [r for r in records if r.family == "pancake_v3"]
    maker_records = [r for r in records if r.family == "maker_sky_psm"]

    assert len(univ3_records) == 16  # 12 WBTC + 4 USDS AMM
    assert len(pancake_records) == 3  # 3 Pancake V3
    assert len(maker_records) == 1  # 1 DaiUsdsConverter

    # Verify all pool IDs are distinct
    pool_ids = [r.pool_id for r in records]
    assert len(set(pool_ids)) == 20

    # Verify NO wrapper pool
    wrapper_addr = "0xa188eec8f81263234da3622a406892f3d630f98c"
    assert not any(wrapper_addr in r.pool.lower() for r in records)
    assert not any(r.config.get("model") == "UsdsPsmWrapper" for r in records)

    # Verify converter is DaiUsdsConverter
    assert maker_records[0].config.get("model") == "DaiUsdsConverter"
    assert maker_records[0].pool.lower() == "0x3225737a9bbb6473cb4a45b7244aca2befdb276a"

    # All must be supported
    assert all(r.status == SupportStatus.SUPPORTED for r in records)


def test_seed_pinned_evidence_and_supplement_context(tmp_path: Path):
    seeded = seed_pinned_evidence(tmp_path)
    assert seeded == 3

    pin = 23549939
    pin_hash = "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12"
    block = BlockRef(1, pin, pin_hash, 1760130851)

    assert is_block_collected(tmp_path, block)

    # Check supplement compatibility
    ctx, ann, _ = prepared_collection_context(pin)
    assert len(ctx.states) == 77

    ctx_supp, ann_supp = supplement_contexts(ctx, ann, [tmp_path])
    # 77 baseline + 20 expansion = 97 states
    assert len(ctx_supp.states) == 97
    assert len(ann_supp.get("supplement_artifacts", [])) == 1

    # Verify USDS -> USDC routing works through DaiUsdsConverter + existing LitePSM
    usds_token = Token(1, "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", 18)
    usdc_token = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
    req = TradeRequest(usds_token, usdc_token, 100_000 * 10**18)

    solver = BaselineSolver()
    ev = Evaluator()
    plans = solver.solve(req, ctx_supp.states)
    assert len(plans) > 0

    evaluation = ev.evaluate(plans[0], ctx_supp.states)
    assert evaluation.feasible
    assert evaluation.amount_out == 100_000 * 10**6


def test_collected_dataset_completeness_and_manifest():
    """Verify all 254 blocks are collected in outputs/october-expansion."""
    out = ROOT / "outputs/october-expansion"
    manifest_path = out / "collection_manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "complete"
    assert manifest["total_blocks"] == 254
    assert manifest["blocks_collected"] == 254
    assert manifest["pools_per_block"] == 20
    assert len(manifest["missing_blocks"]) == 0

    blocks = load_october_blocks()
    assert len(blocks) == 254

    # Check that records and snapshots exist for every block
    for b in blocks:
        rec_path = out / "records" / f"{b.hash}.json"
        calls_path = out / "snapshots/1" / b.hash / "calls.json.gz"
        assert rec_path.is_file(), f"Missing record for block {b.number} ({b.hash})"
        assert calls_path.is_file(), f"Missing snapshot for block {b.number} ({b.hash})"


def test_supplement_context_on_collected_dataset_across_pins():
    """Verify supplement_contexts loads the collected dataset cleanly across qualification pins."""
    out = ROOT / "outputs/october-expansion"

    for pin in (23549939, 23550094, 23550192):
        ctx, ann, _ = prepared_collection_context(pin)
        baseline_states = len(ctx.states)
        ctx_supp, ann_supp = supplement_contexts(ctx, ann, [out])

        # Exactly 20 expansion states added
        assert len(ctx_supp.states) == baseline_states + 20
        assert len(ann_supp["supplement_artifacts"]) == 1

        # Evaluate 1 WETH -> USDC trade
        weth = Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18)
        usdc = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
        req = TradeRequest(weth, usdc, 10**18)

        solver = BaselineSolver()
        ev = Evaluator()
        plans = solver.solve(req, ctx_supp.states)
        assert len(plans) > 0

        evaluation = ev.evaluate(plans[0], ctx_supp.states)
        assert evaluation.feasible
        assert evaluation.amount_out > 0


def test_corrupted_checkpoint_refusal(tmp_path: Path):
    """Verify is_block_collected strictly rejects corrupted or forged checkpoints."""
    seed_pinned_evidence(tmp_path)

    pin = 23549939
    pin_hash = "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12"
    block = BlockRef(1, pin, pin_hash, 1760130851)
    assert is_block_collected(tmp_path, block)

    rec_file = tmp_path / "records" / f"{block.hash}.json"
    header_file = tmp_path / "snapshots/1" / block.hash / "header.json"
    calls_file = tmp_path / "snapshots/1" / block.hash / "calls.json.gz"

    original_rec_text = rec_file.read_text()
    original_header_text = header_file.read_text()
    original_calls_bytes = calls_file.read_bytes()

    # 1. Corrupted block timestamp in records
    doc = json.loads(original_rec_text)
    doc["timestamp"] = 9999999999
    rec_file.write_text(json.dumps(doc))
    assert not is_block_collected(tmp_path, block)
    rec_file.write_text(original_rec_text)

    # 2. Corrupted block number in records
    doc = json.loads(original_rec_text)
    doc["blockNumber"] = 12345
    rec_file.write_text(json.dumps(doc))
    assert not is_block_collected(tmp_path, block)
    rec_file.write_text(original_rec_text)

    # 3. Corrupted pool IDs (20 records, but contains duplicate pool ID)
    doc = json.loads(original_rec_text)
    doc["records"][19] = dict(doc["records"][0])
    rec_file.write_text(json.dumps(doc))
    assert not is_block_collected(tmp_path, block)
    rec_file.write_text(original_rec_text)

    # 4. Corrupted pool IDs (20 unique records, but unexpected pool ID)
    doc = json.loads(original_rec_text)
    fake_rec = dict(doc["records"][0])
    fake_rec["pool_id"] = "uniswap_v3:0x0000000000000000000000000000000000000000:0x0000000000000000000000000000000000000001"
    fake_rec["pool"] = "0x0000000000000000000000000000000000000001"
    doc["records"][0] = fake_rec
    rec_file.write_text(json.dumps(doc))
    assert not is_block_collected(tmp_path, block)
    rec_file.write_text(original_rec_text)

    # 5. Corrupted snapshot header timestamp
    header_doc = json.loads(original_header_text)
    header_doc["timestamp"] = 8888888888
    header_file.write_text(json.dumps(header_doc))
    assert not is_block_collected(tmp_path, block)
    header_file.write_text(original_header_text)

    # 6. Corrupted gzip calls file
    calls_file.write_bytes(b"not a valid gzip stream")
    assert not is_block_collected(tmp_path, block)
    calls_file.write_bytes(original_calls_bytes)

    # Restored checkpoint is valid again
    assert is_block_collected(tmp_path, block)


