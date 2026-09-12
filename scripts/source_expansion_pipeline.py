"""Combined source expansion evaluation pipeline for historical pins.

Integrates existing universe + Curve + WBTC overlay + qualified Pancake V3 and USDS pieces:
- Curve: Legacy 3pool (DAI/USDT/USDC)
- WBTC: 12 Uniswap V3 pools (6 active quote-qualified on selected routes)
- USDS: DaiUsdsConverter (1:1 DAI<->USDS edge into existing LitePSM state)
- USDS-AMM: 4 Uniswap V3 connector pools (USDC/USDS 100, 500, 3000 and DAI/USDS 3000)
- PancakeSwap V3: 3 qualified pools (WETH/USDC 500, WETH/USDT 500, WETH/WBTC 2500)

Rerunnable with --blocks (allowing 3-pin subsets) and mandatory --offline missing data refusal.
Does not overwrite published 254-block dataset or user-facing chart.
Outputs:
- outputs/source-expansion/integration/before_after_comparison.json
- outputs/source-expansion/integration/coverage_summary.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic
from typing import Any

from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_contexts
from swaparch.core.types import Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver
from swaparch.solver.search import GeneralSearchSolver

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs/source-expansion/integration"

CURVE_ROOT = ROOT / "outputs/october-connectors"
WBTC_ROOT = ROOT / "outputs/source-expansion/wbtc"
USDS_ROOT = ROOT / "outputs/source-expansion/usds"
PANCAKE_ROOT = ROOT / "outputs/source-expansion/pancake"
USDS_AMM_ROOT = ROOT / "outputs/source-expansion/usds-amm"

PIN_BLOCKS = (23549939, 23550094, 23550192)

WETH = Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18)
USDC = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
USDT = Token(1, "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6)
DAI = Token(1, "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18)
USDS = Token(1, "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", 18)
WBTC = Token(1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8)

SIZES_WETH = (1, 10, 100)


def check_offline_data_availability(block_num: int, roots: list[Path]) -> None:
    """Refuse execution if any cached collection context, record, or snapshot is missing."""
    ctx, _, client = prepared_collection_context(block_num)
    if client.network_requests > 0:
        raise RuntimeError(
            f"Offline refusal: network request attempted for block {block_num}"
        )
    for root in roots:
        rec_file = root / "records" / f"{ctx.block.hash}.json"
        if not rec_file.is_file():
            raise FileNotFoundError(
                f"Offline refusal: missing record file {rec_file} for block {block_num}"
            )
        snap_dir = root / "snapshots" / str(ctx.block.chain) / ctx.block.hash
        if (
            not snap_dir.is_dir()
            or not (snap_dir / "header.json").is_file()
            or not (snap_dir / "calls.json.gz").is_file()
        ):
            raise FileNotFoundError(
                f"Offline refusal: missing snapshot data in {snap_dir} for block {block_num}"
            )


def solve_best_candidate(
    req: TradeRequest,
    states: tuple[Any, ...],
    ev: Evaluator,
    fallback_plan: Any | None = None,
) -> tuple[Any, Any]:
    """Solve candidate routes using bounded 3-hop GeneralSearchSolver plus BaselineSolver.

    Preserves original baseline winning plan as fallback, evaluates all candidates with the
    exact same Evaluator and refund policy, and ensures strict full-fill consumption.
    No global optimum claim is made.
    """
    b_solver = BaselineSolver()
    baseline_plans = b_solver.solve(req, states)
    baseline_winner = baseline_plans[0] if baseline_plans else None
    baseline_eval = ev.evaluate(baseline_winner, states) if baseline_winner else None

    search_solver = GeneralSearchSolver(
        max_steps=3,
        beam_width=32,
        max_expansions=1000,
        grid_parts=10,
        include_baseline=False,
    )
    search_plans = search_solver.solve(req, states)

    candidates = ([baseline_winner] if baseline_winner else []) + list(search_plans[:25])
    if fallback_plan is not None:
        candidates.append(fallback_plan)

    feasible_candidates = []
    for p in candidates:
        eval_res = ev.evaluate(p, states)
        if (
            eval_res.feasible
            and eval_res.amount_in_spent == req.amount_in
            and eval_res.residual_in == 0
        ):
            feasible_candidates.append((p, eval_res))

    if not feasible_candidates:
        if (
            baseline_eval
            and baseline_eval.feasible
            and baseline_eval.amount_in_spent == req.amount_in
            and baseline_eval.residual_in == 0
        ):
            return baseline_winner, baseline_eval
        if fallback_plan is not None:
            f_eval = ev.evaluate(fallback_plan, states)
            if (
                f_eval.feasible
                and f_eval.amount_in_spent == req.amount_in
                and f_eval.residual_in == 0
            ):
                return fallback_plan, f_eval
        return None, None

    best_plan, best_eval = max(feasible_candidates, key=lambda x: x[1].amount_out)
    if (
        baseline_eval
        and baseline_eval.feasible
        and baseline_eval.amount_in_spent == req.amount_in
        and baseline_eval.residual_in == 0
        and baseline_eval.amount_out > best_eval.amount_out
    ):
        return baseline_winner, baseline_eval

    return best_plan, best_eval


def evaluate_universe(
    block_num: int,
    use_wbtc: bool = True,
    use_usds: bool = True,
    use_pancake: bool = True,
    use_usds_amm: bool = True,
    offline: bool = True,
    fallback_plans: dict[str, Any] | None = None,
) -> dict[str, Any]:
    roots = [CURVE_ROOT]
    overlay_labels = ["curve_3pool"]
    if use_wbtc:
        roots.append(WBTC_ROOT)
        overlay_labels.append("wbtc_12pools")
    if use_usds:
        roots.append(USDS_ROOT)
        overlay_labels.append("usds_converter")
    if use_pancake:
        roots.append(PANCAKE_ROOT)
        overlay_labels.append("pancake_v3_qualified")
    if use_usds_amm:
        roots.append(USDS_AMM_ROOT)
        overlay_labels.append("usds_amm_4pools")

    if offline:
        check_offline_data_availability(block_num, roots)

    ctx, ann, client = prepared_collection_context(block_num)
    if offline and client.network_requests > 0:
        raise RuntimeError(f"Offline refusal: network calls made on block {block_num}")

    ctx_supp, _ann_supp = supplement_contexts(ctx, ann, roots)
    ev = Evaluator()

    # Shared PSM check: exactly 1 LitePSM instance, 0 wrapper instances
    psm_states = [s for s in ctx_supp.states if s.record.family == "maker_sky_psm"]
    litepsm_states = [
        s for s in psm_states if s.record.config.get("model") == "dss-lite-psm"
    ]
    conv_states = [
        s for s in psm_states if s.record.config.get("model") == "DaiUsdsConverter"
    ]
    wrap_states = [
        s for s in psm_states if s.record.config.get("model") == "UsdsPsmWrapper"
    ]
    assert len(litepsm_states) == 1, f"Expected 1 LitePSM state, got {len(litepsm_states)}"
    assert len(wrap_states) == 0, f"Expected 0 UsdsPsmWrapper states, got {len(wrap_states)}"
    if use_usds:
        assert len(conv_states) == 1, (
            f"Expected 1 DaiUsdsConverter state, got {len(conv_states)}"
        )

    # Context unavailable reasons preserved
    unavailable_by_family: dict[str, set[str]] = {}
    for item in ctx_supp.unavailable:
        fam = item.get("family", "unknown")
        reason = item.get("reason", "unknown")
        unavailable_by_family.setdefault(fam, set()).add(reason)
    unavailable_reasons = {
        fam: sorted(reasons) for fam, reasons in unavailable_by_family.items()
    }

    # 1. WETH -> USDC quotes
    weth_quotes = {}
    winning_plans = {}
    for size in SIZES_WETH:
        req = TradeRequest(WETH, USDC, size * 10**18)
        fb_plan = fallback_plans.get(str(size)) if fallback_plans else None
        best_plan, eval_res = solve_best_candidate(
            req, ctx_supp.states, ev, fallback_plan=fb_plan
        )
        if not best_plan or not eval_res or not eval_res.feasible:
            weth_quotes[str(size)] = {"price": None, "amount_out": "0", "feasible": False}
            continue
        winning_plans[str(size)] = best_plan
        price = float(eval_res.amount_out) / 10**6 / size
        steps_info = [
            {
                "pool_id": s.pool_id,
                "token_in": s.token_in,
                "token_out": s.token_out,
                "amount_in": s.amount_in,
            }
            for s in best_plan.steps
        ]
        weth_quotes[str(size)] = {
            "price_usdc_per_weth": price,
            "amount_out": str(eval_res.amount_out),
            "amount_in_spent": str(eval_res.amount_in_spent),
            "residual_in": str(eval_res.residual_in),
            "feasible": eval_res.feasible,
            "solver": getattr(best_plan, "solver", "search"),
            "reasons": list(eval_res.reasons),
            "steps_count": len(best_plan.steps),
            "steps": steps_info,
        }

    # 2. USDS -> USDC quote (100,000 USDS)
    req_usds = TradeRequest(USDS, USDC, 100_000 * 10**18)
    plan_usds, e_usds = solve_best_candidate(req_usds, ctx_supp.states, ev)
    usds_quote = None
    if plan_usds and e_usds and e_usds.feasible:
        usds_quote = {
            "amount_in": str(req_usds.amount_in),
            "amount_out": str(e_usds.amount_out),
            "residual_in": str(e_usds.residual_in),
            "effective_price": float(e_usds.amount_out) / 10**6 / 100_000,
            "feasible": e_usds.feasible,
            "solver": getattr(plan_usds, "solver", "search"),
            "steps": [
                {
                    "pool_id": s.pool_id,
                    "token_in": s.token_in,
                    "token_out": s.token_out,
                    "amount_in": s.amount_in,
                }
                for s in plan_usds.steps
            ],
        }

    # 3. WBTC -> USDC quote (1 WBTC)
    req_wbtc = TradeRequest(WBTC, USDC, 10**8)
    plan_wbtc, e_wbtc = solve_best_candidate(req_wbtc, ctx_supp.states, ev)
    wbtc_quote = None
    if plan_wbtc and e_wbtc and e_wbtc.feasible:
        wbtc_quote = {
            "amount_in": str(req_wbtc.amount_in),
            "amount_out": str(e_wbtc.amount_out),
            "residual_in": str(e_wbtc.residual_in),
            "effective_price": float(e_wbtc.amount_out) / 10**6,
            "feasible": e_wbtc.feasible,
            "solver": getattr(plan_wbtc, "solver", "search"),
            "steps": [
                {
                    "pool_id": s.pool_id,
                    "token_in": s.token_in,
                    "token_out": s.token_out,
                    "amount_in": s.amount_in,
                }
                for s in plan_wbtc.steps
            ],
        }

    return {
        "block": block_num,
        "block_hash": ctx_supp.block.hash,
        "timestamp": ctx_supp.block.timestamp,
        "overlays_applied": overlay_labels,
        "total_states": len(ctx_supp.states),
        "total_records": len(ctx_supp.records),
        "state_families": sorted({s.record.family for s in ctx_supp.states}),
        "weth_quotes": weth_quotes,
        "usds_quote": usds_quote,
        "wbtc_quote": wbtc_quote,
        "context_unavailable": unavailable_reasons,
        "raw_context_states": ctx_supp.states,
        "winning_plans": winning_plans,
    }


def run_pipeline(
    blocks: tuple[int, ...] = PIN_BLOCKS,
    offline: bool = True,
) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = monotonic()

    comparisons = []
    per_block_unavailable = {}
    for block_num in blocks:
        # Baseline (original collection universe + Curve 3pool)
        baseline = evaluate_universe(
            block_num,
            use_wbtc=False,
            use_usds=False,
            use_pancake=False,
            use_usds_amm=False,
            offline=offline,
        )
        # Combined (all qualified sources together)
        combined = evaluate_universe(
            block_num,
            use_wbtc=True,
            use_usds=True,
            use_pancake=True,
            use_usds_amm=True,
            offline=offline,
            fallback_plans=baseline["winning_plans"],
        )

        # 1. No dropped previous sources check
        base_pool_ids = {s.record.pool_id for s in baseline["raw_context_states"]}
        comb_pool_ids = {s.record.pool_id for s in combined["raw_context_states"]}
        assert base_pool_ids.issubset(comb_pool_ids), (
            f"Baseline pools dropped at block {block_num}"
        )

        # 2. Floor / regression check and full-fill check
        for size in SIZES_WETH:
            base_q = baseline["weth_quotes"][str(size)]
            comb_q = combined["weth_quotes"][str(size)]
            base_out = int(base_q["amount_out"])
            comb_out = int(comb_q["amount_out"])
            assert comb_out >= base_out, (
                f"Candidate floor regressed at block {block_num} size {size}: "
                f"{comb_out} < {base_out}"
            )
            assert int(comb_q["residual_in"]) == 0, (
                f"Non-zero residual_in for combined size {size} at {block_num}"
            )
            assert int(comb_q["amount_in_spent"]) == size * 10**18, (
                f"Incomplete spend for combined size {size} at {block_num}"
            )

        if combined["usds_quote"]:
            assert int(combined["usds_quote"]["residual_in"]) == 0
        if combined["wbtc_quote"]:
            assert int(combined["wbtc_quote"]["residual_in"]) == 0

        per_block_unavailable[block_num] = combined["context_unavailable"]

        # Strip raw states and Plan objects before serializing to json
        baseline_clean = {
            k: v
            for k, v in baseline.items()
            if k not in ("raw_context_states", "winning_plans")
        }
        combined_clean = {
            k: v
            for k, v in combined.items()
            if k not in ("raw_context_states", "winning_plans")
        }

        comparisons.append(
            {
                "block": block_num,
                "block_hash": baseline["block_hash"],
                "baseline": baseline_clean,
                "combined": combined_clean,
                "delta_bps": {
                    str(size): (
                        (
                            int(combined["weth_quotes"][str(size)]["amount_out"])
                            - int(baseline["weth_quotes"][str(size)]["amount_out"])
                        )
                        * 10000.0
                        / int(baseline["weth_quotes"][str(size)]["amount_out"])
                    )
                    for size in SIZES_WETH
                },
            }
        )

    # Coverage summary across all 15 source families
    coverage = {
        "schema_version": 1,
        "blocks_evaluated": list(blocks),
        "validation_checks": {
            "full_fill_verified": True,
            "shared_psm_single_instance_verified": True,
            "no_dropped_previous_sources_verified": True,
            "no_regression_verified": True,
            "no_global_optimum_claim": True,
        },
        "families": {
            "uniswap_v2": {
                "status": "supported",
                "notes": "Qualified direct and routed pools active across all pins",
            },
            "uniswap_v3": {
                "status": "supported",
                "notes": "Direct WETH/USDC, WETH/USDT, 4 USDS AMM pools, and 12 snapshotted historical WBTC pools (6 active quote-qualified on selected routes, 6 snapshot-only)",
                "wbtc_pools": "12 snapshotted, 6 active quote-qualified on selected routes",
                "usds_amm_pools": "4 active pools (USDC/USDS 100, 500, 3000 and DAI/USDS 3000) quote-qualified",
            },
            "uniswap_v4": {
                "status": "supported",
                "notes": "StateView static-fee hookless pools active; USDS pools held 0 liquidity",
            },
            "pancake_v3": {
                "status": "supported_partial",
                "qualified_pools": [
                    "WETH/USDC 500 (1, 10, 100 WETH)",
                    "WETH/USDT 500 (1, 10 WETH)",
                    "WETH/WBTC 2500 (1, 10 WETH)",
                ],
                "excluded_pools": [
                    "WETH/USDC 2500 (thin - deceptive partial fill rejected)",
                    "WETH/USDT 500 at 100 WETH (exceeds loaded tick window)",
                ],
                "notes": "Refactored adapter subclassing UniV3State with uint32 feeProtocol",
            },
            "curve": {
                "status": "supported",
                "notes": "Legacy 3pool (DAI/USDT/USDC) + StableSwap-NG qualified",
            },
            "maker_sky_psm": {
                "status": "supported",
                "components": {
                    "LitePSM": "0xf6e7... USDC<->DAI finite buffer and pocket reserves",
                    "DaiUsdsConverter": "0x3225... 1:1 zero-fee connector edge into LitePSM",
                },
                "excluded_components": {
                    "UsdsPsmWrapper": "Disqualified to prevent duplicate shared LitePSM reserves",
                },
                "notes": "USDS routes into LitePSM via DaiUsds conversion edge without duplicate state",
            },
            "lido": {
                "status": "supported",
                "notes": "wstETH wrapping/unwrapping conversion",
            },
            "origin_arm": {
                "status": "supported",
                "notes": "Lido ARM swap pricing qualified",
            },
            "fluid_dex": {
                "status": "supported",
                "notes": "Single-use Fluid DEX T1 model qualified at stress pins",
            },
            "ekubo": {
                "status": "unresolved",
                "sub_versions": {
                    "Ekubo_V3": {
                        "status": "not_deployed_at_block",
                        "notes": "Absent in October 2025: tagged Dec 31 2025, bytecode empty (0x) across pins 23549939..23550192; live on mainnet in 2026",
                    },
                    "Ekubo_V2": {
                        "status": "unresolved",
                        "notes": "Unresolved historical adapter and tick inventory gap; zero PoolInitialized events during 254-block window bounds only new pool creations, not pre-existing pools (74 historical pools indexed prior to window)",
                    },
                },
                "notes": "Ekubo V2 is an unresolved adapter/inventory gap (not disqualified for zero creation events); V3 was uncreated in October 2025.",
            },
            "spark": {
                "status": "unresolved",
                "notes": "Targeted research item; no distinct automated quote model deployed",
            },
            "lista_stable": {
                "status": "not_deployed_at_block",
                "notes": "Lista deployments did not exist at October 2025 block window",
            },
            "balancer_v3": {
                "status": "discovered_unsupported",
                "notes": "Deployment research recorded; no trusted local pricing model implemented",
            },
            "erc4626_conversions": {
                "status": "discovered_unsupported",
                "notes": "sUSDe market exit data researched; offline vault pricing not in active universe",
            },
            "bebop": {
                "status": "unsupported",
                "notes": "Work cancelled; partial drafts unreviewed and removed from registry",
            },
            "0x_rfq": {
                "status": "unavailable",
                "notes": "Historical RFQ offers cannot be invented or reconstructed from settlement logs",
            },
        },
        "original_context_unavailable_reasons": {
            str(b): per_block_unavailable[b] for b in blocks
        },
    }

    comparison_artifact = OUT_DIR / "before_after_comparison.json"
    coverage_artifact = OUT_DIR / "coverage_summary.json"

    comp_payload = {
        "title": "Combined Source Expansion Before/After Comparison",
        "blocks": list(blocks),
        "runtime_seconds": monotonic() - started,
        "solver_policy": "bounded_3hop_search_plus_baseline_candidate_eval",
        "validation_checks": coverage["validation_checks"],
        "results": comparisons,
    }
    comparison_artifact.write_text(json.dumps(comp_payload, indent=2) + "\n")
    coverage_artifact.write_text(json.dumps(coverage, indent=2) + "\n")

    return {
        "status": "success",
        "comparison_file": str(comparison_artifact),
        "coverage_file": str(coverage_artifact),
        "blocks_count": len(blocks),
        "runtime_seconds": comp_payload["runtime_seconds"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blocks",
        help="comma-separated subset of block numbers (default: 3 qualification pins)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="execute 100%% offline using cached snapshot and collection evidence (default: True)",
    )
    args = parser.parse_args()

    blocks = (
        PIN_BLOCKS
        if not args.blocks
        else tuple(int(x.strip()) for x in args.blocks.split(","))
    )
    for b in blocks:
        if b not in PIN_BLOCKS:
            raise ValueError(
                f"Block {b} is not in the allowed 3 qualification pins {PIN_BLOCKS}"
            )

    res = run_pipeline(blocks, offline=args.offline)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
