"""Targeted tests for five crash liquidity collection, provenance, and policy."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def test_window_bounds_alignment():
    """Verify window block ranges and canonical block counts match published index."""
    from five_crash_liquidity import CRASH_SPECS

    frontend_index_path = PROJECT_ROOT / "frontend/public/five-crash-liquidity/index.json"
    assert frontend_index_path.is_file(), "frontend index.json must exist"
    frontend_catalog = json.loads(frontend_index_path.read_text(encoding="utf-8"))

    catalog_by_id = {c["id"]: c for c in frontend_catalog["crashes"]}
    assert len(CRASH_SPECS) == 5
    assert len(catalog_by_id) == 5

    expected_counts = {
        "crash-1": 299,
        "crash-2": 300,
        "crash-3": 301,
        "crash-4": 301,
        "crash-5": 299,
    }

    for spec in CRASH_SPECS:
        cid = spec["id"]
        assert cid in catalog_by_id
        fe = catalog_by_id[cid]
        assert spec["crashUtc"] == fe["crashUtc"]
        assert spec["startUtc"] == fe["startUtc"]
        assert spec["endUtc"] == fe["endUtc"]
        assert spec["blockCount"] == expected_counts[cid]
        assert spec["endBlock"] - spec["startBlock"] + 1 == expected_counts[cid]


def test_no_invented_provenance_and_saved_evidence():
    """Verify that all activation bounds have saved on-chain code evidence and no false qualification."""

    # 1. Aave reference proof
    aave_proof = (
        PROJECT_ROOT
        / "data/discovery-evidence/activation/1/0x5e2b0ad801f51565cad7f363cf103be37b3b36b39deb2cae26cf6b12ea23f424/0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2.json"
    )
    assert aave_proof.is_file(), "Aave pool code evidence must be persisted at block 21762695"
    aave_ev = json.loads(aave_proof.read_text(encoding="utf-8"))
    assert aave_ev["code_length"] == 4802
    assert aave_ev["code"].startswith("0x60806040")

    # 2. DaiUsdsConverter proof
    converter_proof = (
        PROJECT_ROOT
        / "data/discovery-evidence/activation/1/0x5e2b0ad801f51565cad7f363cf103be37b3b36b39deb2cae26cf6b12ea23f424/0x3225737a9bbb6473cb4a45b7244aca2befdb276a.json"
    )
    assert converter_proof.is_file(), "DaiUsdsConverter code evidence must be persisted at block 21762695"
    conv_ev = json.loads(converter_proof.read_text(encoding="utf-8"))
    assert conv_ev["code_length"] == 2858

    # 3. Supplements in representative check must have empty validated_block_hashes
    supp_records_dir = PROJECT_ROOT / "outputs/five-crash-liquidity/supplements/records"
    assert supp_records_dir.is_dir()
    for rec_file in supp_records_dir.glob("*.json"):
        doc = json.loads(rec_file.read_text(encoding="utf-8"))
        for r in doc["records"]:
            # Collection-only supplement records must NOT invent quote qualification
            assert r["config"].get("validated_block_hashes") == [], (
                f"Supplement record {r['pool_id']} must have empty validated_block_hashes"
            )


def test_representative_check_outputs_and_policy():
    """Verify that representative-check.json contains all 5 dates with full 6 cases and raw evidence."""
    rep_path = PROJECT_ROOT / "outputs/five-crash-liquidity/representative-check.json"
    assert rep_path.is_file(), "representative-check.json must exist"
    data = json.loads(rep_path.read_text(encoding="utf-8"))

    assert data["schemaVersion"] == 1
    assert data["representativeBlocksCount"] == 5

    for res in data["results"]:
        cid = res["id"]
        s_row = res["sourcesRow"]
        o_row = res["oracleRow"]

        # 6 cases present and positive
        for asset in ("ETH", "WETH"):
            for sz in ("1", "10", "100"):
                price = s_row["aggregates"][asset][sz]
                assert price is not None and price > 0, f"Missing price for {cid} {asset}:{sz}"

        # Oracles present and positive
        assert s_row["chainlink"] is not None and s_row["chainlink"] > 0
        assert s_row["aave"] is not None and s_row["aave"] > 0
        assert o_row["values"]["oneinch_spot"]["status"] == "ok"
        assert o_row["values"]["uniswap_v3_twap_60"]["status"] == "ok"
        assert o_row["values"]["uniswap_v3_twap_300"]["status"] == "ok"

        # Raw evidence persisted
        block_hash = s_row["blockHash"].lower()
        raw_ev = PROJECT_ROOT / f"outputs/five-crash-liquidity/{cid}/raw-evidence/{block_hash}.json"
        assert raw_ev.is_file(), f"Raw oracle evidence missing for {cid}"
        calls = json.loads(raw_ev.read_text(encoding="utf-8"))["calls"]
        assert "1inch_getRate" in calls
        assert "univ3_observe_60" in calls
        assert "univ3_observe_300" in calls
        assert "chainlink_eth_usd_round" in calls
        assert "chainlink_usdc_usd_round" in calls
