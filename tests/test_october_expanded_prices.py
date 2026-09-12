"""Tests for bounded October expanded source prices, candidate floors, and fail-closed validation."""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import october_source_prices

from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_context

CONNECTORS_DIR = ROOT / "outputs/october-connectors"
PANCAKE_DIR = ROOT / "outputs/source-expansion/pancake"


def test_validate_supplement_coverage_fail_closed(tmp_path):
    """Missing supplement directories, records, or snapshots must fail immediately."""
    fake_market = {23549939: {"blockHash": "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c"}}

    # Missing directory
    missing_dir = tmp_path / "nonexistent"
    with pytest.raises(FileNotFoundError, match="Mandatory supplement directory does not exist"):
        october_source_prices.validate_supplement_coverage([23549939], [missing_dir], fake_market)

    # Directory exists but missing records/
    supp_dir = tmp_path / "supp"
    supp_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="Fail-closed: missing supplement record"):
        october_source_prices.validate_supplement_coverage([23549939], [supp_dir], fake_market)

    # Record exists but missing snapshots/
    records_dir = supp_dir / "records"
    records_dir.mkdir()
    (records_dir / "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="missing supplement snapshot dir"):
        october_source_prices.validate_supplement_coverage([23549939], [supp_dir], fake_market)

    # Snapshot dir exists but missing header.json
    snap_dir = supp_dir / "snapshots" / "1" / "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c"
    snap_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="missing snapshot header"):
        october_source_prices.validate_supplement_coverage([23549939], [supp_dir], fake_market)

    # Header exists but missing calls.json.gz
    (snap_dir / "header.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="missing snapshot calls"):
        october_source_prices.validate_supplement_coverage([23549939], [supp_dir], fake_market)

    # Complete coverage passes
    (snap_dir / "calls.json.gz").write_bytes(b"")
    october_source_prices.validate_supplement_coverage([23549939], [supp_dir], fake_market)


def test_candidate_floor_re_evaluation_preserves_output_and_refund():
    """Previous winning plans with PSM refund evaluate to identical amount and refund."""
    ctx, ann, _ = prepared_collection_context(23550094)
    ctx_supp, ann_supp = supplement_context(ctx, ann, CONNECTORS_DIR)

    prev_agg_file = ROOT / "outputs/october-sources-connectors/aggregate-raw/0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d.json.gz"
    with gzip.open(prev_agg_file, "rt") as f:
        prev_data = json.load(f)

    prev_split = prev_data["reports"]["WETH:1"]["best_split"]
    re_eval = october_source_prices._re_evaluate_candidate(ctx_supp, ann_supp, "WETH", 1, prev_split)

    assert re_eval is not None
    assert re_eval["feasible"] is True
    assert re_eval["amount_out"] == prev_split["amount_out"]
    assert re_eval["terminal_refund"] == prev_split["terminal_refund"]


def test_pancake_native_vs_python_parity():
    """PancakeSwap V3 1 WETH input to USDC/USDT/WBTC yields positive quotes and preserves PancakeV3State."""
    code = """
import sys
from pathlib import Path
ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT / "scripts"))

from swaparch.adapters.pancake_v3 import PancakeV3State
from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_context
import perf_native

ctx, ann, _ = prepared_collection_context(23549939)
PANCAKE_DIR = ROOT / "outputs/source-expansion/pancake"
ctx_supp, _ = supplement_context(ctx, ann, PANCAKE_DIR)
pancake_states = [s for s in ctx_supp.states if isinstance(s, PancakeV3State)]
assert len(pancake_states) == 3

weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
targets = [
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
]

# 1. Pure Python execution
py_results = []
for s in pancake_states:
    tok_addrs = {t.address.lower() for t in s.tokens()}
    out_addr = next(addr for addr in targets if addr in tok_addrs)
    q = s.quote_exact_in(weth, out_addr, 10**18)
    amt_out, next_state = s.swap(weth, out_addr, 10**18)
    assert q > 0 and amt_out > 0
    assert q == amt_out
    assert isinstance(next_state, PancakeV3State)
    assert next_state.record.family == "pancake_v3"
    py_results.append((s.record.pool_id, q, amt_out))

# 2. Native Cython execution
build = perf_native.load_build(perf_native.DEFAULT_BUILD)
nat_results = []
with perf_native.optimized(ctx_supp, build):
    for s in pancake_states:
        tok_addrs = {t.address.lower() for t in s.tokens()}
        out_addr = next(addr for addr in targets if addr in tok_addrs)
        q = s.quote_exact_in(weth, out_addr, 10**18)
        amt_out, next_state = s.swap(weth, out_addr, 10**18)
        assert q > 0 and amt_out > 0
        assert q == amt_out
        assert isinstance(next_state, PancakeV3State)
        assert next_state.record.family == "pancake_v3"
        nat_results.append((s.record.pool_id, q, amt_out))

assert py_results == nat_results
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Pancake parity check failed:\n{res.stderr}"


def test_complete_routed_quote_parity():
    """Complete multi-hop routed quote parity between Native and pure Python on Block 23549939."""
    code = """
import sys
from pathlib import Path
ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT / "scripts"))

from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_context
import crash_slices
import perf_native

ctx, ann, _ = prepared_collection_context(23549939)
overlays = [
    Path("outputs/october-connectors"),
    Path("outputs/source-expansion/wbtc"),
    Path("outputs/source-expansion/usds"),
    Path("outputs/source-expansion/pancake"),
    Path("outputs/source-expansion/usds-amm"),
]
for o in overlays:
    ctx, ann = supplement_context(ctx, ann, o)

build = perf_native.load_build(perf_native.DEFAULT_BUILD)

with perf_native.optimized(ctx, build):
    code_nat, rep_nat = crash_slices._run_quote(ctx, ann, ("WETH", "USDC", "1"), allow_psm_dai_refund=True)

code_py, rep_py = crash_slices._run_quote(ctx, ann, ("WETH", "USDC", "1"), allow_psm_dai_refund=True)

assert code_nat == 0 and code_py == 0
split_nat = rep_nat["best_split"]
split_py = rep_py["best_split"]

assert split_nat["amount_out"] == split_py["amount_out"] == 3787418875
assert split_nat["amount_in_spent"] == split_py["amount_in_spent"] == 10**18
assert split_nat["residual_in"] == split_py["residual_in"] == 0
assert split_nat["steps"] == split_py["steps"]
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Routed quote parity failed:\n{res.stderr}"


def test_publish_atomic_replace_helper(tmp_path):
    """Atomic frontend export helper creates and atomically replaces destination files."""
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="Cannot publish: canonical prices.json or prices.csv missing"):
        october_source_prices._publish_frontend_atomically(out_dir, public_dir)

    (out_dir / "prices.json").write_text('{"status": "complete"}')
    (out_dir / "prices.csv").write_text("block,price\n1,100")
    october_source_prices._publish_frontend_atomically(out_dir, public_dir)

    assert (public_dir / "october-sources.json").read_text() == '{"status": "complete"}'
    assert (public_dir / "october-sources.csv").read_text() == "block,price\n1,100"


def test_check_enforces_exact_254_blocks_and_structure():
    """_check must reject partial or malformed payload structures."""
    # Fewer than 254 blocks raises AssertionError
    payload_incomplete = {
        "pools": [{"id": "p1", "family": "f1"}],
        "rows": [{"block": 23549939}],
    }
    with pytest.raises(AssertionError):
        october_source_prices._check(payload_incomplete)
