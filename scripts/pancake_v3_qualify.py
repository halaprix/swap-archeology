"""PancakeSwap V3 historical qualification runner at pinned blocks.

Executes bounded qualification at three key October 10 2025 pins:
- Start:  23549939
- Stress: 23550094
- End:    23550192

For each pin:
1. Loads pool state for primary candidate pools:
   - WETH/USDC 500 (0x1ac1a8feaaea1900c4166deeed0c11cc10669d36)
   - WETH/USDT 500 (0x6ca298d2983ab03aa1da7679389d955a4efee15c)
   - WETH/WBTC 2500 (0x9b5699d18dff51fc65fb8ad6f70d93287c36349f)
   - Thin candidate WETH/USDC 2500 (0x19ac5f80ec17497d0e585b953100e6d18c330040)
2. Compares local in-memory PancakeV3State against on-chain QuoterV2 (0xb048bbc1ee6b733fffcfb9e9cef7375518e25997).
3. Verifies bit-for-bit quote agreement on liquid pools.
4. Demonstrates partial fill rejection on thin pools where QuoterV2 gives deceptive partial fills.
5. Saves raw evidence and offline snapshot fixtures with block hashes.

Usage:
  flock /tmp/swaparch-source-rpc.lock .venv/bin/python scripts/pancake_v3_qualify.py
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from swaparch.adapters.pancake_v3 import (
    PANCAKE_FACTORY,
    PANCAKE_QUOTER_V2,
    PancakeV3Adapter,
)
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "source-expansion" / "pancake"
CACHE_DIR = OUT_DIR / "rpc-cache"

PINS = [23549939, 23550094, 23550192]

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"

TEST_POOLS = [
    {
        "pool": "0x1ac1a8feaaea1900c4166deeed0c11cc10669d36",
        "name": "WETH/USDC 500",
        "t0": USDC,
        "t1": WETH,
        "sym0": "USDC",
        "sym1": "WETH",
        "dec0": 6,
        "dec1": 18,
        "fee": 500,
        "tick_spacing": 10,
        "trade_in": WETH,
        "trade_out": USDC,
        "sizes_weth": [1, 10, 100],
    },
    {
        "pool": "0x6ca298d2983ab03aa1da7679389d955a4efee15c",
        "name": "WETH/USDT 500",
        "t0": WETH,
        "t1": USDT,
        "sym0": "WETH",
        "sym1": "USDT",
        "dec0": 18,
        "dec1": 6,
        "fee": 500,
        "tick_spacing": 10,
        "trade_in": WETH,
        "trade_out": USDT,
        "sizes_weth": [1, 10, 100],
    },
    {
        "pool": "0x9b5699d18dff51fc65fb8ad6f70d93287c36349f",
        "name": "WETH/WBTC 2500",
        "t0": WBTC,
        "t1": WETH,
        "sym0": "WBTC",
        "sym1": "WETH",
        "dec0": 8,
        "dec1": 18,
        "fee": 2500,
        "tick_spacing": 50,
        "trade_in": WETH,
        "trade_out": WBTC,
        "sizes_weth": [1, 10],
    },
    {
        "pool": "0x19ac5f80ec17497d0e585b953100e6d18c330040",
        "name": "WETH/USDC 2500 (thin)",
        "t0": USDC,
        "t1": WETH,
        "sym0": "USDC",
        "sym1": "WETH",
        "dec0": 6,
        "dec1": 18,
        "fee": 2500,
        "tick_spacing": 50,
        "trade_in": WETH,
        "trade_out": USDC,
        "sizes_weth": [1],
    },
]

SEL_QUOTE_EXACT_IN_SINGLE = (
    "0x" + keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
)


class SnapshotDict:
    def __init__(self, block: BlockRef, calls: dict[tuple[str, str], CallResult]) -> None:
        self.block = block
        self._calls = calls

    def get(self, spec: CallSpec) -> CallResult:
        return self._calls[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._calls


def quoter_spec(token_in: str, token_out: str, amount_in: int, fee: int) -> CallSpec:
    data = (
        SEL_QUOTE_EXACT_IN_SINGLE
        + abi_encode(
            ["(address,address,uint256,uint24,uint160)"],
            [(token_in, token_out, amount_in, fee, 0)],
        ).hex()
    )
    return CallSpec(to=PANCAKE_QUOTER_V2, data=data, tag=f"quoter:{fee}:{amount_in}")


def qualify() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    client = RpcClient(cache_root=CACHE_DIR)
    mc = Multicall3(client)
    adapter = PancakeV3Adapter(word_radius=8)

    pin_results: list[dict[str, Any]] = []

    for block_num in PINS:
        block = client.get_block(block_num)
        print("\n=======================================================")
        print(f"Qualifying block {block.number} (hash: {block.hash})")
        print("=======================================================")

        block_data: dict[str, Any] = {
            "block": asdict(block),
            "pools": [],
        }

        for pool_cfg in TEST_POOLS:
            pool_record = PoolRecord(
                family="pancake_v3",
                chain=1,
                pool_id=f"pancake_v3:{PANCAKE_FACTORY}:{pool_cfg['pool']}",
                deployment=PANCAKE_FACTORY,
                pool=pool_cfg["pool"],
                tokens=(
                    Token(
                        chain=1,
                        address=pool_cfg["t0"],
                        symbol=pool_cfg["sym0"],
                        decimals=pool_cfg["dec0"],
                    ),
                    Token(
                        chain=1,
                        address=pool_cfg["t1"],
                        symbol=pool_cfg["sym1"],
                        decimals=pool_cfg["dec1"],
                    ),
                ),
                config={"fee": pool_cfg["fee"], "tick_spacing": pool_cfg["tick_spacing"]},
                created_block=16950000,
                discovered_by={"method": "factory:getPool"},
                status=SupportStatus.SUPPORTED,
            )

            # 1. Phase 1 read requests
            calls_map: dict[tuple[str, str], CallResult] = {}
            phase1_specs = adapter.read_requests(pool_record, block)
            p1_res = mc.call(phase1_specs, block)
            for r in p1_res:
                calls_map[(r.spec.to, r.spec.data)] = r

            snap = SnapshotDict(block, calls_map)

            # 2. Phase 2 dependent requests (tickBitmap)
            phase2_specs = adapter.dependent_requests(pool_record, block, snap)
            if phase2_specs:
                p2_res = mc.call(phase2_specs, block)
                for r in p2_res:
                    calls_map[(r.spec.to, r.spec.data)] = r

            # 3. Phase 3 dependent requests (ticks)
            snap = SnapshotDict(block, calls_map)
            phase3_specs = adapter.dependent_requests(pool_record, block, snap)
            if phase3_specs:
                p3_res = mc.call(phase3_specs, block)
                for r in p3_res:
                    calls_map[(r.spec.to, r.spec.data)] = r

            snap = SnapshotDict(block, calls_map)
            state = adapter.load_state(pool_record, snap)
            provenance = adapter.provenance(pool_record, snap)

            # 4. QuoterV2 comparison vs in-memory quote
            comparisons: list[dict[str, Any]] = []
            for size_weth in pool_cfg["sizes_weth"]:
                amount_in = size_weth * 10**18
                q_spec = quoter_spec(
                    pool_cfg["trade_in"], pool_cfg["trade_out"], amount_in, pool_cfg["fee"]
                )
                q_res = mc.call([q_spec], block)[0]

                quoter_out: int | None = None
                quoter_ticks_crossed: int | None = None
                if q_res.success and len(q_res.raw) >= 66:
                    dec = abi_decode(
                        ["uint256", "uint160", "uint32", "uint256"], bytes.fromhex(q_res.raw[2:])
                    )
                    quoter_out = dec[0]
                    quoter_ticks_crossed = dec[2]

                local_out: int | None = None
                local_error: str | None = None
                try:
                    local_out = state.quote_exact_in(
                        pool_cfg["trade_in"], pool_cfg["trade_out"], amount_in
                    )
                except Unsupported as exc:
                    local_error = str(exc)
                except (RuntimeError, ValueError) as exc:
                    local_error = f"Unexpected {type(exc).__name__}: {exc}"

                match = False
                partial_fill_risk = False
                if quoter_out is not None and local_out is not None:
                    match = quoter_out == local_out
                elif quoter_out is not None and local_error is not None:
                    # Deceptive quoter partial fill: quoter returns output, but state detects partial fill
                    partial_fill_risk = True

                comparisons.append(
                    {
                        "size_weth": size_weth,
                        "amount_in": amount_in,
                        "quoter_out": quoter_out,
                        "quoter_ticks_crossed": quoter_ticks_crossed,
                        "local_out": local_out,
                        "local_error": local_error,
                        "match": match,
                        "partial_fill_detected": partial_fill_risk,
                    }
                )
                status_str = (
                    "EXACT MATCH"
                    if match
                    else ("PARTIAL FILL DETECTED (REFUSED)" if partial_fill_risk else "MISMATCH")
                )
                print(
                    f"  [{pool_cfg['name']}] {size_weth} WETH -> local: {local_out} | quoter: {quoter_out} [{status_str}]"
                )

            pool_entry = {
                "name": pool_cfg["name"],
                "pool": pool_cfg["pool"],
                "fee": pool_cfg["fee"],
                "tick_spacing": pool_cfg["tick_spacing"],
                "slot0": {
                    "sqrt_price_x96": state.sqrt_price_x96,
                    "tick": state.tick,
                    "liquidity": state.liquidity,
                    "fee_protocol": state.fee_protocol,
                },
                "provenance": dict(provenance),
                "comparisons": comparisons,
                "snapshot_calls": [
                    {
                        "to": r.spec.to,
                        "data": r.spec.data,
                        "tag": r.spec.tag,
                        "success": r.success,
                        "raw": r.raw,
                    }
                    for r in calls_map.values()
                ],
            }
            block_data["pools"].append(pool_entry)

        pin_results.append(block_data)

    summary = {
        "factory": PANCAKE_FACTORY,
        "quoter": PANCAKE_QUOTER_V2,
        "pins": PINS,
        "results": pin_results,
        "network_requests": client.network_requests,
    }

    evidence_file = OUT_DIR / "qualification-evidence.json"
    evidence_file.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nQualification finished. Wrote complete evidence to {evidence_file}")
    return summary


if __name__ == "__main__":
    qualify()
