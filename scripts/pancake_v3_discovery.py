"""PancakeSwap V3 pool discovery at historical block 23549939.

Discovers PancakeSwap V3 pools for core token pairs:
WETH, USDC, USDT, WBTC, DAI, USDS across canonical fee tiers (100, 500, 2500, 10000).
Checks liquidity and creation block for each discovered pool.
Saves raw RPC evidence with block hash and structured PoolRecords.

Usage:
  flock /tmp/swaparch-source-rpc.lock .venv/bin/python scripts/pancake_v3_discovery.py
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
    PANCAKE_FEE_SPACINGS,
    SEL_LIQUIDITY,
)
from swaparch.core.types import BlockRef, CallSpec, PoolRecord, SupportStatus, Token, norm_address
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "source-expansion" / "pancake"
CACHE_DIR = OUT_DIR / "rpc-cache"

START_BLOCK = 23549939

TOKENS: dict[str, tuple[str, int]] = {
    "WETH": ("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", 18),
    "USDC": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
    "USDT": ("0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
    "WBTC": ("0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", 8),
    "DAI": ("0x6b175474e89094c44da98b954eedeac495271d0f", 18),
    "USDS": ("0xdc035d45d973e3ec169d2276ddab16f1e407384f", 18),
}

PAIRS_TO_CHECK = [
    ("WETH", "USDC"),
    ("WETH", "USDT"),
    ("WETH", "WBTC"),
    ("WETH", "DAI"),
    ("WETH", "USDS"),
    ("WBTC", "USDC"),
    ("WBTC", "USDT"),
    ("USDC", "USDT"),
    ("DAI", "USDC"),
    ("DAI", "USDT"),
    ("USDS", "USDC"),
]

FEES_TO_CHECK = [100, 500, 2500, 10000]

POOL_CREATED_TOPIC = "0x" + keccak(text="PoolCreated(address,address,uint24,int24,address)").hex()


def _pad_address(addr: str) -> str:
    return "0x" + norm_address(addr)[2:].rjust(64, "0")


def _pad_uint24(val: int) -> str:
    return "0x" + hex(val)[2:].rjust(64, "0")


def get_pool_spec(token_a: str, token_b: str, fee: int) -> CallSpec:
    sel = "0x" + keccak(text="getPool(address,address,uint24)")[:4].hex()
    data = (
        sel
        + abi_encode(
            ["address", "address", "uint24"], [norm_address(token_a), norm_address(token_b), fee]
        ).hex()
    )
    return CallSpec(to=PANCAKE_FACTORY, data=data, tag=f"getPool:{fee}")


def find_creation_info(
    client: RpcClient, pool_addr: str, to_block: int
) -> tuple[int | None, dict[str, Any] | None]:
    """Find the exact creation block and PoolCreated event via binary search on eth_getCode."""
    low = 16950000
    high = to_block
    if client._rpc("eth_getCode", [norm_address(pool_addr), hex(to_block)]) == "0x":
        return None, None

    while low < high:
        mid = (low + high) // 2
        code = client._rpc("eth_getCode", [norm_address(pool_addr), hex(mid)])
        if code != "0x":
            high = mid
        else:
            low = mid + 1

    created_block = low
    creation_log = None
    try:
        logs = client.get_logs(
            address=PANCAKE_FACTORY,
            topics=[POOL_CREATED_TOPIC],
            from_block=created_block,
            to_block=created_block,
            chunk=1000,
        )
        for lg in logs:
            data = lg.get("data", "")
            if len(data) >= 130:
                logged_pool = "0x" + data[66:130][-40:]
                if norm_address(logged_pool) == norm_address(pool_addr):
                    creation_log = lg
                    break
        if not creation_log and logs:
            creation_log = logs[0]
    except (RuntimeError, ValueError) as exc:
        print(f"Log query failed at block {created_block} for {pool_addr}: {exc}")

    return created_block, creation_log


def run_discovery() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    client = RpcClient(cache_root=CACHE_DIR)
    block: BlockRef = client.get_block(START_BLOCK)

    # 1. Enumerate all pairs and fees
    all_probes: list[tuple[str, str, int]] = []
    call_specs: list[CallSpec] = []
    for sym_a, sym_b in PAIRS_TO_CHECK:
        addr_a, _ = TOKENS[sym_a]
        addr_b, _ = TOKENS[sym_b]
        for fee in FEES_TO_CHECK:
            all_probes.append((sym_a, sym_b, fee))
            call_specs.append(get_pool_spec(addr_a, addr_b, fee))

    print(
        f"Querying {len(call_specs)} pool candidates on factory {PANCAKE_FACTORY} at block {block.number}..."
    )
    results = Multicall3(client).call(call_specs, block)

    discovered_pools: list[dict[str, Any]] = []
    for (sym_a, sym_b, fee), res in zip(all_probes, results, strict=True):
        if not res.success or res.raw == "0x":
            continue
        pool_addr = norm_address(abi_decode(["address"], bytes.fromhex(res.raw[2:]))[0])
        if int(pool_addr, 16) == 0:
            continue

        addr_a, dec_a = TOKENS[sym_a]
        addr_b, dec_b = TOKENS[sym_b]
        t0_addr, t1_addr = (
            (addr_a, addr_b) if norm_address(addr_a) < norm_address(addr_b) else (addr_b, addr_a)
        )
        t0_sym, t1_sym = (
            (sym_a, sym_b) if norm_address(addr_a) < norm_address(addr_b) else (sym_b, sym_a)
        )
        t0_dec, t1_dec = (
            (dec_a, dec_b) if norm_address(addr_a) < norm_address(addr_b) else (dec_b, dec_a)
        )

        discovered_pools.append(
            {
                "pool": pool_addr,
                "sym0": t0_sym,
                "sym1": t1_sym,
                "token0": t0_addr,
                "token1": t1_addr,
                "dec0": t0_dec,
                "dec1": t1_dec,
                "fee": fee,
                "tick_spacing": PANCAKE_FEE_SPACINGS.get(fee, 0),
                "discovery_call": asdict(res),
            }
        )

    print(f"Found {len(discovered_pools)} non-zero pools. Reading live liquidity...")

    # 2. Read liquidity for each discovered pool
    liq_specs = [
        CallSpec(to=p["pool"], data=SEL_LIQUIDITY, tag="liquidity()") for p in discovered_pools
    ]
    liq_results = Multicall3(client).call(liq_specs, block)

    for p, res in zip(discovered_pools, liq_results, strict=True):
        p["liquidity"] = (
            abi_decode(["uint128"], bytes.fromhex(res.raw[2:]))[0] if res.success else 0
        )
        p["liquidity_call"] = asdict(res)
        p["active"] = p["liquidity"] > 0

    # 3. Read creation log for active or key candidate pools
    print("Finding creation logs for discovered pools...")
    pool_records: list[dict[str, Any]] = []
    for p in discovered_pools:
        created_block, creation_log = find_creation_info(client, p["pool"], block.number)
        p["creation_log"] = creation_log
        p["created_block"] = created_block

        rec = PoolRecord(
            family="pancake_v3",
            chain=1,
            pool_id=f"pancake_v3:{PANCAKE_FACTORY}:{p['pool']}",
            deployment=PANCAKE_FACTORY,
            pool=p["pool"],
            tokens=(
                Token(chain=1, address=p["token0"], symbol=p["sym0"], decimals=p["dec0"]),
                Token(chain=1, address=p["token1"], symbol=p["sym1"], decimals=p["dec1"]),
            ),
            config={
                "fee": p["fee"],
                "tick_spacing": p["tick_spacing"],
                "factory": PANCAKE_FACTORY,
            },
            created_block=created_block,
            discovered_by={
                "method": "factory:getPool",
                "factory": PANCAKE_FACTORY,
                "block": block.number,
                "block_hash": block.hash,
                "log": creation_log,
            },
            status=SupportStatus.SUPPORTED if p["active"] else SupportStatus.DISCOVERED_UNSUPPORTED,
            notes=f"PancakeSwap V3 {p['sym0']}/{p['sym1']} {p['fee']} pips ({p['fee'] / 10000:.2f}%)",
        )
        pool_records.append(
            {
                "pool_record": asdict(rec),
                "pool": p["pool"],
                "pair": f"{p['sym0']}/{p['sym1']}",
                "fee": p["fee"],
                "liquidity": p["liquidity"],
                "active": p["active"],
                "created_block": created_block,
            }
        )

    summary = {
        "block": asdict(block),
        "factory": PANCAKE_FACTORY,
        "total_probes": len(call_specs),
        "pools_found": len(discovered_pools),
        "active_pools": sum(1 for p in discovered_pools if p["active"]),
        "pools": discovered_pools,
        "records": pool_records,
        "network_requests": client.network_requests,
    }

    out_file = OUT_DIR / "discovery.json"
    out_file.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"Discovery complete. Saved {len(discovered_pools)} pools ({summary['active_pools']} active) to {out_file}."
    )
    return summary


if __name__ == "__main__":
    run_discovery()
