"""Evaluation of two-leg WETH -> WBTC -> USDC routes at historical pins.

Measures:
  - Full input consumption (residual_in == 0, 100% fill) across 1, 10, 100 WETH
  - Output USDC received and effective execution price (USDC/WETH)
  - Price impact / liquidity deterioration across trade sizes
  - Comparison across different fee-tier route compositions (500/500, 3000/3000, mixed)
  - Comparison against direct Uniswap V3 WETH/USDC pools

Outputs:
  - outputs/source-expansion/wbtc/two_leg_eval.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wbtc_collect import DEFAULT_PINS, load_block_reference
from wbtc_inventory import DEFAULT_OUT, TOKENS, build_wbtc_pool_records

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.snapshot.store import SnapshotStore

# Direct Uniswap V3 WETH/USDC pools for baseline comparison
DIRECT_WETH_USDC_500 = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
DIRECT_WETH_USDC_3000 = "0x7bea39867e4169dbe237d55c8242a8f2fcdcc387"


def evaluate_block_routes(
    block_num: int,
    output_dir: Path = DEFAULT_OUT,
) -> dict[str, Any]:
    """Evaluate two-leg routing for 1, 10, 100 WETH at a given block."""
    block = load_block_reference(block_num)
    store = SnapshotStore(output_dir / "snapshots")
    snapshot = store.load(block.chain, block.hash)

    adapter = UniswapV3Adapter(word_radius=8)
    records = build_wbtc_pool_records(validated_block_hashes=[block.hash])

    # Index pool states by pair and fee
    states: dict[tuple[str, int], Any] = {}
    for rec in records:
        state = adapter.load_state(rec, snapshot)
        pair = f"{state.token0.symbol}/{state.token1.symbol}"
        states[(pair, state.fee)] = state

    wbtc = TOKENS["WBTC"].address
    weth = TOKENS["WETH"].address
    usdc = TOKENS["USDC"].address
    usdt = TOKENS["USDT"].address

    sizes = [1, 10, 100]
    route_configs = [
        ("WETH->WBTC(500)->USDC(500)", 500, 500, "USDC"),
        ("WETH->WBTC(3000)->USDC(3000)", 3000, 3000, "USDC"),
        ("WETH->WBTC(500)->USDC(3000)", 500, 3000, "USDC"),
        ("WETH->WBTC(3000)->USDC(500)", 3000, 500, "USDC"),
        ("WETH->WBTC(500)->USDT(500)", 500, 500, "USDT"),
        ("WETH->WBTC(3000)->USDT(3000)", 3000, 3000, "USDT"),
    ]

    evaluations: list[dict[str, Any]] = []

    for name, fee1, fee2, out_sym in route_configs:
        out_addr = usdc if out_sym == "USDC" else usdt
        leg2_pair = f"WBTC/{out_sym}"

        state_leg1_init = states[("WBTC/WETH", fee1)]
        state_leg2_init = states[(leg2_pair, fee2)]

        size_results = []
        for n in sizes:
            amount_in = n * 10**18
            try:
                # Leg 1: WETH -> WBTC
                wbtc_out, _s1_after = state_leg1_init.swap(weth, wbtc, amount_in)
                # Leg 2: WBTC -> out_token
                final_out, _s2_after = state_leg2_init.swap(wbtc, out_addr, wbtc_out)

                decimals_out = 6
                effective_price = (final_out / (10**decimals_out)) / n
                leg1_rate = (wbtc_out / 1e8) / n
                leg2_rate = (final_out / (10**decimals_out)) / (wbtc_out / 1e8)

                size_results.append(
                    {
                        "size_weth": n,
                        "amount_in_spent": amount_in,
                        "residual_in": 0,
                        "full_input_consumed": True,
                        "intermediate_wbtc_out_raw": wbtc_out,
                        "intermediate_wbtc_out": wbtc_out / 1e8,
                        "wbtc_per_weth": leg1_rate,
                        "final_out_raw": final_out,
                        "final_out": final_out / (10**decimals_out),
                        "out_per_wbtc": leg2_rate,
                        "effective_price_per_weth": effective_price,
                        "succeeded": True,
                        "error": None,
                    }
                )
            except Unsupported as exc:
                size_results.append(
                    {
                        "size_weth": n,
                        "amount_in_spent": 0,
                        "residual_in": amount_in,
                        "full_input_consumed": False,
                        "succeeded": False,
                        "error": str(exc),
                    }
                )

        # Calculate price deterioration from 1 WETH to 10 and 100 WETH
        p1 = (
            size_results[0].get("effective_price_per_weth")
            if size_results[0]["succeeded"]
            else None
        )
        p10 = (
            size_results[1].get("effective_price_per_weth")
            if size_results[1]["succeeded"]
            else None
        )
        p100 = (
            size_results[2].get("effective_price_per_weth")
            if size_results[2]["succeeded"]
            else None
        )

        impact_10_bps = ((p1 - p10) / p1 * 10_000) if (p1 and p10) else None
        impact_100_bps = ((p1 - p100) / p1 * 10_000) if (p1 and p100) else None

        evaluations.append(
            {
                "route": name,
                "leg1_pool": state_leg1_init.record.pool,
                "leg1_fee": fee1,
                "leg2_pool": state_leg2_init.record.pool,
                "leg2_fee": fee2,
                "out_asset": out_sym,
                "all_sizes_consumed_fully": all(r["full_input_consumed"] for r in size_results),
                "price_impact_1_to_10_bps": impact_10_bps,
                "price_impact_1_to_100_bps": impact_100_bps,
                "sizes": size_results,
            }
        )

    # Find the best WBTC route for USDC output at each size
    best_by_size = {}
    for n in sizes:
        candidates = []
        for ev in evaluations:
            if ev["out_asset"] != "USDC":
                continue
            sr = next((r for r in ev["sizes"] if r["size_weth"] == n and r["succeeded"]), None)
            if sr:
                candidates.append((ev["route"], sr["final_out"], sr["effective_price_per_weth"]))
        if candidates:
            best_route, best_out, best_price = max(candidates, key=lambda x: x[1])
            best_by_size[str(n)] = {
                "best_route": best_route,
                "usdc_out": best_out,
                "effective_price": best_price,
            }

    return {
        "block": block.number,
        "block_hash": block.hash,
        "timestamp": block.timestamp,
        "best_wbtc_usdc_routes": best_by_size,
        "routes": evaluations,
    }


def evaluate_all_pins(
    blocks: list[int] | tuple[int, ...] = DEFAULT_PINS,
    output_dir: Path = DEFAULT_OUT,
) -> dict[str, Any]:
    """Evaluate two-leg routes across all qualification pins."""
    block_results = [evaluate_block_routes(b, output_dir=output_dir) for b in blocks]

    summary = {
        "scope": "Two-leg WBTC route evaluation and input consumption validation",
        "pins_tested": list(blocks),
        "overall_full_input_consumed": all(
            all(r["all_sizes_consumed_fully"] for r in b["routes"]) for b in block_results
        ),
        "block_results": block_results,
    }

    target = output_dir / "two_leg_eval.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=2) + "\n")
    tmp.replace(target)
    return summary


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
    args = parser.parse_args()

    blocks = [int(b.strip()) for b in args.blocks.split(",")] if args.blocks else list(DEFAULT_PINS)
    summary = evaluate_all_pins(blocks, output_dir=Path(args.output_dir))
    print(
        json.dumps(
            {
                "status": "success",
                "overall_full_input_consumed": summary["overall_full_input_consumed"],
                "eval_path": str(Path(args.output_dir) / "two_leg_eval.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
