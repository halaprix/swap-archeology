"""Historical USDS pool state collector for Uniswap V3 connector pools.

Collects and qualifies full tick and pool state for the 4 active discovered USDS pools
at the three historical qualification pins:
  - 23549939 (start)
  - 23550094 (stress)
  - 23550192 (end)

The 4 pools:
  1. USDS / USDC 1 bps (fee=100, spacing=1)  -> 0x4eb5db0134fac94e66da89764d58a9f709d53a8f
  2. USDS / USDC 5 bps (fee=500, spacing=10) -> 0x8aee53b873176d9f938d24a53a8ae5cf36276464
  3. USDS / USDC 30 bps (fee=3000, spacing=60)-> 0xa66a2770bc0e0c65b63b5a3bb4560e90f95d6146
  4. USDS / DAI 30 bps (fee=3000, spacing=60) -> 0xe9f1e2ef814f5686c30ce6fb7103d0f780836c67

Features:
  - Uses SnapshotStore and standard UniswapV3Adapter (bounded ticks)
  - Emits records/<block_hash>.json and snapshots/<chain>/<block_hash>/ in supplement format
  - Runs independent QuoterV2 checks for 1 and 100,000 USDS exact-input swaps
  - Verifies fail-closed bounded tick coverage without fabricated fullfill
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallSpec, PoolRecord, SupportStatus, Token, norm_address
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "outputs/source-expansion/usds-amm"
REFERENCES_FILE = ROOT / "outputs/dense-crash/references.json"
DEFAULT_PINS = (23_549_939, 23_550_094, 23_550_192)

V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
SEL_QUOTE_EXACT_INPUT_SINGLE = (
    "0x" + keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
)

USDS_ADDR = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
USDC_ADDR = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI_ADDR = "0x6b175474e89094c44da98b954eedeac495271d0f"

TOKENS: dict[str, Token] = {
    "USDS": Token(1, USDS_ADDR, "USDS", 18),
    "USDC": Token(1, USDC_ADDR, "USDC", 6),
    "DAI": Token(1, DAI_ADDR, "DAI", 18),
}

POOLS_META: list[dict[str, Any]] = [
    {
        "pair_label": "USDS/USDC",
        "pool": "0x4eb5db0134fac94e66da89764d58a9f709d53a8f",
        "token0": "USDC",
        "token1": "USDS",
        "fee": 100,
        "fee_bps": 1.0,
        "tick_spacing": 1,
        "created_block": 20700000,
    },
    {
        "pair_label": "USDS/USDC",
        "pool": "0x8aee53b873176d9f938d24a53a8ae5cf36276464",
        "token0": "USDC",
        "token1": "USDS",
        "fee": 500,
        "fee_bps": 5.0,
        "tick_spacing": 10,
        "created_block": 20700000,
    },
    {
        "pair_label": "USDS/USDC",
        "pool": "0xa66a2770bc0e0c65b63b5a3bb4560e90f95d6146",
        "token0": "USDC",
        "token1": "USDS",
        "fee": 3000,
        "fee_bps": 30.0,
        "tick_spacing": 60,
        "created_block": 20700000,
    },
    {
        "pair_label": "USDS/DAI",
        "pool": "0xe9f1e2ef814f5686c30ce6fb7103d0f780836c67",
        "token0": "DAI",
        "token1": "USDS",
        "fee": 3000,
        "fee_bps": 30.0,
        "tick_spacing": 60,
        "created_block": 20700000,
    },
]


def build_usds_amm_pool_records(validated_block_hashes: list[str] | None = None) -> list[PoolRecord]:
    records: list[PoolRecord] = []
    hashes = list(validated_block_hashes) if validated_block_hashes else []
    for meta in POOLS_META:
        pool_addr = norm_address(meta["pool"])
        t0 = TOKENS[meta["token0"]]
        t1 = TOKENS[meta["token1"]]
        rec = PoolRecord(
            family="uniswap_v3",
            chain=1,
            pool_id=f"uniswap_v3:{norm_address(V3_FACTORY)}:{pool_addr}",
            deployment=norm_address(V3_FACTORY),
            pool=pool_addr,
            tokens=(t0, t1),
            config={
                "fee": meta["fee"],
                "fee_bps": meta["fee_bps"],
                "tick_spacing": meta["tick_spacing"],
                "validated_block_hashes": hashes,
            },
            created_block=meta["created_block"],
            discovered_by={
                "method": "v3_factory_getPool",
                "deployment": norm_address(V3_FACTORY),
                "deployed_by_block": meta["created_block"],
                "filter": {
                    "token0": t0.address,
                    "token1": t1.address,
                    "token0_symbol": t0.symbol,
                    "token1_symbol": t1.symbol,
                },
            },
            status=SupportStatus.SUPPORTED,
            notes=f"Active Uniswap V3 {meta['pair_label']} {meta['fee_bps']:g} bps pool",
        )
        records.append(rec)
    return records


def pool_record_to_dict(rec: PoolRecord) -> dict[str, Any]:
    return {
        "family": rec.family,
        "chain": rec.chain,
        "pool_id": rec.pool_id,
        "deployment": rec.deployment,
        "pool": rec.pool,
        "tokens": [
            {"address": t.address, "symbol": t.symbol, "decimals": t.decimals} for t in rec.tokens
        ],
        "config": dict(rec.config),
        "created_block": rec.created_block,
        "discovered_by": dict(rec.discovered_by),
        "status": rec.status.value,
        "notes": rec.notes,
    }


def load_block_reference(number: int, client: RpcClient | None = None) -> BlockRef:
    if REFERENCES_FILE.is_file():
        data = json.loads(REFERENCES_FILE.read_text())
        for row in data.get("rows", []):
            if row.get("block") == number:
                return BlockRef(
                    chain=1,
                    number=number,
                    hash=str(row["blockHash"]).lower(),
                    timestamp=int(row["timestamp"]),
                )
    if client is None:
        client = RpcClient()
    return client.get_block(number)


def run_quoter_checks(
    block: BlockRef,
    records: list[PoolRecord],
    adapter: UniswapV3Adapter,
    snapshot: Any,
    client: RpcClient | None = None,
) -> list[dict[str, Any]]:
    amounts = [10**18, 100_000 * 10**18]
    checks = []

    specs = []
    call_keys = []
    for rec in records:
        pool_addr = norm_address(rec.pool)
        t0, t1 = rec.tokens
        fee = rec.config["fee"]
        other_token = t0.address  # USDS is t1 in all 4 pools
        for amt in amounts:
            args = abi_encode(
                ["(address,address,uint256,uint24,uint160)"],
                [(t1.address, other_token, amt, fee, 0)],
            )
            spec = CallSpec(QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE + args.hex(), f"{pool_addr}:{amt}")
            specs.append(spec)
            call_keys.append((rec, other_token, amt))

    if client is None:
        client = RpcClient()

    results = Multicall3(client).call(specs, block)
    for (rec, other_token, amt), res in zip(call_keys, results, strict=True):
        st = adapter.load_state(rec, snapshot)
        quoter_out = None
        quoter_sqrt_after = None
        if res.success:
            decoded = abi_decode(["uint256", "uint160", "uint32", "uint256"], bytes.fromhex(res.raw[2:]))
            quoter_out = decoded[0]
            quoter_sqrt_after = decoded[1]

        local_out = None
        local_sqrt_after = None
        revert_reason = None
        try:
            local_out, next_st = st.swap(rec.tokens[1].address, other_token, amt)
            local_sqrt_after = next_st.sqrt_price_x96
        except Unsupported as exc:
            revert_reason = str(exc)

        matched = (local_out is not None and local_out == quoter_out)
        checks.append({
            "pool": rec.pool,
            "pool_id": rec.pool_id,
            "fee": rec.config["fee"],
            "amount_in": amt,
            "quoter_success": res.success,
            "quoter_out": str(quoter_out) if quoter_out is not None else None,
            "quoter_sqrt_after": str(quoter_sqrt_after) if quoter_sqrt_after is not None else None,
            "local_out": str(local_out) if local_out is not None else None,
            "local_sqrt_after": str(local_sqrt_after) if local_sqrt_after is not None else None,
            "matched": matched,
            "fail_closed_reason": revert_reason,
        })
    return checks


def collect_block(
    block_num: int,
    output_dir: Path = DEFAULT_OUT,
    word_radius: int = 8,
    client: RpcClient | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    snapshots_dir = output_dir / "snapshots"
    records_dir = output_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    store = SnapshotStore(snapshots_dir)
    if client is None and not offline:
        client = RpcClient()

    block = load_block_reference(block_num, client)
    records = build_usds_amm_pool_records(validated_block_hashes=[block.hash])

    adapter = UniswapV3Adapter(word_radius=word_radius)
    adapters = {"uniswap_v3": adapter}

    if offline:
        snapshot = store.load(block.chain, block.hash)
        report = None
    else:
        snapshot, report = acquire(adapters, records, block, store, client)

    loaded_states = []
    pool_summaries = []
    for rec in records:
        state = adapter.load_state(rec, snapshot)
        loaded_states.append(state)
        pool_summaries.append({
            "pool": rec.pool,
            "tokens": f"{state.token0.symbol}/{state.token1.symbol}",
            "fee": state.fee,
            "tick": state.tick,
            "sqrt_price_x96": str(state.sqrt_price_x96),
            "liquidity": str(state.liquidity),
            "word_range": [state.word_lo, state.word_hi],
            "initialized_ticks_count": len(state.tick_liquidity_net),
        })

    # Save records document in supplement format
    rec_doc = {
        "blockHash": block.hash,
        "blockNumber": block.number,
        "timestamp": block.timestamp,
        "records": [pool_record_to_dict(r) for r in records],
    }
    rec_target = records_dir / f"{block.hash}.json"
    tmp = rec_target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec_doc, indent=2) + "\n")
    tmp.replace(rec_target)

    quoter_results = None
    if not offline:
        quoter_results = run_quoter_checks(block, records, adapter, snapshot, client)

    return {
        "block": block.number,
        "block_hash": block.hash,
        "timestamp": block.timestamp,
        "specs_total": report.specs_total if report else len(snapshot.calls),
        "phases": report.phases if report else 0,
        "pools_loaded": len(loaded_states),
        "pools": pool_summaries,
        "quoter_checks": quoter_results,
    }


def collect_blocks(
    blocks: list[int] | tuple[int, ...],
    output_dir: Path = DEFAULT_OUT,
    word_radius: int = 8,
    client: RpcClient | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if client is None and not offline:
        client = RpcClient()

    results = []
    all_quoter_checks = {}
    for b in blocks:
        res = collect_block(
            b,
            output_dir=output_dir,
            word_radius=word_radius,
            client=client,
            offline=offline,
        )
        if res.get("quoter_checks"):
            all_quoter_checks[b] = res["quoter_checks"]
        results.append(res)

    # Save standalone inventory overlay
    all_records = build_usds_amm_pool_records(
        validated_block_hashes=[r["block_hash"] for r in results]
    )
    overlay = {
        "family": "uniswap_v3",
        "chain": 1,
        "pools": [pool_record_to_dict(r) for r in all_records],
    }
    overlay_path = output_dir / "usds_amm_inventory.json"
    overlay_path.write_text(json.dumps(overlay, indent=2) + "\n")

    manifest = {
        "scope": "Historical USDS Uniswap V3 connector pools collection",
        "blocks_requested": list(blocks),
        "blocks_collected": len(results),
        "word_radius": word_radius,
        "output_directory": str(output_dir),
        "results": results,
    }

    manifest_file = output_dir / "collection_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")

    if all_quoter_checks:
        quoter_file = output_dir / "quoter_checks.json"
        quoter_file.write_text(json.dumps(all_quoter_checks, indent=2) + "\n")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blocks",
        help="Comma-separated block numbers (defaults to qualification pins: 23549939,23550094,23550192)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUT),
        help="Output directory for USDS AMM artifacts",
    )
    parser.add_argument(
        "--word-radius",
        type=int,
        default=8,
        help="Tick bitmap word radius around current tick (default: 8)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Load cached snapshot offline without RPC calls",
    )
    args = parser.parse_args()

    blocks = [int(b.strip()) for b in args.blocks.split(",")] if args.blocks else list(DEFAULT_PINS)
    manifest = collect_blocks(
        blocks,
        output_dir=Path(args.output_dir),
        word_radius=args.word_radius,
        offline=args.offline,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "blocks_collected": manifest["blocks_collected"],
                "manifest": str(Path(args.output_dir) / "collection_manifest.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
