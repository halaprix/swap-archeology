"""Focused unit and regression tests for USDS Uniswap V3 connector pools.

Validates:
1. Offline loading of supplemental snapshots and records across the 3 historical pins
   (23549939, 23550094, 23550192).
2. Exact on-chain token ordering, fees, and tick spacings:
   - USDS/USDC 100: token0=USDC (6), token1=USDS (18), fee=100, spacing=1
   - USDS/USDC 500: token0=USDC (6), token1=USDS (18), fee=500, spacing=10
   - USDS/USDC 3000: token0=USDC (6), token1=USDS (18), fee=3000, spacing=60
   - USDS/DAI 3000: token0=DAI (18), token1=USDS (18), fee=3000, spacing=60
3. Parsing of all produced records through pool_record_from_json without schema error.
4. Wei-exact agreement with independent on-chain QuoterV2 on 1 USDS and 100,000 USDS.
5. Fail-closed bounded tick coverage: thin pools (fee 100 and 3000) reject 100,000 USDS
   with Unsupported rather than fabricating partial fills.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, norm_address
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import pool_record_from_json

ROOT = Path(__file__).resolve().parents[1]
AMM_OUT = ROOT / "outputs/source-expansion/usds-amm"
PIN_HASHES = {
    23549939: "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
    23550094: "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d",
    23550192: "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c",
}

USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"


def test_supplement_records_and_snapshots_exist():
    assert AMM_OUT.exists(), f"missing directory {AMM_OUT}"
    records_dir = AMM_OUT / "records"
    snapshots_dir = AMM_OUT / "snapshots/1"
    assert records_dir.exists()
    assert snapshots_dir.exists()

    for block_num, block_hash in PIN_HASHES.items():
        rec_file = records_dir / f"{block_hash}.json"
        assert rec_file.exists(), f"missing record file for block {block_num}"
        snap_dir = snapshots_dir / block_hash
        assert snap_dir.exists(), f"missing snapshot dir for block {block_num}"


def test_records_schema_and_parsing():
    records_dir = AMM_OUT / "records"
    for block_hash in PIN_HASHES.values():
        rec_file = records_dir / f"{block_hash}.json"
        data = json.loads(rec_file.read_text())
        assert data["blockHash"] == block_hash
        assert len(data["records"]) == 4

        for row in data["records"]:
            record = pool_record_from_json(row)
            assert isinstance(record, PoolRecord)
            assert record.status == SupportStatus.SUPPORTED
            assert record.family == "uniswap_v3"
            assert len(record.tokens) == 2


def test_pool_metadata_and_token_ordering():
    rec_file = AMM_OUT / "records" / f"{PIN_HASHES[23549939]}.json"
    data = json.loads(rec_file.read_text())
    records = [pool_record_from_json(r) for r in data["records"]]

    pool_map = {norm_address(r.pool): r for r in records}
    assert len(pool_map) == 4

    # 1. USDS / USDC 100
    p100 = pool_map[norm_address("0x4eb5db0134fac94e66da89764d58a9f709d53a8f")]
    assert p100.config["fee"] == 100
    assert p100.config["tick_spacing"] == 1
    assert p100.tokens[0].symbol == "USDC" and p100.tokens[0].decimals == 6
    assert p100.tokens[1].symbol == "USDS" and p100.tokens[1].decimals == 18

    # 2. USDS / USDC 500
    p500 = pool_map[norm_address("0x8aee53b873176d9f938d24a53a8ae5cf36276464")]
    assert p500.config["fee"] == 500
    assert p500.config["tick_spacing"] == 10
    assert p500.tokens[0].symbol == "USDC" and p500.tokens[0].decimals == 6
    assert p500.tokens[1].symbol == "USDS" and p500.tokens[1].decimals == 18

    # 3. USDS / USDC 3000
    p3000 = pool_map[norm_address("0xa66a2770bc0e0c65b63b5a3bb4560e90f95d6146")]
    assert p3000.config["fee"] == 3000
    assert p3000.config["tick_spacing"] == 60
    assert p3000.tokens[0].symbol == "USDC" and p3000.tokens[0].decimals == 6
    assert p3000.tokens[1].symbol == "USDS" and p3000.tokens[1].decimals == 18

    # 4. USDS / DAI 3000
    pdai = pool_map[norm_address("0xe9f1e2ef814f5686c30ce6fb7103d0f780836c67")]
    assert pdai.config["fee"] == 3000
    assert pdai.config["tick_spacing"] == 60
    assert pdai.tokens[0].symbol == "DAI" and pdai.tokens[0].decimals == 18
    assert pdai.tokens[1].symbol == "USDS" and pdai.tokens[1].decimals == 18


def test_offline_pool_state_loading_across_pins():
    store = SnapshotStore(AMM_OUT / "snapshots")
    adapter = UniswapV3Adapter(word_radius=8)

    for block_hash in PIN_HASHES.values():
        snapshot = store.load(1, block_hash)
        rec_file = AMM_OUT / "records" / f"{block_hash}.json"
        data = json.loads(rec_file.read_text())
        records = [pool_record_from_json(r) for r in data["records"]]

        for rec in records:
            state = adapter.load_state(rec, snapshot)
            assert state.liquidity > 0
            assert len(state.tick_liquidity_net) > 0
            assert state.word_lo <= state.word_hi


def test_quoter_v2_comparison_offline():
    """Verify wei-for-wei QuoterV2 matching on 1 USDS and fail-closed bounds on 100,000 USDS."""
    quoter_file = AMM_OUT / "quoter_checks.json"
    assert quoter_file.exists()

    checks_by_pin = json.loads(quoter_file.read_text())
    store = SnapshotStore(AMM_OUT / "snapshots")
    adapter = UniswapV3Adapter(word_radius=8)

    for block_str, checks in checks_by_pin.items():
        block_num = int(block_str)
        block_hash = PIN_HASHES[block_num]
        snapshot = store.load(1, block_hash)
        rec_file = AMM_OUT / "records" / f"{block_hash}.json"
        data = json.loads(rec_file.read_text())
        records = {norm_address(r["pool"]): pool_record_from_json(r) for r in data["records"]}

        for c in checks:
            rec = records[norm_address(c["pool"])]
            state = adapter.load_state(rec, snapshot)
            amt_in = int(c["amount_in"])
            other_token = rec.tokens[0].address

            if amt_in == 10**18:
                # 1 USDS swaps must succeed and match QuoterV2 exactly
                local_out, _ = state.swap(USDS, other_token, amt_in)
                assert str(local_out) == c["quoter_out"]
                assert c["matched"] is True
            elif amt_in == 100_000 * 10**18:
                # 100,000 USDS:
                # Deep pools (fee 500 and USDS/DAI 3000) match QuoterV2
                # Thin pools (fee 100 and 3000) fail closed with Unsupported
                if rec.config["fee"] == 500 or (rec.config["fee"] == 3000 and "DAI" in rec.notes):
                    local_out, _ = state.swap(USDS, other_token, amt_in)
                    assert str(local_out) == c["quoter_out"]
                    assert c["matched"] is True
                else:
                    with pytest.raises(Unsupported, match="insufficient tick coverage"):
                        state.swap(USDS, other_token, amt_in)
                    assert c["matched"] is False
                    assert "insufficient tick coverage" in c["fail_closed_reason"]
