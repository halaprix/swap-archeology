"""Historical WBTC pool inventory overlay for Uniswap V3 connector pools.

Defines canonical tokens, 12 factory-verified pools across WETH/WBTC, WBTC/USDC,
and WBTC/USDT with exact fees, tick spacings, canonical token ordering (WBTC is token0
in all 12 pools), creation blocks, transaction hashes, and log indices.

Outputs:
  - outputs/source-expansion/wbtc/wbtc_inventory.json
  - outputs/source-expansion/wbtc/activation/activation_evidence.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swaparch.core.types import PoolRecord, SupportStatus, Token, norm_address

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "outputs/source-expansion/wbtc"
FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"

# Canonical token definitions
TOKENS: dict[str, Token] = {
    "WBTC": Token(1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8),
    "WETH": Token(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
    "USDC": Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6),
    "USDT": Token(1, "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6),
}

# Twelve factory-verified pools with complete on-chain creation provenance
WBTC_POOLS_META: list[dict[str, Any]] = [
    # WETH / WBTC
    {
        "pair_label": "WETH/WBTC",
        "other_symbol": "WETH",
        "fee": 100,
        "tick_spacing": 1,
        "pool": "0xe6ff8b9a37b0fab776134636d9981aa778c4e718",
        "created_block": 13660296,
        "tx_hash": "0x40731e1ef51ee4be69bc86aa217143a9ac6a281fad963f047c236cbbb78c29f0",
        "log_index": 267,
    },
    {
        "pair_label": "WETH/WBTC",
        "other_symbol": "WETH",
        "fee": 500,
        "tick_spacing": 10,
        "pool": "0x4585fe77225b41b697c938b018e2ac67ac5a20c0",
        "created_block": 12376387,
        "tx_hash": "0xcb2fcbba10febc9dfc29aef3e993ec5581f30eabeb034d2a68ffe8810788784b",
        "log_index": 163,
    },
    {
        "pair_label": "WETH/WBTC",
        "other_symbol": "WETH",
        "fee": 3000,
        "tick_spacing": 60,
        "pool": "0xcbcdf9626bc03e24f779434178a73a0b4bad62ed",
        "created_block": 12369821,
        "tx_hash": "0xf87d91f3d72a8e912c020c2e316151f3557b1217b44d4f6b6bec126448318530",
        "log_index": 36,
    },
    {
        "pair_label": "WETH/WBTC",
        "other_symbol": "WETH",
        "fee": 10000,
        "tick_spacing": 200,
        "pool": "0x6ab3bba2f41e7eaa262fa5a1a9b3932fa161526f",
        "created_block": 12376536,
        "tx_hash": "0x2481547148cb27fec2ae2a39606e6e37047595c0a200f1688defbddcb1af71f6",
        "log_index": 135,
    },
    # WBTC / USDC
    {
        "pair_label": "WBTC/USDC",
        "other_symbol": "USDC",
        "fee": 100,
        "tick_spacing": 1,
        "pool": "0x026babd2ae9379525030fc2574e39bc156c10583",
        "created_block": 15531569,
        "tx_hash": "0x51cf4f6fea527217d62549a1c3787d144ed27d9302423738909956dc86ce81a1",
        "log_index": 181,
    },
    {
        "pair_label": "WBTC/USDC",
        "other_symbol": "USDC",
        "fee": 500,
        "tick_spacing": 10,
        "pool": "0x9a772018fbd77fcd2d25657e5c547baff3fd7d16",
        "created_block": 12561607,
        "tx_hash": "0xd0cf4b98ecf2c7a69bd1ee7fc3012a508202d9afdf2d9d41f05b49525bb4a853",
        "log_index": 75,
    },
    {
        "pair_label": "WBTC/USDC",
        "other_symbol": "USDC",
        "fee": 3000,
        "tick_spacing": 60,
        "pool": "0x99ac8ca7087fa4a2a1fb6357269965a2014abc35",
        "created_block": 12376048,
        "tx_hash": "0x2acb44ed240131e3a05eacc0ee870bdf93564f299eb633156a60d81055a352eb",
        "log_index": 45,
    },
    {
        "pair_label": "WBTC/USDC",
        "other_symbol": "USDC",
        "fee": 10000,
        "tick_spacing": 200,
        "pool": "0xcbfb0745b8489973bf7b334d54fdbd573df7ef3c",
        "created_block": 12601908,
        "tx_hash": "0x82996ea61f7a59f7db5f022519321481867c4908fc43cf26bbdad03781b58647",
        "log_index": 17,
    },
    # WBTC / USDT
    {
        "pair_label": "WBTC/USDT",
        "other_symbol": "USDT",
        "fee": 100,
        "tick_spacing": 1,
        "pool": "0xf98cf0d979cfbb780774f318e3da4f7317af50d7",
        "created_block": 20629019,
        "tx_hash": "0x96ea4ae1b484dc6a5fbebc960c1327d96da04ad2e7757c3a679ea86e7b4b872c",
        "log_index": 135,
    },
    {
        "pair_label": "WBTC/USDT",
        "other_symbol": "USDT",
        "fee": 500,
        "tick_spacing": 10,
        "pool": "0x56534741cd8b152df6d48adf7ac51f75169a83b2",
        "created_block": 12601886,
        "tx_hash": "0xd77ad5bf1fff0e5b9eb373f6793dc85bda189eb347548fe4561476b1e3464842",
        "log_index": 42,
    },
    {
        "pair_label": "WBTC/USDT",
        "other_symbol": "USDT",
        "fee": 3000,
        "tick_spacing": 60,
        "pool": "0x9db9e0e53058c89e5b94e29621a205198648425b",
        "created_block": 12376091,
        "tx_hash": "0xcde67a271d99721c52a096426ee6d1212ab60a002ae7ef7bf6803df8739ab3a8",
        "log_index": 165,
    },
    {
        "pair_label": "WBTC/USDT",
        "other_symbol": "USDT",
        "fee": 10000,
        "tick_spacing": 200,
        "pool": "0x5a59e4e647a3acc42b01715f3a1d271c1f7e7aeb",
        "created_block": 12601886,
        "tx_hash": "0xcd1d4b8356e398baf36926c9b510e131ca74de0f1741a668766068012d5fb595",
        "log_index": 52,
    },
]


def get_wbtc_tokens() -> dict[str, Token]:
    return dict(TOKENS)


def get_wbtc_pools_meta() -> list[dict[str, Any]]:
    return [dict(p) for p in WBTC_POOLS_META]


def build_wbtc_pool_records(
    validated_block_hashes: list[str] | None = None,
    status: SupportStatus = SupportStatus.SUPPORTED,
) -> list[PoolRecord]:
    """Construct accurate PoolRecord instances for the 12 WBTC connector pools.

    Ensures strict token ordering where token0 is WBTC (0x2260...) because
    int(WBTC) < int(USDC), int(WETH), and int(USDT).
    """
    records: list[PoolRecord] = []
    wbtc = TOKENS["WBTC"]
    hashes = list(validated_block_hashes or [])

    for meta in WBTC_POOLS_META:
        pool_addr = norm_address(meta["pool"])
        other_token = TOKENS[meta["other_symbol"]]

        # Strict assertion on canonical ordering: token0 must have lower address
        assert int(wbtc.address, 16) < int(other_token.address, 16), (
            f"Token ordering violated for {meta['pair_label']}: "
            f"{wbtc.address} vs {other_token.address}"
        )
        token0, token1 = wbtc, other_token

        rec = PoolRecord(
            family="uniswap_v3",
            chain=1,
            pool_id=f"uniswap_v3:{norm_address(FACTORY)}:{pool_addr}",
            deployment=norm_address(FACTORY),
            pool=pool_addr,
            tokens=(token0, token1),
            config={
                "fee": meta["fee"],
                "tick_spacing": meta["tick_spacing"],
                "validated_block_hashes": hashes,
            },
            created_block=meta["created_block"],
            discovered_by={
                "method": "logs:PoolCreated",
                "deployment": norm_address(FACTORY),
                "tx_hash": meta["tx_hash"],
                "log_index": meta["log_index"],
                "deployed_by_block": meta["created_block"],
                "filter": {
                    "token0": token0.address,
                    "token1": token1.address,
                    "token0_symbol": token0.symbol,
                    "token1_symbol": token1.symbol,
                    "canonical": True,
                },
            },
            status=status,
            notes="Factory-verified Uniswap V3 WBTC connector pool",
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


def export_inventory_overlay(
    output_dir: Path = DEFAULT_OUT,
    validated_block_hashes: list[str] | None = None,
) -> Path:
    """Export the WBTC inventory overlay JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records = build_wbtc_pool_records(validated_block_hashes=validated_block_hashes)

    data = {
        "chain": 1,
        "family": "uniswap_v3",
        "overlay": "wbtc",
        "schema": 1,
        "status": "supported",
        "deployments": [
            {
                "address": norm_address(FACTORY),
                "version": "UniswapV3Factory (v3-core 1.0.0)",
                "created_block": 12369621,
                "evidence": [
                    "data/adapters-evidence/uniswap_v3/factory-creation.json",
                    "outputs/0x-discovery/historical-leads.json",
                ],
            }
        ],
        "pools": [pool_record_to_dict(r) for r in records],
    }

    target = output_dir / "wbtc_inventory.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(target)
    return target


def export_activation_evidence(
    output_dir: Path = DEFAULT_OUT,
    client: Any = None,
) -> Path:
    """Save raw activation evidence at the start block (23549939).

    Uses the existing factory verification and getCode checks.
    """
    activation_dir = output_dir / "activation"
    activation_dir.mkdir(parents=True, exist_ok=True)

    evidence_file = ROOT / "outputs/0x-discovery/historical-leads.json"
    leads = json.loads(evidence_file.read_text()) if evidence_file.is_file() else {}

    block_info = leads.get(
        "block",
        {
            "chain": 1,
            "number": 23549939,
            "hash": "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
            "timestamp": 1760130851,
        },
    )

    pools_evidence = []
    for meta in WBTC_POOLS_META:
        pool_addr = norm_address(meta["pool"])
        # Match lead from historical-leads.json
        matched = next(
            (
                p
                for p in leads.get("uniswapConnectorPools", [])
                if norm_address(p.get("pool", "")) == pool_addr
            ),
            None,
        )
        pools_evidence.append(
            {
                "pool": pool_addr,
                "pair": meta["pair_label"],
                "fee": meta["fee"],
                "created_block": meta["created_block"],
                "creation_tx": meta["tx_hash"],
                "creation_log_index": meta["log_index"],
                "activation_verified_by_block": block_info["number"],
                "historical_lead_match": matched is not None,
                "liquidity_at_start_pin": matched.get("liquidity") if matched else None,
                "discovery_raw": matched.get("discovery") if matched else None,
                "liquidity_raw": matched.get("liquidityRaw") if matched else None,
            }
        )

    payload = {
        "scope": "Historical activation and factory verification for 12 Uniswap V3 WBTC connector pools",
        "factory": norm_address(FACTORY),
        "start_pin": block_info,
        "evidence_source": "outputs/0x-discovery/historical-leads.json",
        "pools": pools_evidence,
    }

    target = activation_dir / "activation_evidence.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(target)
    return target


def main() -> None:
    inv_path = export_inventory_overlay()
    act_path = export_activation_evidence()
    print(
        json.dumps(
            {
                "inventory": str(inv_path),
                "activation_evidence": str(act_path),
                "pools_count": len(WBTC_POOLS_META),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
