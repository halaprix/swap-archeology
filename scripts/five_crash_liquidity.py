"""Five Crash Liquidity Collection & Routing Engine.

Collects full liquidity-source data for all five selected Binance ETH crashes:
  1. crash-1 (2025-02-03, blocks 21762695..21762993, 299 blocks)
  2. crash-2 (2025-02-25, blocks 21921690..21921989, 300 blocks)
  3. crash-3 (2025-04-07, blocks 22215221..22215521, 301 blocks)
  4. crash-4 (2025-06-21, blocks 22755384..22755684, 301 blocks)
  5. crash-5 (2025-10-10, blocks 23549939..23550237, 299 blocks)

Key Architecture & Compliance:
- Isolated per-event inventory with code-verified activation at event window start.
- Truthful Aave V3 reference bound (Crash 1 block 21762695) with saved on-chain code proof.
- Full October routing universe: Fluid, UniV2, UniV3, UniV4 + Curve 3pool, WBTC pools,
  Pancake V3, DaiUsdsConverter, and active USDS AMM pools.
- Actual 3-call Curve metadata queries (registry get_coins/get_decimals, MetaRegistry get_base_pool).
- Exact October routing policy: crash_slices._run_quote under perf_native.optimized
  (SETTINGS 8 steps, 128 beam, 5000 expansions, 10 grid parts).
- Full check_report validation: full spend, zero residual, aggregate >= best direct.
- Preserves raw oracle multicall evidence alongside decoded values.
- Exactly matches frontend public JSON schemas: sources.json, oracle-references.json, index.json.
- Bounded concurrency (≤ 4 workers), resumable per-block checkpoints, fail-closed null logic.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import crash_slices
import october_source_prices
import perf_native
from curve_discovery_run import META as CURVE_META
from curve_discovery_run import spec as curve_spec
from curve_discovery_run import unpack as curve_unpack
from eval_quote_performance import check_report
from october_expansion_collect import (
    build_dai_usds_converter_record,
    build_pancake_admitted_records,
)
from recent_oracle_check import query_block_oracle_references
from usds_amm_collect import build_usds_amm_pool_records
from wbtc_inventory import build_wbtc_pool_records

from swaparch.adapters.curve_legacy_3pool import (
    POOL as CURVE_3POOL,
)
from swaparch.adapters.curve_legacy_3pool import (
    REGISTRY as CURVE_REGISTRY,
)
from swaparch.adapters.curve_legacy_3pool import (
    ZERO as CURVE_ZERO,
)
from swaparch.adapters.curve_legacy_3pool import (
    CurveLegacy3PoolAdapter,
)
from swaparch.adapters.pancake_v3 import PancakeV3Adapter
from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.collection import collect_block
from swaparch.collection_quotes import prepared_collection_context
from swaparch.collection_supplement import supplement_context
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, PoolRecord, norm_address
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import MakerSkyPsmAdapter, acquire, pool_record_from_json

OUT_DIR = PROJECT_ROOT / "outputs/five-crash-liquidity"
PROGRESS_LOG = OUT_DIR / "progress.log"
STATUS_JSON = OUT_DIR / "status.json"
INDEX_JSON = OUT_DIR / "index.json"
REPRESENTATIVE_CHECK_JSON = OUT_DIR / "representative-check.json"

_WORKER: dict[str, Any] = {}

SIZES = (1, 10, 100)
ASSETS = ("ETH", "WETH")

CRASH_SPECS: list[dict[str, Any]] = [
    {
        "id": "crash-1",
        "label": "2025-02-03",
        "dateUtc": "2025-02-03",
        "crashUtc": "2025-02-03T01:56:59Z",
        "crashTimestamp": 1738547819,
        "startBlock": 21762695,
        "endBlock": 21762993,
        "blockCount": 299,
        "startUtc": "2025-02-03T01:26:59Z",
        "endUtc": "2025-02-03T02:26:59Z",
        "representativeBlock": 21762844,
        "representativeTargetIndex": 72,
    },
    {
        "id": "crash-2",
        "label": "2025-02-25",
        "dateUtc": "2025-02-25",
        "crashUtc": "2025-02-25T07:25:59Z",
        "crashTimestamp": 1740468359,
        "startBlock": 21921690,
        "endBlock": 21921989,
        "blockCount": 300,
        "startUtc": "2025-02-25T06:55:59Z",
        "endUtc": "2025-02-25T07:55:59Z",
        "representativeBlock": 21921840,
        "representativeTargetIndex": 72,
    },
    {
        "id": "crash-3",
        "label": "2025-04-07",
        "dateUtc": "2025-04-07",
        "crashUtc": "2025-04-07T06:54:59Z",
        "crashTimestamp": 1744008899,
        "startBlock": 22215221,
        "endBlock": 22215521,
        "blockCount": 301,
        "startUtc": "2025-04-07T06:24:59Z",
        "endUtc": "2025-04-07T07:24:59Z",
        "representativeBlock": 22215371,
        "representativeTargetIndex": 72,
    },
    {
        "id": "crash-4",
        "label": "2025-06-21",
        "dateUtc": "2025-06-21",
        "crashUtc": "2025-06-21T21:31:59Z",
        "crashTimestamp": 1750541519,
        "startBlock": 22755384,
        "endBlock": 22755684,
        "blockCount": 301,
        "startUtc": "2025-06-21T21:01:59Z",
        "endUtc": "2025-06-21T22:01:59Z",
        "representativeBlock": 22755534,
        "representativeTargetIndex": 72,
    },
    {
        "id": "crash-5",
        "label": "2025-10-10",
        "dateUtc": "2025-10-10",
        "crashUtc": "2025-10-10T21:20:59Z",
        "crashTimestamp": 1760131259,
        "startBlock": 23549939,
        "endBlock": 23550237,
        "blockCount": 299,
        "startUtc": "2025-10-10T21:14:00Z",
        "endUtc": "2025-10-10T22:14:00Z",
        "representativeBlock": 23549973,
        "representativeTargetIndex": 72,
    },
]


def log_progress(msg: str) -> None:
    line = f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}\n"
    print(line, end="", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with PROGRESS_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def atomic_write_json(path: Path, payload: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".tmp-{os.getpid()}-{time.time_ns()}")
    content = json.dumps(payload, indent=indent) + "\n"
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_gzip_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".tmp-{os.getpid()}-{time.time_ns()}.gz")
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    tmp_path.replace(path)


def update_status(phase: str, details: dict[str, Any] | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    status_data = {
        "schemaVersion": 1,
        "phase": phase,
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "crashes": [
            {
                "id": c["id"],
                "label": c["label"],
                "dateUtc": c["dateUtc"],
                "crashUtc": c["crashUtc"],
                "startBlock": c["startBlock"],
                "endBlock": c["endBlock"],
                "blockCount": c["blockCount"],
                "startUtc": c["startUtc"],
                "endUtc": c["endUtc"],
                "status": c.get("status", "pending"),
            }
            for c in CRASH_SPECS
        ],
    }
    if details:
        status_data.update(details)
    atomic_write_json(STATUS_JSON, status_data)


def write_index_json() -> None:
    def actual_status(crash: dict[str, Any]) -> str:
        path = OUT_DIR / crash["id"] / "sources.json"
        if not path.is_file():
            return crash.get("status", "pending")
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
            found = {row["block"] for row in rows}
            expected = set(range(crash["startBlock"], crash["endBlock"] + 1))
            return "completed" if found == expected else "partial"
        except (OSError, ValueError, KeyError, TypeError):
            return "partial"

    index_data = {
        "schemaVersion": 1,
        "crashes": [
            {
                "id": c["id"],
                "label": c["label"],
                "crashUtc": c["crashUtc"],
                "startUtc": c["startUtc"],
                "endUtc": c["endUtc"],
                "status": actual_status(c),
                "sourcesUrl": f"/five-crash-liquidity/{c['id']}/sources.json",
                "oracleReferencesUrl": f"/five-crash-liquidity/{c['id']}/oracle-references.json",
                "csvUrl": f"/five-crash-liquidity/{c['id']}/sources.csv",
                "coverage": {
                    "startBlock": c["startBlock"],
                    "endBlock": c["endBlock"],
                    "blockCount": c["blockCount"],
                    "windowSeconds": 3600,
                    "windowDocumentation": (
                        "Window covers 1 hour centered at crash minute trough ±1800s (Crash 1-4) "
                        "or 21:14:00Z..22:14:00Z (Crash 5) preserving canonical October dense dataset."
                    ),
                },
            }
            for c in CRASH_SPECS
        ],
    }
    atomic_write_json(INDEX_JSON, index_data)


def _code_fingerprint(event_spec: dict[str, Any], inv_dir: Path, build: dict[str, Any]) -> str:
    """Identity for every input that can change a per-block result; never includes RPC configuration."""
    digest = hashlib.sha256()
    inputs = [
        Path(__file__), PROJECT_ROOT / "scripts/crash_slices.py", PROJECT_ROOT / "scripts/perf_native.py",
        PROJECT_ROOT / "scripts/october_source_prices.py", PROJECT_ROOT / "scripts/curve_discovery_run.py",
        PROJECT_ROOT / "scripts/eval_quote_performance.py", PROJECT_ROOT / "scripts/recent_oracle_check.py",
        PROJECT_ROOT / "scripts/october_expansion_collect.py", PROJECT_ROOT / "scripts/wbtc_inventory.py",
        PROJECT_ROOT / "scripts/usds_amm_collect.py", PROJECT_ROOT / "src/swaparch",
    ]
    for source in inputs:
        paths = [source] if source.is_file() else sorted(source.rglob("*.py"))
        for path in paths:
            digest.update(str(path.relative_to(PROJECT_ROOT)).encode())
            digest.update(path.read_bytes())
    for path in sorted(inv_dir.glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(json.dumps({
        "event": {key: event_spec[key] for key in ("id", "startBlock", "endBlock")},
        "assets": ASSETS, "sizes": SIZES, "solver": crash_slices.SETTINGS,
        "nativeBuild": build["manifest"],
    }, sort_keys=True, default=str).encode())
    return digest.hexdigest()


def prepare_event_inventory(event_spec: dict[str, Any], client: RpcClient) -> Path:
    """Prepares isolated historical inventory for the event window.

    Evaluated at the window's startBlock so that the entire window (startBlock..endBlock)
    is covered without prematurely gating the early half.
    Only checks eth_getCode for candidate pools with validated_block_hashes and deployed_by > startBlock.
    Preserves known created_block gating intact.
    """
    event_id = event_spec["id"]
    start_block_num = event_spec["startBlock"]
    start_block = client.get_block(start_block_num)

    inv_dir = OUT_DIR / "inventories" / event_id
    inv_dir.mkdir(parents=True, exist_ok=True)

    ev_dir = PROJECT_ROOT / "data/discovery-evidence/activation/1" / start_block.hash
    ev_dir.mkdir(parents=True, exist_ok=True)

    for p in sorted((PROJECT_ROOT / "data/discovery/1").glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        modified_pools = []
        for pool in data.get("pools", []):
            rec = dict(pool)
            if rec.get("config", {}).get("validated_block_hashes"):
                created = rec.get("created_block")
                deployed_by = rec.get("discovered_by", {}).get("deployed_by_block")
                # If created_block is later than window start, keep as is (activation_reason will exclude)
                if created is not None and created > start_block.number:
                    modified_pools.append(rec)
                    continue

                if deployed_by is not None and deployed_by > start_block.number:
                    addr = norm_address(rec["pool"])
                    ev_path = ev_dir / f"{addr.lower()}.json"
                    if ev_path.exists():
                        code = json.loads(ev_path.read_text())["code"]
                    else:
                        code = client._rpc(
                            "eth_getCode",
                            [addr, {"blockHash": start_block.hash, "requireCanonical": True}],
                        )
                        ev = {
                            "chain": start_block.chain,
                            "block": start_block.number,
                            "block_hash": start_block.hash,
                            "address": addr.lower(),
                            "method": "eth_getCode",
                            "code": code,
                        }
                        ev_path.write_text(json.dumps(ev, indent=2) + "\n")

                    if code != "0x":
                        disc = dict(rec.get("discovered_by", {}))
                        disc["deployed_by_block"] = start_block.number
                        disc["evidence"] = sorted(
                            set(
                                disc.get("evidence", [])
                                + [str(ev_path.relative_to(PROJECT_ROOT))]
                            )
                        )
                        rec["discovered_by"] = disc

            modified_pools.append(rec)

        data["pools"] = modified_pools
        (inv_dir / p.name).write_text(json.dumps(data, indent=2) + "\n")

    return inv_dir


def preflight_event_usds_pools(event_spec: dict[str, Any], client: RpcClient) -> list[PoolRecord]:
    """Preflights the 4 USDS AMM Uniswap V3 connector pools once at the event window start.

    Filters out pools that were not yet created/deployed at this event window,
    avoiding per-block getCode calls.
    Sets validated_block_hashes = [] for supplemental collection-only records.
    """
    start_block = client.get_block(event_spec["startBlock"])
    evidence_dir = PROJECT_ROOT / "data/discovery-evidence/activation/1" / start_block.hash
    evidence_dir.mkdir(parents=True, exist_ok=True)
    candidates = build_usds_amm_pool_records([])
    active_records = []

    for rec in candidates:
        if rec.created_block is not None and rec.created_block > start_block.number:
            continue
        code = client._rpc(
            "eth_getCode",
            [rec.pool, {"blockHash": start_block.hash, "requireCanonical": True}],
        )
        if code != "0x":
            evidence_path = evidence_dir / f"{rec.pool.lower()}.json"
            if not evidence_path.is_file():
                atomic_write_json(evidence_path, {
                    "chain": start_block.chain, "block": start_block.number,
                    "block_hash": start_block.hash, "address": rec.pool.lower(),
                    "method": "eth_getCode", "code": code,
                })
            mod_rec = replace(
                rec,
                config=dict(rec.config, validated_block_hashes=[]),
                discovered_by={
                    "method": "curated", "deployed_by_block": start_block.number,
                    "evidence": [str(evidence_path.relative_to(PROJECT_ROOT))],
                },
            )
            active_records.append(mod_rec)

    return active_records


def acquire_block_supplements(
    block: BlockRef,
    client: RpcClient,
    supp_dir: Path,
    active_usds_records: list[PoolRecord],
) -> list[PoolRecord]:
    """Acquires October supplements at the given block without fabricated metadata.

    - Curve legacy 3pool: Executes actual 3-call on-chain metadata queries (registry get_coins/get_decimals,
      MetaRegistry get_base_pool), decodes on-chain values, and loads state via CurveLegacy3PoolAdapter.
    - WBTC, Pancake V3, DaiUsdsConverter, USDS AMM: Acquired with validated_block_hashes = [].
    """
    store = SnapshotStore(supp_dir / "snapshots")

    # 1. WBTC records (12 pools) - validated_block_hashes = []
    wbtc_recs = [
        replace(r, config=dict(r.config, validated_block_hashes=[]))
        for r in build_wbtc_pool_records([])
    ]

    # 2. Pancake V3 records (3 pools) - validated_block_hashes = []
    pancake_recs = [
        replace(r, config=dict(r.config, validated_block_hashes=[]))
        for r in build_pancake_admitted_records([])
    ]

    # 3. DaiUsdsConverter (1:1 connector) - verified deployed at 21762695
    converter_rec = build_dai_usds_converter_record([])
    converter_rec = replace(
        converter_rec,
        config=dict(converter_rec.config, validated_block_hashes=[]),
        discovered_by={
            "method": "curated",
            "deployed_by_block": 21762695,
            "evidence": [
                "data/discovery-evidence/activation/1/0x5e2b0ad801f51565cad7f363cf103be37b3b36b39deb2cae26cf6b12ea23f424/0x3225737a9bbb6473cb4a45b7244aca2befdb276a.json"
            ],
        },
    )

    # 4. USDS AMM records (active for this event window)
    usds_recs = list(active_usds_records)

    # 5. Curve legacy 3pool: Real on-chain queries from october_connectors.collect
    identity = [
        curve_spec(CURVE_REGISTRY, "get_coins(address)", ["address"], [CURVE_3POOL]),
        curve_spec(CURVE_REGISTRY, "get_decimals(address)", ["address"], [CURVE_3POOL]),
        curve_spec(CURVE_META, "get_base_pool(address)", ["address"], [CURVE_3POOL]),
    ]
    meta_snapshot = store.extend(block, identity, client)
    coins = list(curve_unpack(meta_snapshot.get(identity[0]), "address[8]"))[:3]
    decimals = list(curve_unpack(meta_snapshot.get(identity[1]), "uint256[8]"))[:3]
    base = curve_unpack(meta_snapshot.get(identity[2]), "address")

    curve_template = next(
        r
        for r in json.loads(
            (PROJECT_ROOT / "data/discovery/1/curve.json").read_text()
        )["pools"]
        if r["pool"] == CURVE_3POOL
    )
    c_dict = copy.deepcopy(curve_template)
    assert coins == [t["address"] for t in c_dict["tokens"]]
    assert decimals == [18, 6, 6] and base == CURVE_ZERO

    c_dict["config"]["registry_observations"] = {
        block.hash: {
            "block": block.number,
            "coins": coins,
            "decimals": decimals,
            "base_pool": base,
            "base_registries": [CURVE_REGISTRY],
            "method": "direct canonical registry get_coins/get_decimals; MetaRegistry get_base_pool",
        }
    }
    c_dict["config"]["curve_legacy_3pool"] = True
    c_dict["config"]["transfer_semantics"] = {coin: "standard" for coin in coins}
    c_dict["config"]["validated_block_hashes"] = []
    c_dict["discovered_by"] = {
        "deployed_by_block": block.number,
        "evidence": [str(store.root / "1" / block.hash)],
    }
    c_dict["status"] = "supported"
    c_dict["notes"] = "Supplemental collection-model state; verified on-chain registry observations."
    curve_rec = pool_record_from_json(c_dict)

    all_supp_records = (
        wbtc_recs + pancake_recs + [converter_rec] + usds_recs + [curve_rec]
    )

    supp_adapters = {
        "uniswap_v3": UniswapV3Adapter(word_radius=8),
        "pancake_v3": PancakeV3Adapter(word_radius=8),
        "maker_sky_psm": MakerSkyPsmAdapter(),
        "curve": CurveLegacy3PoolAdapter(),
    }

    _snap, report = acquire(supp_adapters, all_supp_records, block, store, client)
    if report.unsupported:
        raise RuntimeError(
            f"Fail-closed: unexpected unsupported supplement pool(s) at {block.number}: {report.unsupported}"
        )

    rec_doc = {
        "blockHash": block.hash,
        "blockNumber": block.number,
        "timestamp": block.timestamp,
        "records": [
            {
                "family": r.family,
                "chain": r.chain,
                "pool_id": r.pool_id,
                "deployment": r.deployment,
                "pool": r.pool,
                "tokens": [
                    {
                        "chain": r.chain,
                        "address": t.address,
                        "symbol": t.symbol,
                        "decimals": t.decimals,
                    }
                    for t in r.tokens
                ],
                "config": dict(r.config),
                "created_block": r.created_block,
                "discovered_by": dict(r.discovered_by),
                "status": r.status.value,
                "notes": r.notes,
            }
            for r in all_supp_records
        ],
    }
    rec_path = supp_dir / "records" / f"{block.hash}.json"
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(json.dumps(rec_doc, indent=2) + "\n")

    return all_supp_records


def _fee_bps(state: Any) -> float | None:
    config = state.record.config
    if state.record.family == "uniswap_v2":
        num, den = config.get("fee_numerator"), config.get("fee_denominator")
        return (den - num) * 10_000 / den if isinstance(num, int) and isinstance(den, int) else 30.0
    if state.record.family == "fluid_dex":
        return None
    fee = config.get("fee")
    return fee / 100 if isinstance(fee, int) else None


def _pool_metadata(states: tuple[Any, ...]) -> list[dict[str, Any]]:
    pools = []
    for state in states:
        tokens = {token.symbol: token for token in state.tokens()}
        if "USDC" not in tokens:
            continue
        for asset in ASSETS:
            if asset in tokens:
                pools.append({
                    "id": state.record.pool_id,
                    "family": state.record.family,
                    "address": state.record.pool,
                    "inputSymbol": asset,
                    "feeBps": _fee_bps(state),
                    "label": f"{state.record.family}:{state.record.pool}",
                })
    return sorted(pools, key=lambda p: p["id"])


def _direct_quote(state: Any, asset: str, amount_wei: int) -> dict[str, Any]:
    tokens = {token.symbol: token for token in state.tokens()}
    try:
        amount_out = state.quote_exact_in(
            tokens[asset].address, tokens["USDC"].address, amount_wei
        )
        if amount_out <= 0:
            raise ValueError("non-positive direct output")
    except (Unsupported, ValueError, OverflowError) as exc:
        return {"price": None, "amountOut": None, "reason": str(exc)}

    price = float(
        Decimal(amount_out)
        / Decimal(10**6)
        / (Decimal(amount_wei) / Decimal(10**18))
    )
    return {"price": round(price, 6), "amountOut": str(amount_out)}


def _availability(context: Any, family_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    inventory = {item["family"]: item for item in context.inventories}
    direct_by_family = {fam: 0 for fam in inventory}
    for row in family_rows:
        direct_by_family[row["family"]] += 1
    return {
        fam: {
            "status": item["status"],
            "usableCount": sum(st.record.family == fam for st in context.states),
            "directCount": direct_by_family[fam],
        }
        for fam, item in sorted(inventory.items())
    }


def _candidate_floor(
    context: Any, annotation: dict[str, Any], asset: str, size: int, report: dict[str, Any]
) -> None:
    """Keep the October expanded incumbent only when it still evaluates on this exact state."""
    if context.block.number < october_source_prices.START or context.block.number > october_source_prices.END:
        return
    previous = october_source_prices.PREV_AGGREGATES / f"{context.block.hash.lower()}.json.gz"
    if not previous.is_file():
        return
    with gzip.open(previous, "rt", encoding="utf-8") as handle:
        incumbent = json.load(handle).get("reports", {}).get(f"{asset}:{size}")
    old_split = incumbent and incumbent.get("best_split")
    if not old_split or not old_split.get("feasible"):
        return
    old_out = int(old_split["amount_out"])
    current = report.get("best_split") or {}
    current_out = int(current["amount_out"]) if current.get("feasible") else 0
    if current_out >= old_out:
        return
    re_evaluated = october_source_prices._re_evaluate_candidate(
        context, annotation, asset, size, old_split
    )
    if re_evaluated and re_evaluated["amount_out"] >= old_out:
        report["best_split"] = re_evaluated
        return
    raise AssertionError(
        f"Candidate floor regressed at block {context.block.number} {asset}:{size}: {current_out} < {old_out}"
    )


def process_block(
    block_num: int,
    event_spec: dict[str, Any],
    client: RpcClient,
    multicall: Multicall3,
    build: dict[str, Any],
    inv_dir: Path,
    supp_dir: Path,
    collection_root: Path,
    active_usds_records: list[PoolRecord],
    event_out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Processes a single block end-to-end and returns (sources_row, oracle_row)."""
    block = client.get_block(block_num)

    # 1. Oracle references + raw call preservation
    oracle_row, raw_calls = query_block_oracle_references(multicall, block)
    raw_ev_path = event_out_dir / "raw-evidence" / f"{block.hash.lower()}.json"
    atomic_write_json(raw_ev_path, raw_calls)

    # 2. Base universe collection via collect_block
    rep = collect_block(
        block_num,
        inventory_root=inv_dir,
        client=client,
        output_root=collection_root,
    )

    # 3. Supplements acquisition
    acquire_block_supplements(block, client, supp_dir, active_usds_records)

    # 4. Supplemented context loading
    ctx, ann, _ = prepared_collection_context(block_num, root=collection_root / "1")
    ctx_supp, ann_supp = supplement_context(ctx, ann, supp_dir)

    # 5. Direct quotes
    direct_pools = _pool_metadata(ctx_supp.states)
    states_by_id = {st.record.pool_id: st for st in ctx_supp.states}
    pool_quotes: dict[str, dict[str, dict[str, Any]]] = {}

    for p_meta in direct_pools:
        p_id = p_meta["id"]
        st = states_by_id[p_id]
        asset = p_meta["inputSymbol"]
        pool_quotes[p_id] = {
            str(sz): _direct_quote(st, asset, sz * 10**18) for sz in SIZES
        }

    # Direct raw persistence
    raw_path = event_out_dir / "raw" / f"{block.hash.lower()}.json.gz"
    atomic_write_gzip_json(
        raw_path,
        {
            "schemaVersion": 1,
            "block": block.number,
            "blockHash": block.hash,
            "pools": direct_pools,
            "quotes": pool_quotes,
        },
    )

    # 6. Dense routing quotes using October policy (crash_slices._run_quote under perf_native)
    aggregates: dict[str, dict[str, float | None]] = {}
    aggregate_reasons: dict[str, dict[str, str | None]] = {}
    aggregate_refunds: dict[str, dict[str, Any]] = {}
    agg_reports: dict[str, Any] = {}

    with perf_native.optimized(ctx_supp, build):
        for asset in ASSETS:
            aggregates[asset] = {}
            aggregate_reasons[asset] = {}
            aggregate_refunds[asset] = {}

            for sz in SIZES:
                _code, report = crash_slices._run_quote(
                    ctx_supp,
                    ann_supp,
                    (asset, "USDC", str(sz)),
                    allow_psm_dai_refund=True,
                )
                _candidate_floor(ctx_supp, ann_supp, asset, sz, report)
                check_report(report)
                split = report.get("best_split")
                key = f"{asset}:{sz}"
                agg_reports[key] = report

                if split and split.get("feasible") and split.get("residual_in") == 0:
                    amount_out = int(split["amount_out"])
                    price = round(
                        float(
                            Decimal(amount_out)
                            / Decimal(10**6)
                            / Decimal(sz)
                        ),
                        6,
                    )
                    aggregates[asset][str(sz)] = price
                    aggregate_reasons[asset][str(sz)] = None
                    aggregate_refunds[asset][str(sz)] = split.get("terminal_refund", {})

                    # Verify aggregate >= best direct candidate
                    best_dir = 0.0
                    for p_meta in direct_pools:
                        if p_meta["inputSymbol"] == asset:
                            dq = pool_quotes[p_meta["id"]][str(sz)]
                            if dq["price"] is not None and dq["price"] > best_dir:
                                best_dir = dq["price"]

                    if price + 1e-9 < best_dir:
                        raise AssertionError(
                            f"Aggregate ({price}) below best direct ({best_dir}) for block {block_num} {asset} {sz}"
                        )
                else:
                    aggregates[asset][str(sz)] = None
                    aggregate_reasons[asset][str(sz)] = "no full-fill aggregate candidate"
                    aggregate_refunds[asset][str(sz)] = {}

    # Aggregate raw persistence
    agg_path = event_out_dir / "aggregate-raw" / f"{block.hash.lower()}.json.gz"
    atomic_write_gzip_json(
        agg_path,
        {
            "schemaVersion": 1,
            "block": block.number,
            "blockHash": block.hash,
            "reports": agg_reports,
        },
    )

    # Aave reference
    aave_price = None
    weth_ap = next((p for p in rep.get("aave_prices", []) if p["token"]["symbol"] == "WETH"), None)
    usdc_ap = next((p for p in rep.get("aave_prices", []) if p["token"]["symbol"] == "USDC"), None)
    if weth_ap and usdc_ap and weth_ap["available"] and usdc_ap["available"]:
        aave_price = round(weth_ap["price_base"] / usdc_ap["price_base"], 6)

    iso_timestamp = datetime.fromtimestamp(block.timestamp, UTC).isoformat().replace("+00:00", "Z")

    sources_row = {
        "block": block.number,
        "blockHash": block.hash,
        "timestamp": iso_timestamp,
        "chainlink": oracle_row["chainlink"]["price"],
        "aave": aave_price,
        "aggregates": aggregates,
        "aggregateRefunds": aggregate_refunds,
        "pools": pool_quotes,
        "poolMetadata": direct_pools,
        "availability": _availability(ctx_supp, direct_pools),
        "rawIndex": {
            "direct": str(raw_path.relative_to(PROJECT_ROOT)),
            "aggregates": str(agg_path.relative_to(PROJECT_ROOT)),
            "aggregateReasons": aggregate_reasons,
        },
    }

    oracle_reference_row = {
        "block": block.number,
        "blockHash": block.hash,
        "timestamp": iso_timestamp,
        "values": {
            "oneinch_spot": {
                "price": oracle_row["oneinch"]["price"],
                "status": oracle_row["oneinch"]["status"],
            },
            "uniswap_v3_twap_60": {
                "price": oracle_row["univ3_twap_60"]["price"],
                "status": oracle_row["univ3_twap_60"]["status"],
            },
            "uniswap_v3_twap_300": {
                "price": oracle_row["univ3_twap_300"]["price"],
                "status": oracle_row["univ3_twap_300"]["status"],
            },
        },
    }

    return sources_row, oracle_reference_row


def _init_worker(config: dict[str, Any]) -> None:
    """Each process owns its RPC and Multicall clients; workers never share a cache writer for one block."""
    client = RpcClient()
    _WORKER.update(config)
    _WORKER["client"] = client
    _WORKER["multicall"] = Multicall3(client)
    _WORKER["build"] = perf_native.load_build(perf_native.DEFAULT_BUILD)


def _process_worker(block_num: int) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    started = time.perf_counter()
    cfg = _WORKER
    source, oracle = process_block(
        block_num, cfg["event_spec"], cfg["client"], cfg["multicall"], cfg["build"],
        Path(cfg["inv_dir"]), Path(cfg["supp_dir"]), Path(cfg["collection_root"]),
        cfg["active_usds"], Path(cfg["event_out_dir"]),
    )
    return source, oracle, round((time.perf_counter() - started) * 1000), os.getpid()


def _checkpoint_record_is_valid(
    record: dict[str, Any], fingerprint: str, event_out_dir: Path
) -> bool:
    source, oracle = record.get("source"), record.get("oracle")
    if record.get("fingerprint") != fingerprint or not isinstance(source, dict) or not isinstance(oracle, dict):
        return False
    if source.get("block") != oracle.get("block") or source.get("blockHash") != oracle.get("blockHash"):
        return False
    raw = source.get("rawIndex", {})
    expected_hashes = record.get("artifactHashes")
    if not isinstance(expected_hashes, dict):
        return False
    paths = {
        "direct": event_out_dir / "raw" / f"{source['blockHash'].lower()}.json.gz",
        "aggregates": event_out_dir / "aggregate-raw" / f"{source['blockHash'].lower()}.json.gz",
        "oracle": event_out_dir / "raw-evidence" / f"{source['blockHash'].lower()}.json",
    }
    return (
        isinstance(source.get("poolMetadata"), list)
        and all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes.get(key) for key, path in paths.items())
        and isinstance(raw.get("aggregateReasons"), dict)
    )


def _artifact_hashes(source: dict[str, Any], event_out_dir: Path) -> dict[str, str]:
    block_hash = source["blockHash"].lower()
    paths = {
        "direct": event_out_dir / "raw" / f"{block_hash}.json.gz",
        "aggregates": event_out_dir / "aggregate-raw" / f"{block_hash}.json.gz",
        "oracle": event_out_dir / "raw-evidence" / f"{block_hash}.json",
    }
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}


def _self_check() -> None:
    """Small local check for the resumable/export contracts; deliberately makes no RPC calls."""
    assert list(map(str, SIZES)) == ["1", "10", "100"]
    assert {c["id"] for c in CRASH_SPECS} == {"crash-1", "crash-2", "crash-3", "crash-4", "crash-5"}
    for crash in CRASH_SPECS:
        assert crash["endBlock"] - crash["startBlock"] + 1 == crash["blockCount"]


def run_representative_check() -> dict[str, Any]:
    """Runs the ONE representative full 6-case block per date across all 5 events.

    Verifies end-to-end:
    - Real on-chain Curve 3pool metadata calls
    - Active USDS preflight and DaiUsdsConverter activation
    - crash_slices._run_quote under perf_native with SETTINGS (8 steps, 128 beam)
    - Full check_report, zero residual, aggregate >= best direct
    - Raw oracle evidence preservation
    - Output schema compatibility
    """
    log_progress("Running representative 6-case verification check across all 5 crash events...")
    update_status("representative_check_running")

    client = RpcClient()
    multicall = Multicall3(client)
    build = perf_native.load_build(perf_native.DEFAULT_BUILD)

    check_results = []

    for event_spec in CRASH_SPECS:
        event_id = event_spec["id"]
        rep_block_num = event_spec["representativeBlock"]
        log_progress(f"--- [Rep Check] {event_id} at block {rep_block_num} ({event_spec['dateUtc']}) ---")
        t0 = time.perf_counter()

        inv_dir = prepare_event_inventory(event_spec, client)
        active_usds = preflight_event_usds_pools(event_spec, client)
        event_out_dir = OUT_DIR / event_id
        supp_dir = OUT_DIR / "supplements"
        collection_root = OUT_DIR / "collection"

        s_row, o_row = process_block(
            rep_block_num,
            event_spec,
            client,
            multicall,
            build,
            inv_dir,
            supp_dir,
            collection_root,
            active_usds,
            event_out_dir,
        )
        elapsed = time.perf_counter() - t0

        log_progress(
            f"[{event_id}] Rep block {rep_block_num} completed in {elapsed:.2f}s: "
            f"Chainlink={s_row['chainlink']} | Aave={s_row['aave']} | "
            f"WETH:1={s_row['aggregates']['WETH']['1']} | "
            f"WETH:100={s_row['aggregates']['WETH']['100']} | "
            f"ETH:1={s_row['aggregates']['ETH']['1']} | "
            f"ETH:100={s_row['aggregates']['ETH']['100']}"
        )

        check_results.append({
            "id": event_id,
            "dateUtc": event_spec["dateUtc"],
            "block": rep_block_num,
            "elapsedSeconds": round(elapsed, 3),
            "sourcesRow": s_row,
            "oracleRow": o_row,
        })

    payload = {
        "schemaVersion": 1,
        "verifiedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "representativeBlocksCount": len(check_results),
        "results": check_results,
    }

    atomic_write_json(REPRESENTATIVE_CHECK_JSON, payload)
    write_index_json()
    update_status(
        "representative_check_completed",
        {"representativeCheckFile": str(REPRESENTATIVE_CHECK_JSON.relative_to(PROJECT_ROOT))},
    )
    log_progress(f"Representative verification check passed! Saved to {REPRESENTATIVE_CHECK_JSON}")
    return payload


def run_dense_event(
    event_spec: dict[str, Any],
    client: RpcClient,
    multicall: Multicall3,
    build: dict[str, Any],
    blocks_subset: list[int] | None = None,
    workers: int = 4,
) -> None:
    """Executes dense collection and quoting across all blocks in an event window."""
    started = time.perf_counter()
    event_id = event_spec["id"]
    start_b = event_spec["startBlock"]
    end_b = event_spec["endBlock"]
    wanted_blocks = blocks_subset if blocks_subset is not None else list(range(start_b, end_b + 1))
    if not wanted_blocks:
        return
    if any(block < start_b or block > end_b for block in wanted_blocks):
        raise ValueError(f"Requested blocks are outside {event_id}'s bounds {start_b}..{end_b}")
    wanted_blocks = sorted(set(wanted_blocks))

    log_progress(f"Starting dense collection for {event_id} ({len(wanted_blocks)} blocks: {wanted_blocks[0]}..{wanted_blocks[-1]})...")

    event_out_dir = OUT_DIR / event_id
    inv_dir = OUT_DIR / "inventories" / event_id
    if not any(inv_dir.glob("*.json")):
        inv_dir = prepare_event_inventory(event_spec, client)
    supp_dir = OUT_DIR / "supplements"
    collection_root = OUT_DIR / "collection"

    checkpoint_path = event_out_dir / "checkpoint.json"
    fingerprint = _code_fingerprint(event_spec, inv_dir, build)
    completed_blocks: dict[int, dict[str, Any]] = {}

    if checkpoint_path.is_file():
        try:
            cp_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            for key, record in cp_data.get("blocks", {}).items():
                block = int(key)
                if block in wanted_blocks and _checkpoint_record_is_valid(record, fingerprint, event_out_dir):
                    completed_blocks[block] = record
            if completed_blocks:
                log_progress(f"Resumed {len(completed_blocks)} validated block records from checkpoint.")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            log_progress(f"Ignoring invalid checkpoint {checkpoint_path}: {exc}")

    pending = [block for block in wanted_blocks if block not in completed_blocks]
    if pending:
        active_usds = preflight_event_usds_pools(event_spec, client)
        config = {
            "event_spec": event_spec, "inv_dir": str(inv_dir), "supp_dir": str(supp_dir),
            "collection_root": str(collection_root), "active_usds": active_usds,
            "event_out_dir": str(event_out_dir),
        }
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(config,)) as executor:
            futures = {executor.submit(_process_worker, block): block for block in pending}
            for index, future in enumerate(as_completed(futures), start=1):
                source, oracle, elapsed_ms, pid = future.result()
                block = source["block"]
                completed_blocks[block] = {
                    "fingerprint": fingerprint, "source": source, "oracle": oracle,
                    "artifactHashes": _artifact_hashes(source, event_out_dir),
                }
                atomic_write_json(checkpoint_path, {
                    "schemaVersion": 2, "fingerprint": fingerprint, "event": event_id,
                    "requestedBlocks": wanted_blocks, "completed": len(completed_blocks),
                    "total": len(wanted_blocks), "lastBlock": block,
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "blocks": {str(key): completed_blocks[key] for key in sorted(completed_blocks)},
                })
                log_progress(
                    f"[{event_id}] Block {block} ({index}/{len(pending)} new; {len(completed_blocks)}/{len(wanted_blocks)} total) "
                    f"pid={pid} elapsed={elapsed_ms / 1000:.2f}s WETH:1={source['aggregates']['WETH']['1']}"
                )

    sources_rows = [completed_blocks[block]["source"] for block in wanted_blocks]
    oracle_rows = [completed_blocks[block]["oracle"] for block in wanted_blocks]

    # Sort rows strictly by block
    sources_rows.sort(key=lambda r: r["block"])
    oracle_rows.sort(key=lambda r: r["block"])

    # Extract direct pool catalog
    pools_catalog = {}
    for r in sources_rows:
        for pool in r["poolMetadata"]:
            pools_catalog.setdefault(pool["id"], pool)

    sources_doc = {
        "schemaVersion": 1,
        "scope": {
            "startBlock": sources_rows[0]["block"],
            "endBlock": sources_rows[-1]["block"],
            "startUtc": sources_rows[0]["timestamp"],
            "endUtc": sources_rows[-1]["timestamp"],
            "stride": 1,
            "sizes": list(map(str, SIZES)),
        },
        "pools": sorted(pools_catalog.values(), key=lambda p: p["id"]),
        "families": [{"id": f, "label": f} for f in sources_rows[0]["availability"]],
        "rows": sources_rows,
        "qualification": (
            "direct per-pool model quotes and candidate routed quotes from frozen offline discovery state; "
            "known model-qualified pool coverage only (does not claim exhaustive Ethereum liquidity); not family-optimal routing"
        ),
        "fingerprint": fingerprint,
        "runtimeSeconds": round(time.perf_counter() - started, 6),
    }
    atomic_write_json(event_out_dir / "sources.json", sources_doc)

    oracle_doc = {
        "schemaVersion": 1,
        "sources": [
            {
                "id": "oneinch_spot",
                "label": "1inch Spot (OffchainOracle)",
                "kind": "spot",
                "description": "Liquidity-weighted DEX spot price from canonical 1inch OffchainOracle (getRate WETH/USDC, useWrappers=false)",
                "address": "0x00000000000D6FFc74A8feb35aF5827bf57f6786",
            },
            {
                "id": "uniswap_v3_twap_60",
                "label": "Uniswap V3 TWAP (60s)",
                "kind": "twap",
                "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (60-second window)",
                "windowSeconds": 60,
                "pool": "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
            },
            {
                "id": "uniswap_v3_twap_300",
                "label": "Uniswap V3 TWAP (300s)",
                "kind": "twap",
                "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (300-second window, default frontend comparison)",
                "windowSeconds": 300,
                "pool": "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
            },
        ],
        "rows": oracle_rows,
    }
    atomic_write_json(event_out_dir / "oracle-references.json", oracle_doc)

    # Write sources.csv
    csv_path = event_out_dir / "sources.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=("block", "blockHash", "timestamp", "poolId", "size", "price", "amountOut", "reason"),
        )
        writer.writeheader()
        for r in sources_rows:
            for p_id, quotes in r["pools"].items():
                for sz, q in quotes.items():
                    writer.writerow({
                        "block": r["block"],
                        "blockHash": r["blockHash"],
                        "timestamp": r["timestamp"],
                        "poolId": p_id,
                        "size": sz,
                        "price": q["price"],
                        "amountOut": q["amountOut"],
                        "reason": q.get("reason"),
                    })

    full_window = wanted_blocks == list(range(start_b, end_b + 1))
    event_spec["status"] = "completed" if full_window else "partial"
    write_index_json()
    update_status("dense_collection_in_progress", {"lastCompletedCrash": event_id})
    log_progress(f"Dense collection complete for {event_id} ({len(sources_rows)} blocks).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-representative",
        action="store_true",
        help="Run representative full 6-case block per date across all 5 events and verify",
    )
    parser.add_argument(
        "--crash",
        type=str,
        default=None,
        help="Crash ID to collect (e.g. crash-1, crash-2, etc. or 'all')",
    )
    parser.add_argument(
        "--blocks",
        type=str,
        default=None,
        help="Optional comma-separated list of blocks to process",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of bounded worker processes (default: 4)",
    )
    parser.add_argument("--self-check", action="store_true", help="Run local cache and catalog invariants without RPC")
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        return 0

    if args.check_representative:
        run_representative_check()
        return 0

    client = RpcClient()
    multicall = Multicall3(client)
    build = perf_native.load_build(perf_native.DEFAULT_BUILD)

    blocks_subset = [int(b.strip()) for b in args.blocks.split(",")] if args.blocks else None

    if args.blocks and (not args.crash or args.crash == "all" or "," in args.crash):
        raise ValueError("--blocks requires one explicit --crash ID")

    if not 1 <= args.workers <= 4:
        raise ValueError("--workers must be between 1 and 4")
    target_crashes = CRASH_SPECS
    if args.crash and args.crash != "all":
        wanted_ids = {item.strip() for item in args.crash.split(",") if item.strip()}
        target_crashes = [c for c in CRASH_SPECS if c["id"] in wanted_ids]
        if not target_crashes:
            raise ValueError(f"Unknown crash ID: {args.crash}")
        if wanted_ids != {c["id"] for c in target_crashes}:
            raise ValueError(f"Unknown crash ID(s): {sorted(wanted_ids - {c['id'] for c in target_crashes})}")

    update_status("dense_collection_started")
    for event_spec in target_crashes:
        subset = blocks_subset
        if blocks_subset is not None:
            subset = [block for block in blocks_subset if event_spec["startBlock"] <= block <= event_spec["endBlock"]]
            if not subset:
                continue
        run_dense_event(event_spec, client, multicall, build, blocks_subset=subset, workers=args.workers)

    update_status("dense_collection_completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
