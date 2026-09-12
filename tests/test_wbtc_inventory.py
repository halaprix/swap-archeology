"""Tests for historical WBTC pool inventory overlay and activation metadata."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wbtc_inventory import (
    FACTORY,
    TOKENS,
    build_wbtc_pool_records,
    export_activation_evidence,
    export_inventory_overlay,
    get_wbtc_tokens,
)

from swaparch.core.types import SupportStatus, norm_address
from swaparch.universe import activation_reason, pool_record_from_json


def test_canonical_tokens():
    """Verify canonical token addresses, symbols, and decimals."""
    tokens = get_wbtc_tokens()
    assert set(tokens.keys()) == {"WBTC", "WETH", "USDC", "USDT"}

    assert tokens["WBTC"].address == "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
    assert tokens["WBTC"].decimals == 8
    assert tokens["WBTC"].symbol == "WBTC"
    assert tokens["WBTC"].chain == 1

    assert tokens["WETH"].address == "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    assert tokens["WETH"].decimals == 18

    assert tokens["USDC"].address == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert tokens["USDC"].decimals == 6

    assert tokens["USDT"].address == "0xdac17f958d2ee523a2206206994597c13d831ec7"
    assert tokens["USDT"].decimals == 6


def test_wbtc_pool_records_structure():
    """Verify exactly 12 pools are built with strict Uniswap V3 ordering and config."""
    records = build_wbtc_pool_records()
    assert len(records) == 12

    pairs_count = {}
    fee_spacing_map = {100: 1, 500: 10, 3000: 60, 10000: 200}
    wbtc_addr = TOKENS["WBTC"].address

    for rec in records:
        assert rec.family == "uniswap_v3"
        assert rec.chain == 1
        assert rec.deployment == FACTORY
        assert rec.status == SupportStatus.SUPPORTED

        # WBTC must strictly be token0 in all 12 pools by address ordering
        t0, t1 = rec.tokens
        assert t0.address == wbtc_addr, f"Expected WBTC as token0, got {t0.symbol}"
        assert int(t0.address, 16) < int(t1.address, 16), (
            f"Token ordering violated: {t0.address} not < {t1.address}"
        )
        assert t1.symbol in {"WETH", "USDC", "USDT"}

        # Check fee and tick spacing alignment
        fee = rec.config["fee"]
        assert fee in fee_spacing_map
        assert rec.config["tick_spacing"] == fee_spacing_map[fee]

        # Check pool ID convention
        expected_id = f"uniswap_v3:{FACTORY}:{norm_address(rec.pool)}"
        assert rec.pool_id == expected_id

        # Check creation block precedes historical October window (start: 23549939)
        assert rec.created_block is not None
        assert rec.created_block <= 20_629_019
        assert rec.created_block < 23_549_939

        pair_key = (t1.symbol, fee)
        pairs_count[pair_key] = pairs_count.get(pair_key, 0) + 1

    # Exactly 4 fee tiers for each of the 3 pairs
    assert len(pairs_count) == 12
    for other in ("WETH", "USDC", "USDT"):
        for fee in (100, 500, 3000, 10000):
            assert pairs_count.get((other, fee)) == 1


def test_activation_reason_established():
    """Verify activation_reason returns None at all October window blocks."""
    records = build_wbtc_pool_records()
    for rec in records:
        # At start pin 23549939
        assert activation_reason(rec, 23_549_939) is None
        # At stress pin 23550094
        assert activation_reason(rec, 23_550_094) is None
        # At end pin 23550192
        assert activation_reason(rec, 23_550_192) is None


def test_inventory_overlay_export(tmp_path):
    """Verify JSON export and round-trip parsing of the inventory overlay."""
    out_file = export_inventory_overlay(output_dir=tmp_path)
    assert out_file.is_file()

    data = json.loads(out_file.read_text())
    assert data["chain"] == 1
    assert data["family"] == "uniswap_v3"
    assert data["overlay"] == "wbtc"
    assert len(data["pools"]) == 12

    # Round trip through pool_record_from_json
    for p_json in data["pools"]:
        rec = pool_record_from_json(p_json)
        assert rec.family == "uniswap_v3"
        assert rec.tokens[0].symbol == "WBTC"
        assert rec.tokens[1].symbol in {"WETH", "USDC", "USDT"}


def test_activation_evidence_export(tmp_path):
    """Verify activation evidence JSON export."""
    act_file = export_activation_evidence(output_dir=tmp_path)
    assert act_file.is_file()

    evidence = json.loads(act_file.read_text())
    assert evidence["factory"] == FACTORY
    assert evidence["start_pin"]["number"] == 23549939
    assert len(evidence["pools"]) == 12
    assert all(p["historical_lead_match"] for p in evidence["pools"])
    assert all(p["liquidity_at_start_pin"] is not None for p in evidence["pools"])
