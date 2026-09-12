"""Focused integration tests for source expansion (WBTC, USDS, PancakeV3).

Tests:
1. Adapter registry in universe.py (PancakeV3, MakerSkyPsm delegating adapter).
2. MakerSkyPsmAdapter delegating between LitePSM and DaiUsdsConverter.
3. Multi-overlay chained loading via collection_supplement.py.
4. Preserving candidate floor (amount_out(combined) >= amount_out(baseline)).
5. USDS -> DAI -> USDC routing via DaiUsdsConverter + existing LitePSM state.
6. Shared capacity correctness (no duplicate reserve modeling).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from swaparch.adapters.pancake_v3 import PancakeV3Adapter
from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_contexts
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver
from swaparch.universe import implemented_adapters, pool_record_from_json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

WBTC_ROOT = ROOT / "outputs/source-expansion/wbtc"
USDS_ROOT = ROOT / "outputs/source-expansion/usds"
USDS_OVERLAY = USDS_ROOT / "usds_inventory_overlay.json"
USDS_EVIDENCE = USDS_ROOT / "pinned_evidence.json"
CURVE_ROOT = ROOT / "outputs/october-connectors"
PANCAKE_ROOT = ROOT / "outputs/source-expansion/pancake"
USDS_AMM_ROOT = ROOT / "outputs/source-expansion/usds-amm"

WETH = Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18)
USDC = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
USDS = Token(1, "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", 18)
WBTC = Token(1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8)


def test_implemented_adapters_includes_pancake_and_maker_sky():
    adapters = implemented_adapters()
    assert "pancake_v3" in adapters
    assert isinstance(adapters["pancake_v3"], PancakeV3Adapter)
    assert "maker_sky_psm" in adapters


def test_maker_sky_psm_delegation():
    adapters = implemented_adapters()
    maker_adapter = adapters["maker_sky_psm"]

    # Test LitePSM record
    lite_record = PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id="maker_sky_psm:0xf6e72db5454dd049d0788e411b06cfaf16853042:0xf6e72db5454dd049d0788e411b06cfaf16853042",
        deployment="0xf6e72db5454dd049d0788e411b06cfaf16853042",
        pool="0xf6e72db5454dd049d0788e411b06cfaf16853042",
        tokens=(
            Token(1, "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18),
            Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6),
        ),
        config={"model": "dss-lite-psm"},
        created_block=None,
        discovered_by={"deployed_by_block": 23549939},
        status=SupportStatus.SUPPORTED,
    )

    # Test DaiUsdsConverter record
    overlay_data = json.loads(USDS_OVERLAY.read_text())
    conv_raw = next(r for r in overlay_data["pools"] if r["config"].get("model") == "DaiUsdsConverter")
    conv_record = pool_record_from_json(conv_raw)

    # Test wrapper record should be rejected
    wrap_raw = next(r for r in overlay_data["pools"] if r["config"].get("model") == "UsdsPsmWrapper")
    wrap_record = pool_record_from_json(wrap_raw)

    # Validate read requests generated
    from swaparch.core.types import BlockRef
    block = BlockRef(1, 23549939, "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12", 1760130851)

    lite_specs = maker_adapter.read_requests(lite_record, block)
    assert len(lite_specs) == 6  # LitePSM static specs

    conv_specs = maker_adapter.read_requests(conv_record, block)
    assert len(conv_specs) == 4  # DaiUsdsConverter static specs

    with pytest.raises(Unsupported, match="not supported"):
        maker_adapter.read_requests(wrap_record, block)


def test_supplement_contexts_chaining():
    ctx, ann, client = prepared_collection_context(23549939)
    assert client.network_requests == 0
    baseline_states = len(ctx.states)

    roots = [CURVE_ROOT, WBTC_ROOT]
    ctx_chained, ann_chained = supplement_contexts(ctx, ann, roots)

    # Curve added 1, WBTC added 12
    assert len(ctx_chained.states) == baseline_states + 1 + 12
    assert WBTC.address in ctx_chained.tokens
    assert ann_chained["collection_evidence_identity"] != ann["collection_evidence_identity"]
    assert len(ann_chained.get("supplement_artifacts", [])) == 2


def test_candidate_floor_preservation_at_stress_pin():
    """At crash pin 23550094, adding WBTC must not regress the best WETH->USDC quote."""
    ctx, ann, _ = prepared_collection_context(23550094)
    ctx_wbtc, _ann_wbtc = supplement_contexts(ctx, ann, [WBTC_ROOT])

    ev = Evaluator()
    solver = BaselineSolver()

    for size in (1, 10, 100):
        req = TradeRequest(WETH, USDC, size * 10**18)
        plans_before = solver.solve(req, ctx.states)
        eval_before = ev.evaluate(plans_before[0], ctx.states)
        assert eval_before.feasible

        plans_after = solver.solve(req, ctx_wbtc.states)
        eval_after = ev.evaluate(plans_after[0], ctx_wbtc.states)
        assert eval_after.feasible

        # Output must be greater than or equal to baseline floor
        assert eval_after.amount_out >= eval_before.amount_out


def test_usds_dai_usdc_exact_routing():
    """USDS routes to USDC via DaiUsdsConverter -> LitePSM without reserve duplication."""
    usds_root = ROOT / "outputs/source-expansion/usds"
    ctx, ann, _ = prepared_collection_context(23549939)
    ctx_usds, _ = supplement_contexts(ctx, ann, [usds_root])

    ev = Evaluator()
    solver = BaselineSolver()

    req = TradeRequest(USDS, USDC, 100_000 * 10**18)
    plans = solver.solve(req, ctx_usds.states)
    assert len(plans) > 0

    best = plans[0]
    evaluation = ev.evaluate(best, ctx_usds.states)

    assert evaluation.feasible
    assert evaluation.residual_in == 0
    assert evaluation.amount_in_spent == 100_000 * 10**18
    # 100,000 USDS -> 100,000 DAI -> 100,000 USDC (100_000 * 10^6 raw units)
    assert evaluation.amount_out == 100_000 * 10**6

    # Verify steps used
    step_pools = [s.pool_id for s in best.steps]
    assert any("0x3225737a9bbb6473cb4a45b7244aca2befdb276a" in p for p in step_pools)
    assert any("0xf6e72db5454dd049d0788e411b06cfaf16853042" in p for p in step_pools)


def test_combined_four_overlay_universe():
    """All 4 overlays (Curve, WBTC, USDS, Pancake) load and solve together."""
    curve = ROOT / "outputs/october-connectors"
    wbtc = ROOT / "outputs/source-expansion/wbtc"
    usds = ROOT / "outputs/source-expansion/usds"
    pancake = ROOT / "outputs/source-expansion/pancake"

    ctx, ann, _ = prepared_collection_context(23549939)
    ctx_comb, ann_comb = supplement_contexts(ctx, ann, [curve, wbtc, usds, pancake])

    # 77 baseline + 1 Curve + 12 WBTC + 1 USDS + 3 Pancake = 94 states
    assert len(ctx_comb.states) == 94
    assert len(ann_comb["supplement_artifacts"]) == 4

    ev = Evaluator()
    solver = BaselineSolver()
    req = TradeRequest(WETH, USDC, 10**18)
    plans = solver.solve(req, ctx_comb.states)

    assert len(plans) > 0
    evaluation = ev.evaluate(plans[0], ctx_comb.states)
    assert evaluation.feasible
    assert evaluation.residual_in == 0
    assert evaluation.amount_out > 3_700 * 10**6


def test_combined_five_overlay_universe():
    """All 5 overlays (Curve, WBTC, USDS, Pancake, USDS-AMM) load and solve together."""
    from scripts.source_expansion_pipeline import (
        check_offline_data_availability,
        solve_best_candidate,
    )

    roots = [CURVE_ROOT, WBTC_ROOT, USDS_ROOT, PANCAKE_ROOT, USDS_AMM_ROOT]
    check_offline_data_availability(23549939, roots)

    ctx, ann, _ = prepared_collection_context(23549939)
    ctx_comb, ann_comb = supplement_contexts(ctx, ann, roots)

    # 77 baseline + 1 Curve + 12 WBTC + 1 USDS + 3 Pancake + 4 USDS-AMM = 98 states
    assert len(ctx_comb.states) == 98
    assert len(ann_comb["supplement_artifacts"]) == 5

    ev = Evaluator()
    req = TradeRequest(WETH, USDC, 10**18)
    best_plan, evaluation = solve_best_candidate(req, ctx_comb.states, ev)

    assert best_plan is not None
    assert evaluation.feasible
    assert evaluation.residual_in == 0
    assert evaluation.amount_in_spent == 10**18
    assert evaluation.amount_out > 3_700 * 10**6


def test_shared_psm_single_instance_no_wrapper():
    """Verify single instance of LitePSM and zero UsdsPsmWrapper instances."""
    ctx, ann, _ = prepared_collection_context(23549939)
    ctx_comb, _ = supplement_contexts(
        ctx, ann, [CURVE_ROOT, WBTC_ROOT, USDS_ROOT, PANCAKE_ROOT, USDS_AMM_ROOT]
    )

    psm_states = [s for s in ctx_comb.states if s.record.family == "maker_sky_psm"]
    litepsm_states = [
        s for s in psm_states if s.record.config.get("model") == "dss-lite-psm"
    ]
    conv_states = [
        s for s in psm_states if s.record.config.get("model") == "DaiUsdsConverter"
    ]
    wrap_states = [
        s for s in psm_states if s.record.config.get("model") == "UsdsPsmWrapper"
    ]

    assert len(litepsm_states) == 1
    assert len(conv_states) == 1
    assert len(wrap_states) == 0


def test_offline_refusal_on_missing_data():
    """Verify mandatory --offline refusal when cached data is absent."""
    from scripts.source_expansion_pipeline import check_offline_data_availability

    fake_root = ROOT / "outputs/non_existent_overlay"
    with pytest.raises(FileNotFoundError, match="Offline refusal"):
        check_offline_data_availability(23549939, [fake_root])


