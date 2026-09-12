"""Differential checks between local UniV3State and on-chain QuoterV2 for WBTC pools.

Compares exact outputs and final sqrtPriceX96 values at historical blocks
(start 23549939, stress 23550094, end 23550192) for:
  - Leg 1: WETH -> WBTC (across fee tiers 500 and 3000 bps)
  - Leg 2: WBTC -> USDC (across fee tiers 500 and 3000 bps)
  - Leg 2 (alt): WBTC -> USDT (across fee tiers 500 and 3000 bps)
  - Full 2-leg sequential composition: WETH -> WBTC -> USDC

Outputs:
  - outputs/source-expansion/wbtc/quoter_checks.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak
from wbtc_collect import DEFAULT_PINS, load_block_reference
from wbtc_inventory import DEFAULT_OUT, TOKENS, build_wbtc_pool_records

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.types import BlockRef, PoolRecord
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore

QUOTER_V2 = "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
SEL_QUOTE = (
    "0x" + keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
)
QUOTE_TYPES = ("uint256", "uint160", "uint32", "uint256")


def call_quoter(
    client: RpcClient,
    block: BlockRef,
    token_in: str,
    token_out: str,
    amount_in: int,
    fee: int,
) -> dict[str, Any]:
    """Call QuoterV2.quoteExactInputSingle on mainnet."""
    data = (
        SEL_QUOTE
        + encode(
            ["(address,address,uint256,uint24,uint160)"],
            [(token_in, token_out, amount_in, fee, 0)],
        ).hex()
    )
    res = client.eth_call(QUOTER_V2, data, block)

    row: dict[str, Any] = {
        "token_in": token_in,
        "token_out": token_out,
        "amount_in": amount_in,
        "fee": fee,
        "success": res.success,
        "raw": res.raw,
        "via": res.via,
    }
    if res.success:
        amount_out, sqrt_after, ticks_crossed, gas_est = decode(
            QUOTE_TYPES, bytes.fromhex(res.raw[2:])
        )
        row.update(
            {
                "amount_out": amount_out,
                "sqrt_price_after": sqrt_after,
                "initialized_ticks_crossed": ticks_crossed,
                "gas_estimate": gas_est,
            }
        )
    return row


def run_differential_check(
    block_num: int,
    output_dir: Path = DEFAULT_OUT,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Perform differential verification at a single block."""
    if client is None:
        client = RpcClient()

    block = load_block_reference(block_num, client)
    store = SnapshotStore(output_dir / "snapshots")
    snapshot = store.load(block.chain, block.hash)

    adapter = UniswapV3Adapter(word_radius=8)
    records = build_wbtc_pool_records(validated_block_hashes=[block.hash])
    states_by_pair_fee: dict[tuple[str, int], Any] = {}
    records_by_pair_fee: dict[tuple[str, int], PoolRecord] = {}

    for rec in records:
        state = adapter.load_state(rec, snapshot)
        pair = f"{state.token0.symbol}/{state.token1.symbol}"
        states_by_pair_fee[(pair, state.fee)] = state
        records_by_pair_fee[(pair, state.fee)] = rec

    wbtc = TOKENS["WBTC"].address
    weth = TOKENS["WETH"].address
    usdc = TOKENS["USDC"].address
    usdt = TOKENS["USDT"].address

    sizes_weth = [1, 10, 100]
    checks: list[dict[str, Any]] = []

    # 1. Single pool leg checks
    # Test WETH/WBTC 500 and 3000 pools
    for fee in (500, 3000):
        state_leg1 = states_by_pair_fee[("WBTC/WETH", fee)]
        state_leg2_usdc = states_by_pair_fee[("WBTC/USDC", fee)]
        state_leg2_usdt = states_by_pair_fee[("WBTC/USDT", fee)]

        for n in sizes_weth:
            amount_weth = n * 10**18

            # Leg 1: WETH -> WBTC
            local_wbtc_out, state1_after = state_leg1.swap(weth, wbtc, amount_weth)
            quoter_leg1 = call_quoter(client, block, weth, wbtc, amount_weth, fee)

            leg1_match = local_wbtc_out == quoter_leg1.get(
                "amount_out"
            ) and state1_after.sqrt_price_x96 == quoter_leg1.get("sqrt_price_after")

            # Leg 2a: WBTC -> USDC using leg 1 output
            local_usdc_out, state2_after = state_leg2_usdc.swap(wbtc, usdc, local_wbtc_out)
            quoter_leg2 = call_quoter(client, block, wbtc, usdc, local_wbtc_out, fee)

            leg2_match = local_usdc_out == quoter_leg2.get(
                "amount_out"
            ) and state2_after.sqrt_price_x96 == quoter_leg2.get("sqrt_price_after")

            # Leg 2b: WBTC -> USDT using leg 1 output
            local_usdt_out, state2_usdt_after = state_leg2_usdt.swap(wbtc, usdt, local_wbtc_out)
            quoter_leg2_usdt = call_quoter(client, block, wbtc, usdt, local_wbtc_out, fee)

            leg2_usdt_match = local_usdt_out == quoter_leg2_usdt.get(
                "amount_out"
            ) and state2_usdt_after.sqrt_price_x96 == quoter_leg2_usdt.get("sqrt_price_after")

            checks.append(
                {
                    "path": f"WETH->WBTC->USDC (fee {fee})",
                    "input_weth": n,
                    "input_weth_wei": amount_weth,
                    "leg1_weth_wbtc": {
                        "pool": records_by_pair_fee[("WBTC/WETH", fee)].pool,
                        "fee": fee,
                        "local_out": local_wbtc_out,
                        "quoter_out": quoter_leg1.get("amount_out"),
                        "out_diff_wei": local_wbtc_out - (quoter_leg1.get("amount_out") or 0),
                        "local_sqrt_after": str(state1_after.sqrt_price_x96),
                        "quoter_sqrt_after": str(quoter_leg1.get("sqrt_price_after")),
                        "exact_match": leg1_match,
                    },
                    "leg2_wbtc_usdc": {
                        "pool": records_by_pair_fee[("WBTC/USDC", fee)].pool,
                        "fee": fee,
                        "input_wbtc": local_wbtc_out,
                        "local_out": local_usdc_out,
                        "quoter_out": quoter_leg2.get("amount_out"),
                        "out_diff_wei": local_usdc_out - (quoter_leg2.get("amount_out") or 0),
                        "local_sqrt_after": str(state2_after.sqrt_price_x96),
                        "quoter_sqrt_after": str(quoter_leg2.get("sqrt_price_after")),
                        "exact_match": leg2_match,
                    },
                    "leg2_wbtc_usdt": {
                        "pool": records_by_pair_fee[("WBTC/USDT", fee)].pool,
                        "fee": fee,
                        "input_wbtc": local_wbtc_out,
                        "local_out": local_usdt_out,
                        "quoter_out": quoter_leg2_usdt.get("amount_out"),
                        "out_diff_wei": local_usdt_out - (quoter_leg2_usdt.get("amount_out") or 0),
                        "local_sqrt_after": str(state2_usdt_after.sqrt_price_x96),
                        "quoter_sqrt_after": str(quoter_leg2_usdt.get("sqrt_price_after")),
                        "exact_match": leg2_usdt_match,
                    },
                    "composite_usdc_out": local_usdc_out,
                    "composite_effective_price_usdc_per_weth": (local_usdc_out / 1e6) / n,
                    "all_legs_exact_match": leg1_match and leg2_match and leg2_usdt_match,
                }
            )

    return {
        "block": block.number,
        "block_hash": block.hash,
        "timestamp": block.timestamp,
        "total_checks": len(checks),
        "all_exact_matches": all(c["all_legs_exact_match"] for c in checks),
        "checks": checks,
    }


def run_differential_checks(
    blocks: list[int] | tuple[int, ...] = DEFAULT_PINS,
    output_dir: Path = DEFAULT_OUT,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Execute differential checks across all specified blocks and persist report."""
    if client is None:
        client = RpcClient()

    block_reports = []
    for b in blocks:
        rep = run_differential_check(b, output_dir=output_dir, client=client)
        block_reports.append(rep)

    summary = {
        "scope": "Differential checks between UniV3State model and on-chain QuoterV2 for historical WBTC pools",
        "quoter_v2": QUOTER_V2,
        "blocks_tested": list(blocks),
        "total_checks_per_block": block_reports[0]["total_checks"] if block_reports else 0,
        "overall_exact_match": all(r["all_exact_matches"] for r in block_reports),
        "block_reports": block_reports,
    }

    target = output_dir / "quoter_checks.json"
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
    summary = run_differential_checks(blocks, output_dir=Path(args.output_dir))
    print(
        json.dumps(
            {
                "status": "success",
                "overall_exact_match": summary["overall_exact_match"],
                "report": str(Path(args.output_dir) / "quoter_checks.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
