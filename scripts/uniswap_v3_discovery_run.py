"""Run Uniswap V3 discovery once and write ``data/discovery/1/uniswap_v3.json``.

One ``eth_getLogs`` per *ordered* token pair (topic0 = PoolCreated, topic1/topic2
= the pair), from the factory's creation block to a pinned ``to_block`` whose
hash is recorded, plus one unfiltered ``FeeAmountEnabled`` scan over the same
range.  Token symbols/decimals are resolved on chain at ``to_block`` through one
Multicall3 batch and cached in the inventory.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import castlib as C
from eth_abi import decode as abi_decode
from eth_utils import keccak

from swaparch.core.types import SupportStatus, Token
from swaparch.discovery import uniswap_v3 as D

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "discovery" / "1" / "uniswap_v3.json"
EVID = ROOT / "data" / "adapters-evidence" / "uniswap_v3"

TO_BLOCK = 25896003
SEL_SYMBOL = "0x" + keccak(text="symbol()")[:4].hex()
SEL_DECIMALS = "0x" + keccak(text="decimals()")[:4].hex()

#: The 0.05% WETH/USDC pool whose quotes were cross-checked against QuoterV2.
CROSS_CHECKED_POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
CROSS_CHECK_EVIDENCE = [
    "docs/adapters/uniswap_v3.md",
    "data/adapters-evidence/uniswap_v3/quoter-cross-check-25896003-r8.json",
    "data/adapters-evidence/uniswap_v3/quoter-cross-check-23549991-r8.json",
    "data/adapters-evidence/uniswap_v3/funded-run-comparison.json",
]


def resolve_tokens(block: int) -> dict[str, Token]:
    addresses = [a for a in D.DISCOVERY_TOKENS.values()]
    calls = []
    for a in addresses:
        calls += [(a, SEL_SYMBOL), (a, SEL_DECIMALS)]
    out = C.multicall(calls, block, chunk=50)
    tokens: dict[str, Token] = {}
    raw: list[dict] = []
    for i, a in enumerate(addresses):
        ok_s, ret_s = out[2 * i]
        ok_d, ret_d = out[2 * i + 1]
        if not (ok_s and ok_d):
            raise RuntimeError(f"token metadata read failed for {a}")
        symbol = abi_decode(["string"], ret_s)[0]
        decimals = abi_decode(["uint8"], ret_d)[0]
        tokens[a] = Token(chain=1, address=a, symbol=symbol, decimals=decimals)
        raw += [
            {"to": a, "call": "symbol()", "data": SEL_SYMBOL, "result": "0x" + ret_s.hex(),
             "decoded": symbol},
            {"to": a, "call": "decimals()", "data": SEL_DECIMALS, "result": "0x" + ret_d.hex(),
             "decoded": decimals},
        ]
    (EVID / "token-metadata.json").write_text(
        json.dumps({"block": block, "calls": raw}, indent=1)
    )
    return tokens


def main() -> int:
    scanned = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    to_block_hash = C.cast_block_hash(TO_BLOCK)
    tokens = resolve_tokens(TO_BLOCK)

    specs = D.build_pool_created_filters(D.DISCOVERY_TOKENS, to_block=TO_BLOCK)
    coverage: list[dict] = []
    records: dict[str, dict] = {}
    raw_logs: list[dict] = []

    for spec in specs:
        logs = json.loads(C.cast_logs(spec.address, list(spec.topics), spec.from_block,
                                      spec.to_block))
        coverage.append(
            {
                "method": "logs:PoolCreated",
                "deployment": D.FACTORY,
                "filter": {
                    "topics": list(spec.topics),
                    "tokens": [spec.meta["token0"], spec.meta["token1"]],
                    "symbols": [spec.meta["token0_symbol"], spec.meta["token1_symbol"]],
                    "canonical_ordering": spec.meta["canonical"],
                },
                "from_block": spec.from_block,
                "to_block": spec.to_block,
                "to_block_hash": to_block_hash,
                "scanned_utc": scanned,
                "logs_found": len(logs),
            }
        )
        for log in logs:
            raw_logs.append(log)
            rec = D.pool_record_from_log(
                log, tokens, filter_meta=spec.meta, status=SupportStatus.DISCOVERED_UNSUPPORTED
            )
            note = (
                "quote model cross-checked against QuoterV2 at blocks 25896003 and 23549991 "
                "and against 9 settled forge-fork swaps"
                if rec.pool == CROSS_CHECKED_POOL
                else "Discovery only; run validate_v3_universe.py before admitting this pool to routing"
            )
            records[rec.pool_id] = {
                **D.pool_record_to_json(rec),
                "notes": note,
                **({"evidence": CROSS_CHECK_EVIDENCE} if rec.pool == CROSS_CHECKED_POOL else {}),
            }

    fee_spec = D.build_fee_amount_enabled_filter(to_block=TO_BLOCK)
    fee_logs = json.loads(
        C.cast_logs(fee_spec.address, list(fee_spec.topics), fee_spec.from_block,
                    fee_spec.to_block)
    )
    fee_tiers = [D.decode_fee_amount_enabled(log) for log in fee_logs]
    coverage.append(
        {
            "method": "logs:FeeAmountEnabled",
            "deployment": D.FACTORY,
            "filter": {"topics": list(fee_spec.topics)},
            "from_block": fee_spec.from_block,
            "to_block": fee_spec.to_block,
            "to_block_hash": to_block_hash,
            "scanned_utc": scanned,
            "logs_found": len(fee_logs),
        }
    )

    pools = sorted(records.values(), key=lambda r: (r["created_block"] or 0, r["pool"]))
    found_tiers = sorted({p["config"]["fee"] for p in pools})

    inventory = {
        "chain": 1,
        "family": "uniswap_v3",
        "schema": 1,
        "status": "supported",
        "deployments": [
            {
                "address": D.FACTORY,
                "version": "UniswapV3Factory (v3-core 1.0.0)",
                "created_block": D.FACTORY_CREATION_BLOCK,
                "evidence": [
                    "data/adapters-evidence/uniswap_v3/factory-creation.json",
                    "docs/adapters/uniswap_v3.md",
                ],
            }
        ],
        "coverage": coverage,
        "pools": pools,
        "fee_tiers_enabled": [
            {"fee": t["fee"], "tick_spacing": t["tick_spacing"],
             "enabled_block": t["enabled_block"], "tx_hash": t["tx_hash"]}
            for t in sorted(fee_tiers, key=lambda t: t["enabled_block"] or 0)
        ],
        "fee_tiers_found_in_pools": found_tiers,
        "unresolved": [
            {
                "what": "per-pool QuoterV2 cross-check",
                "why": (
                    "the cross-check was run on 0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640 only; "
                    "other pools require their own checks at the requested block hash"
                ),
                "next_step": (
                    "run scripts/validate_v3_universe.py BLOCK after discovery"
                ),
            },
            {
                "what": "rebasing and fee-on-transfer tokens (stETH)",
                "why": (
                    "UniswapV3Pool.swap accounting is balance-checked; a rebasing token such as "
                    "stETH can make the pool's balance drift from its tracked liquidity. The "
                    "swap math itself is unaffected, but such pools are typically near-empty and "
                    "were not depth-verified"
                ),
                "next_step": "verify actual token-transfer semantics and usable liquidity before routing",
            },
            {
                "what": "tick coverage per block",
                "why": (
                    "state is loaded for a bounded tick-bitmap window; a trade large enough to "
                    "leave it raises Unsupported rather than returning a number"
                ),
                "next_step": "widen UniswapV3Adapter(word_radius=...) and re-acquire",
            },
        ],
        "notes": (
            "Discovery is pair-filtered over the six study endpoints plus stETH and USDe as "
            "connectors, from the factory's creation block 12369621 to block 25896003. topic3 "
            "(fee) is left unset so every fee tier is discovered, not just 100/500/3000/10000. "
            "A negative result covers exactly the pairs listed in `coverage`: adding a token "
            "later needs a backfill of the new pairs from block 12369621."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(inventory, indent=1))
    (EVID / "poolcreated-raw-logs.json").write_text(
        json.dumps({"to_block": TO_BLOCK, "to_block_hash": to_block_hash,
                    "pool_created": raw_logs, "fee_amount_enabled": fee_logs}, indent=1)
    )

    print(f"pools: {len(pools)}  fee tiers in pools: {found_tiers}")
    print(f"fee tiers enabled on the factory: "
          f"{[(t['fee'], t['tick_spacing'], t['enabled_block']) for t in fee_tiers]}")
    print(f"coverage entries: {len(coverage)}   cast/eth_* invocations: {C.CALL_COUNT}")
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
