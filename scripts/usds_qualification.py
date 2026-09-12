"""Qualify USDS connectors (DaiUsdsConverter and UsdsPsmWrapper) and discover AMM pools.

Historical Ethereum ETH/WETH sell->USDC crash window (October 10 2025):
Pins:
  - start: 23549939
  - stress: 23550094
  - end: 23550192

Serializes archive-RPC calls through flock /tmp/swaparch-source-rpc.lock.
Saves raw pinned evidence with block hashes and call receipts.
Produces an inventory overlay with USDS token, connector records, and active AMM pools.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from swaparch.adapters.litepsm import LitePsmAdapter, LitePsmState
from swaparch.adapters.usds import (
    CONVERTER,
    DAI,
    LITE_PSM,
    POCKET,
    USDC,
    USDS,
    WRAPPER,
    DaiUsdsAdapter,
    DaiUsdsState,
    UsdsPsmWrapperAdapter,
    UsdsPsmWrapperState,
)
from swaparch.core.protocols import Unsupported
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    PoolRecord,
    SupportStatus,
    Token,
    norm_address,
)
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore, StoredSnapshot

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs/source-expansion/usds"
DEFAULT_PINS = [23549939, 23550094, 23550192]

V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
V4_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
V4_STATE_VIEW = "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"

TOKENS_DEF = {
    "DAI": Token(1, DAI, "DAI", 18),
    "USDS": Token(1, USDS, "USDS", 18),
    "USDC": Token(1, USDC, "USDC", 6),
    "USDT": Token(1, "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6),
    "WETH": Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
}


def make_pool_record(
    pool_id: str,
    deployment: str,
    pool: str,
    tokens: tuple[Token, ...],
    model: str,
    kind: str,
    config_extra: dict[str, Any] | None = None,
) -> PoolRecord:
    cfg = {"kind": kind, "model": model}
    if config_extra:
        cfg.update(config_extra)
    return PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id=pool_id,
        deployment=norm_address(deployment),
        pool=norm_address(pool),
        tokens=tokens,
        config=cfg,
        created_block=None,
        discovered_by={"method": "curated", "deployed_by_block": 23549939},
        status=SupportStatus.SUPPORTED,
        notes=f"{model} qualified at historical pins",
    )


def qualify_pin(
    client: RpcClient,
    block: BlockRef,
    store: SnapshotStore,
    conv_adapter: DaiUsdsAdapter,
    wrap_adapter: UsdsPsmWrapperAdapter,
    lite_adapter: LitePsmAdapter,
    conv_rec: PoolRecord,
    wrap_rec: PoolRecord,
    lite_rec: PoolRecord,
) -> dict[str, Any]:
    # 1. Read phase 1 specs
    specs = (
        conv_adapter.read_requests(conv_rec, block)
        + wrap_adapter.read_requests(wrap_rec, block)
        + lite_adapter.read_requests(lite_rec, block)
    )
    # Deduplicate CallSpecs
    unique_specs = list({(s.to, s.data): s for s in specs}.values())
    results = Multicall3(client).call(unique_specs, block)
    snapshot1 = StoredSnapshot(block, tuple(results))

    # 2. Read phase 2 (dependent) specs
    dep_specs = (
        conv_adapter.dependent_requests(conv_rec, block, snapshot1)
        + wrap_adapter.dependent_requests(wrap_rec, block, snapshot1)
        + lite_adapter.dependent_requests(lite_rec, block, snapshot1)
    )
    unique_deps = list({(s.to, s.data): s for s in dep_specs if not snapshot1.has(s)}.values())
    if unique_deps:
        dep_results = Multicall3(client).call(unique_deps, block)
        snapshot = StoredSnapshot(block, tuple(results) + tuple(dep_results))
    else:
        snapshot = snapshot1

    # Load states
    conv_state: DaiUsdsState = conv_adapter.load_state(conv_rec, snapshot)
    wrap_state: UsdsPsmWrapperState = wrap_adapter.load_state(wrap_rec, snapshot)
    lite_state: LitePsmState = lite_adapter.load_state(lite_rec, snapshot)

    # Arithmetic checks
    checks = []

    # Check 1: Converter 1:1 exactness across sizes
    for amount in (1, 100, 1_000_000, 100_000_000):
        wad = amount * 10**18
        out_usds, _ = conv_state.swap(DAI, USDS, wad)
        out_dai, _ = conv_state.swap(USDS, DAI, wad)
        checks.append({
            "name": f"converter_1_to_1_{amount}_DAI_to_USDS",
            "matched": out_usds == wad,
            "in": wad,
            "out": out_usds,
        })
        checks.append({
            "name": f"converter_1_to_1_{amount}_USDS_to_DAI",
            "matched": out_dai == wad,
            "in": wad,
            "out": out_dai,
        })

    # Check 2: Wrapper vs LitePSM exact semantic equivalence
    # For sellGem (USDC -> USDS vs USDC -> DAI -> USDS):
    for usdc_units in (1, 100, 100_000):
        usdc_in = usdc_units * 10**6
        # Wrapper sellGem
        usds_wrap_out, wrap_after = wrap_state.swap(USDC, USDS, usdc_in)
        # LitePSM sellGem + Converter
        dai_lite_out, lite_after = lite_state.swap(USDC, DAI, usdc_in)
        usds_conv_out, _ = conv_state.swap(DAI, USDS, dai_lite_out)

        matched = (
            usds_wrap_out == usds_conv_out == dai_lite_out
            and wrap_after.dai_buffer == lite_after.dai_buffer
            and wrap_after.pocket_gem == lite_after.pocket_gem
        )
        checks.append({
            "name": f"sell_gem_equivalence_{usdc_units}_USDC",
            "matched": matched,
            "usds_wrap_out": usds_wrap_out,
            "usds_conv_out": usds_conv_out,
            "dai_buffer_after": wrap_after.dai_buffer,
        })

    # For buyGem (USDS -> USDC vs USDS -> DAI -> USDC):
    for usdc_units in (1, 100, 100_000):
        gem_out = usdc_units * 10**6
        # Preimage USDS in:
        usds_in = gem_out * 10**12  # with tout = 0
        usdc_wrap_out, wrap_after = wrap_state.swap(USDS, USDC, usds_in)

        # Via Converter + LitePSM:
        dai_out, _ = conv_state.swap(USDS, DAI, usds_in)
        usdc_lite_out, lite_after = lite_state.swap(DAI, USDC, dai_out)

        matched = (
            usdc_wrap_out == usdc_lite_out == gem_out
            and wrap_after.dai_buffer == lite_after.dai_buffer
            and wrap_after.pocket_gem == lite_after.pocket_gem
            and wrap_after.pocket_gem_allowance == lite_after.pocket_gem_allowance
        )
        checks.append({
            "name": f"buy_gem_equivalence_{usdc_units}_USDC",
            "matched": matched,
            "usdc_wrap_out": usdc_wrap_out,
            "usdc_lite_out": usdc_lite_out,
            "pocket_gem_after": wrap_after.pocket_gem,
        })

    # Check 3: Shared capacity IDs match
    wrap_caps = wrap_state.capacity_ids()
    lite_caps = lite_state.capacity_ids()
    checks.append({
        "name": "shared_capacity_ids_identity",
        "matched": wrap_caps == lite_caps,
        "wrapper_capacity_ids": list(wrap_caps),
        "litepsm_capacity_ids": list(lite_caps),
    })

    # Check 4: Unattainable input / preimage requirement
    unattainable_in = 10**18 + 1  # 1 USDS + 1 wei (fractional micro-USDC)
    wrap_rejected = False
    lite_rejected = False
    try:
        wrap_state.swap(USDS, USDC, unattainable_in)
    except Unsupported:
        wrap_rejected = True

    try:
        lite_state.swap(DAI, USDC, unattainable_in)
    except Unsupported:
        lite_rejected = True

    checks.append({
        "name": "unattainable_exact_input_rejection",
        "matched": wrap_rejected and lite_rejected,
        "wrap_rejected": wrap_rejected,
        "lite_rejected": lite_rejected,
    })

    # Save all call specs & raw values
    all_specs = unique_specs + unique_deps
    calls_data = []
    for s in all_specs:
        res: CallResult = snapshot.get(s)
        calls_data.append({
            "to": s.to,
            "data": s.data,
            "tag": s.tag,
            "success": res.success,
            "raw": res.raw,
        })

    return {
        "block": asdict(block),
        "dai_usds_state": {
            "live": conv_state.live,
            "usds_wards": conv_state.usds_wards,
            "dai_wards": conv_state.dai_wards,
        },
        "usds_psm_wrapper_state": {
            "psm": wrap_state.psm,
            "tin": wrap_state.tin,
            "tout": wrap_state.tout,
            "to18_conversion_factor": wrap_state.to18_conversion_factor,
            "dai_buffer": str(wrap_state.dai_buffer),
            "pocket_gem": str(wrap_state.pocket_gem),
            "pocket_gem_allowance": str(wrap_state.pocket_gem_allowance),
        },
        "litepsm_state": {
            "tin": lite_state.tin,
            "tout": lite_state.tout,
            "dai_buffer": str(lite_state.dai_buffer),
            "pocket_gem": str(lite_state.pocket_gem),
            "pocket_gem_allowance": str(lite_state.pocket_gem_allowance),
        },
        "checks": checks,
        "all_checks_passed": all(c["matched"] for c in checks),
        "calls": calls_data,
    }


def discover_amm_pools(client: RpcClient, block: BlockRef) -> dict[str, Any]:
    sel_get_pool = "0x" + keccak(text="getPool(address,address,uint24)")[:4].hex()
    sel_liq = "0x" + keccak(text="liquidity()")[:4].hex()

    pairs = [
        ("USDS", "USDC", USDS, USDC),
        ("USDS", "USDT", USDS, TOKENS_DEF["USDT"].address),
        ("USDS", "DAI", USDS, DAI),
        ("USDS", "WETH", USDS, TOKENS_DEF["WETH"].address),
    ]
    fees = [100, 500, 3000, 10000]

    specs = []
    meta = []
    for s1, s2, t1, t2 in pairs:
        for f in fees:
            data = sel_get_pool + abi_encode(["address", "address", "uint24"], [t1, t2, f]).hex()
            specs.append(CallSpec(V3_FACTORY, data, f"v3:{s1}/{s2}:{f}"))
            meta.append((s1, s2, f))

    results = Multicall3(client).call(specs, block)
    v3_candidates = []
    for (s1, s2, f), r in zip(meta, results, strict=True):
        if r.success:
            addr = abi_decode(["address"], bytes.fromhex(r.raw[2:]))[0]
            if addr != "0x0000000000000000000000000000000000000000":
                v3_candidates.append({
                    "venue": "uniswap_v3",
                    "pair": f"{s1}/{s2}",
                    "token0": s1,
                    "token1": s2,
                    "fee": f,
                    "pool": norm_address(addr),
                })

    if v3_candidates:
        liq_specs = [
            CallSpec(c["pool"], sel_liq, f"v3_liq:{c['pair']}:{c['fee']}")
            for c in v3_candidates
        ]
        liq_results = Multicall3(client).call(liq_specs, block)
        for c, lr in zip(v3_candidates, liq_results, strict=True):
            if lr.success and len(lr.raw) >= 66:
                c["liquidity"] = abi_decode(["uint128"], bytes.fromhex(lr.raw[2:]))[0]
                c["active"] = c["liquidity"] > 0
            else:
                c["liquidity"] = 0
                c["active"] = False

    # V4 checks for hookless pools
    v4_pools = [
        ("USDC/USDS 500", "0x665e779426fdfcbe68a867db6e771b9dca2ca59f14066060cbb6da82e7fc77e4"),
        ("USDC/USDS 100", "0xcecc13fab7e487da5a1b32d184719e761614f17d3d2b270725aee869d80d216f"),
        ("USDT/USDS 500", "0x91d820ba59560f4e15033bc6f0144d1e21b790d79d6ca32cb28b2a3734a36e92"),
        ("USDT/USDS 100", "0xb54ece655e084ef757754f0a0d924d55be16f39446f9065a3977a419eb31c8ad"),
        ("DAI/USDS 500",  "0xfbeee5c0a3eafe1bcce14112e457e7eb7dae74b3f86e3f4219e917d29486c91a"),
    ]
    sel_v4_liq = "0xfa6793d5"
    v4_specs = [
        CallSpec(V4_STATE_VIEW, sel_v4_liq + pid[2:], f"v4_liq:{label}")
        for label, pid in v4_pools
    ]
    v4_results = Multicall3(client).call(v4_specs, block)
    v4_candidates = []
    for (label, pid), vr in zip(v4_pools, v4_results, strict=True):
        liq = 0
        if vr.success and len(vr.raw) >= 66:
            liq = abi_decode(["uint128"], bytes.fromhex(vr.raw[2:]))[0]
        v4_candidates.append({
            "venue": "uniswap_v4",
            "label": label,
            "pool_id": pid,
            "liquidity": liq,
            "active": liq > 0,
        })

    return {
        "block": asdict(block),
        "uniswap_v3": v3_candidates,
        "uniswap_v4": v4_candidates,
    }


def build_inventory_overlay(discovery_result: dict[str, Any]) -> dict[str, Any]:
    pools = [
        {
            "family": "maker_sky_psm",
            "chain": 1,
            "pool_id": f"maker_sky_psm:{CONVERTER}:{CONVERTER}",
            "deployment": CONVERTER,
            "pool": CONVERTER,
            "tokens": [asdict(TOKENS_DEF["DAI"]), asdict(TOKENS_DEF["USDS"])],
            "config": {
                "kind": "converter",
                "model": "DaiUsdsConverter",
                "rate": "1:1 exact, no fee",
                "directions": [
                    {
                        "name": "daiToUsds",
                        "token_in": DAI,
                        "token_out": USDS,
                        "rate": "1:1 exact integer",
                    },
                    {
                        "name": "usdsToDai",
                        "token_in": USDS,
                        "token_out": DAI,
                        "rate": "1:1 exact integer",
                    },
                ],
            },
            "created_block": None,
            "discovered_by": {
                "method": "curated",
                "deployed_by_block": 23549939,
                "evidence": ["https://github.com/sky-ecosystem/usds"],
            },
            "status": "supported",
            "notes": "1:1 zero-fee connector between DAI and USDS via DSS DaiJoin/UsdsJoin.",
        },
        {
            "family": "maker_sky_psm",
            "chain": 1,
            "pool_id": f"maker_sky_psm:{WRAPPER}:{WRAPPER}",
            "deployment": WRAPPER,
            "pool": WRAPPER,
            "tokens": [asdict(TOKENS_DEF["USDS"]), asdict(TOKENS_DEF["USDC"])],
            "config": {
                "kind": "psm_wrapper",
                "model": "UsdsPsmWrapper",
                "wraps": LITE_PSM,
                "to18ConversionFactor": 10**12,
                "tin": 0,
                "tout": 0,
                "capacity_ids": [
                    f"maker_sky_psm:{LITE_PSM}:dai_buffer",
                    f"maker_sky_psm:{LITE_PSM}:pocket_usdc",
                ],
            },
            "created_block": None,
            "discovered_by": {
                "method": "curated",
                "deployed_by_block": 23549939,
                "evidence": ["https://github.com/sky-ecosystem/usds-psm-wrapper"],
            },
            "status": SupportStatus.DISCOVERED_UNSUPPORTED.value,
            "notes": "Reference model only; excluded from active universe to avoid duplicate shared LitePSM reserves.",
        },
    ]

    # Add active V3 pools from discovery
    for p in discovery_result.get("uniswap_v3", []):
        if p.get("active"):
            t0 = TOKENS_DEF[p["token0"]]
            t1 = TOKENS_DEF[p["token1"]]
            fee_bps = p["fee"] / 100.0
            pools.append({
                "family": "uniswap_v3",
                "chain": 1,
                "pool_id": f"uniswap_v3:{V3_FACTORY}:{p['pool']}",
                "deployment": V3_FACTORY,
                "pool": p["pool"],
                "tokens": [asdict(t0), asdict(t1)],
                "config": {
                    "fee": p["fee"],
                    "fee_bps": fee_bps,
                    "active_liquidity_at_start": str(p["liquidity"]),
                },
                "created_block": None,
                "discovered_by": {
                    "method": "v3_factory_getPool",
                    "deployed_by_block": 23549939,
                },
                "status": SupportStatus.SUPPORTED.value,
                "notes": f"Uniswap V3 {p['pair']} {fee_bps:g} bps (fee={p['fee']}) pool with active liquidity at 23549939",
            })

    return {
        "family": "maker_sky_psm",
        "tokens": [asdict(TOKENS_DEF["USDS"])],
        "pools": pools,
    }


def main():
    parser = argparse.ArgumentParser(description="Qualify USDS connectors and discover AMMs")
    parser.add_argument(
        "--pins",
        default=",".join(str(p) for p in DEFAULT_PINS),
        help="Comma-separated block numbers for historical qualification",
    )
    args = parser.parse_args()
    pins = [int(p.strip()) for p in args.pins.split(",") if p.strip()]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = RpcClient()
    store = SnapshotStore()

    conv_adapter = DaiUsdsAdapter()
    wrap_adapter = UsdsPsmWrapperAdapter()
    lite_adapter = LitePsmAdapter()

    conv_rec = make_pool_record(
        f"maker_sky_psm:{CONVERTER}:{CONVERTER}",
        CONVERTER,
        CONVERTER,
        (TOKENS_DEF["DAI"], TOKENS_DEF["USDS"]),
        "DaiUsdsConverter",
        "converter",
    )
    wrap_rec = make_pool_record(
        f"maker_sky_psm:{WRAPPER}:{WRAPPER}",
        WRAPPER,
        WRAPPER,
        (TOKENS_DEF["USDS"], TOKENS_DEF["USDC"]),
        "UsdsPsmWrapper",
        "psm_wrapper",
        config_extra={"to18ConversionFactor": 10**12, "wraps": LITE_PSM},
    )
    lite_rec = make_pool_record(
        f"maker_sky_psm:{LITE_PSM}:{LITE_PSM}",
        LITE_PSM,
        LITE_PSM,
        (TOKENS_DEF["DAI"], TOKENS_DEF["USDC"]),
        "dss-lite-psm",
        "psm",
        config_extra={"to18ConversionFactor": 10**12, "pocket": POCKET},
    )

    pin_results = []
    print(f"Qualifying USDS connectors across {len(pins)} historical pins...")
    for p in pins:
        block = client.get_block(p)
        print(f"  Pin {block.number} (hash {block.hash})...")
        res = qualify_pin(
            client, block, store, conv_adapter, wrap_adapter, lite_adapter, conv_rec, wrap_rec, lite_rec
        )
        pin_results.append(res)
        print(f"    All checks passed: {res['all_checks_passed']}")

    # AMM discovery at first pin (23549939)
    first_block = client.get_block(pins[0])
    print(f"Discovering USDS AMM pools at block {first_block.number}...")
    amm_res = discover_amm_pools(client, first_block)
    active_v3 = [p for p in amm_res["uniswap_v3"] if p["active"]]
    print(f"  Found {len(active_v3)} active Uniswap V3 USDS pools:")
    for p in active_v3:
        print(f"    {p['pair']} {p['fee']}bps: {p['pool']} (liq={p['liquidity']})")

    overlay = build_inventory_overlay(amm_res)

    # Write output artifacts
    pinned_path = OUTPUT_DIR / "pinned_evidence.json"
    pinned_path.write_text(json.dumps(pin_results, indent=2) + "\n")
    print(f"Saved pinned evidence to {pinned_path}")

    amm_path = OUTPUT_DIR / "amm_discovery.json"
    amm_path.write_text(json.dumps(amm_res, indent=2) + "\n")
    print(f"Saved AMM discovery to {amm_path}")

    overlay_path = OUTPUT_DIR / "usds_inventory_overlay.json"
    overlay_path.write_text(json.dumps(overlay, indent=2) + "\n")
    print(f"Saved inventory overlay to {overlay_path}")


if __name__ == "__main__":
    main()
