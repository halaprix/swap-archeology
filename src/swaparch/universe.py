"""Lead-owned integration layer: inventory files -> PoolRecords -> block snapshot -> PoolStates.

This is the only place that ties discovery, snapshot acquisition and adapters together.
It performs RPC only through `acquire()`; `load_states()` is offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from swaparch.core.protocols import PoolState, Snapshot, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallSpec, PoolRecord, SupportStatus, Token
from swaparch.rpc.client import PROJECT_ROOT

DISCOVERY_ROOT = PROJECT_ROOT / "data/discovery"


class MakerSkyPsmAdapter:
    """Delegating adapter for Maker/Sky PSM family (LitePSM and DaiUsdsConverter)."""

    family = "maker_sky_psm"

    def __init__(self) -> None:
        from swaparch.adapters.litepsm import LitePsmAdapter
        from swaparch.adapters.usds import DaiUsdsAdapter

        self._litepsm = LitePsmAdapter()
        self._dai_usds = DaiUsdsAdapter()

    def _delegate(self, pool: PoolRecord) -> SourceAdapter:
        from swaparch.core.types import norm_address

        norm_pool = norm_address(pool.pool)
        if norm_pool == "0x3225737a9bbb6473cb4a45b7244aca2befdb276a" or pool.config.get("model") == "DaiUsdsConverter":
            return self._dai_usds
        if norm_pool == "0xf6e72db5454dd049d0788e411b06cfaf16853042" or pool.config.get("model") == "dss-lite-psm":
            return self._litepsm
        raise Unsupported(f"{pool.pool_id} is not supported by maker_sky_psm adapter")

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        return self._delegate(pool).read_requests(pool, block)

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        return self._delegate(pool).dependent_requests(pool, block, snapshot)

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> PoolState:
        return self._delegate(pool).load_state(pool, snapshot)


def implemented_adapters() -> dict[str, SourceAdapter]:
    """Explicit adapter inventory shared by CLI, benchmarks, and offline reports."""
    from swaparch.adapters.curve_legacy_3pool import CurveAdapter
    from swaparch.adapters.fluid_dex import FluidDexAdapter
    from swaparch.adapters.lido import LidoAdapter
    from swaparch.adapters.origin_arm import OriginArmAdapter
    from swaparch.adapters.pancake_v3 import PancakeV3Adapter
    from swaparch.adapters.uniswap_v2 import UniswapV2Adapter
    from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
    from swaparch.adapters.uniswap_v4 import UniswapV4Adapter

    return {adapter.family: adapter for adapter in
            (CurveAdapter(), LidoAdapter(), MakerSkyPsmAdapter(), OriginArmAdapter(),
             PancakeV3Adapter(), UniswapV2Adapter(), UniswapV3Adapter(), UniswapV4Adapter(),
             FluidDexAdapter())}


def pool_record_from_json(row: Mapping[str, Any]) -> PoolRecord:
    raw_status = row.get("status", "discovered_unsupported")
    try:
        status = SupportStatus(raw_status)
    except ValueError:
        status = SupportStatus.DISCOVERED_UNSUPPORTED

    return PoolRecord(
        family=row["family"],
        chain=int(row["chain"]),
        pool_id=row["pool_id"],
        deployment=row["deployment"],
        pool=row["pool"],
        tokens=tuple(
            Token(int(row["chain"]), t["address"], t["symbol"], int(t["decimals"]))
            for t in row["tokens"]
        ),
        config=dict(row.get("config", {})),
        created_block=row.get("created_block"),
        discovered_by=dict(row.get("discovered_by", {})),
        status=status,
        notes=row.get("notes", ""),
    )


def load_inventory(family: str, chain: int = 1, root: Path = DISCOVERY_ROOT) -> list[PoolRecord]:
    path = root / str(chain) / f"{family}.json"
    data = json.loads(path.read_text())
    return [pool_record_from_json(p) for p in data.get("pools", [])]


def pools_live_at(records: Iterable[PoolRecord], block_number: int) -> list[PoolRecord]:
    """Return records whose existence and configured initialization are established.

    A successful historical code/registry observation can prove a venue existed
    by a block without inventing its exact creation block. Earlier blocks remain
    unresolved, rather than being labelled not deployed.
    """
    return [r for r in records if activation_reason(r, block_number) is None]


def activation_reason(record: PoolRecord, block_number: int) -> str | None:
    if record.created_block is not None:
        if record.created_block > block_number:
            return "not_deployed_at_block"
    else:
        bound = record.discovered_by.get("deployed_by_block")
        if not (type(bound) is int and 0 <= bound <= block_number):
            return "activation not established at requested block"

    initialized = record.config.get("initialized_block")
    if type(initialized) is int and initialized > 0 and block_number < initialized:
        return "not_initialized_at_block"
    return None


def observe_singleton_activation(record: PoolRecord, block: BlockRef, client) -> PoolRecord | None:
    """Establish an earlier singleton observation before requesting its state."""
    reason = activation_reason(record, block.number)
    if reason is None:
        return record
    if reason != "activation not established at requested block":
        return None
    from swaparch.core.types import norm_address

    address = norm_address(record.pool)
    path = (PROJECT_ROOT / "data/discovery-evidence/activation" / str(block.chain)
            / block.hash / f"{address}.json")
    if path.exists():
        evidence = json.loads(path.read_text())
        if evidence["block_hash"] != block.hash or evidence["address"] != address:
            raise ValueError("singleton activation evidence identity mismatch")
    else:
        code = client._rpc("eth_getCode", [address, {"blockHash": block.hash,
                                                   "requireCanonical": True}])
        evidence = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
                    "address": address, "method": "eth_getCode", "code": code}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence) + "\n")
    if evidence["code"] == "0x":
        return None
    discovery = dict(record.discovered_by)
    discovery["deployed_by_block"] = block.number
    discovery["evidence"] = sorted(set(discovery.get("evidence", [])
                                       + [str(path.relative_to(PROJECT_ROOT))]))
    return replace(record, discovered_by=discovery)


@dataclass
class AcquireReport:
    block: BlockRef
    pools: int
    phases: int
    specs_total: int
    network_requests: int
    unsupported: dict[str, str] = field(default_factory=dict)


def acquire(adapters: Mapping[str, SourceAdapter], records: list[PoolRecord], block: BlockRef,
            store, client, max_phases: int = 6,
            prefetch_specs: Iterable[CallSpec] = ()) -> tuple[Snapshot, AcquireReport]:
    """Phase-1 independent reads, then dependent reads until every adapter is satisfied."""
    specs: list[CallSpec] = list(prefetch_specs)
    failures: dict[str, str] = {}
    for rec in records:
        activation = activation_reason(rec, block.number)
        if activation is not None:
            failures[rec.pool_id] = activation
            continue
        try:
            specs.extend(adapters[rec.family].read_requests(rec, block))
        except Unsupported as exc:
            failures[rec.pool_id] = str(exc)
    snapshot = store.extend(block, _dedupe(specs), client)
    total = len(specs)
    phases = 1
    while phases < max_phases:
        more: list[CallSpec] = []
        for rec in records:
            if rec.pool_id in failures:
                continue
            try:
                more.extend(adapters[rec.family].dependent_requests(rec, block, snapshot))
            except Unsupported as exc:
                failures[rec.pool_id] = str(exc)
        more = [s for s in _dedupe(more) if not snapshot.has(s)]
        if not more:
            break
        snapshot = store.extend(block, more, client)
        total += len(more)
        phases += 1
    return snapshot, AcquireReport(block, len(records), phases, total,
                                   getattr(client, "network_requests", -1), failures)


def load_states(adapters: Mapping[str, SourceAdapter], records: Iterable[PoolRecord],
                snapshot: Snapshot, acquisition_failures: Mapping[str, str] | None = None
                ) -> tuple[list[PoolState], list[tuple[PoolRecord, str]]]:
    states: list[PoolState] = []
    unsupported: list[tuple[PoolRecord, str]] = []
    for rec in records:
        try:
            if rec.pool_id in (acquisition_failures or {}):
                raise Unsupported(acquisition_failures[rec.pool_id])
            if rec.family not in adapters:
                raise Unsupported("adapter not implemented")
            if rec.status != SupportStatus.SUPPORTED:
                raise Unsupported(f"{rec.status.value}: {rec.notes}")
            if snapshot.block.hash not in rec.config.get("validated_block_hashes", []):
                raise Unsupported("no recorded quote validation at this block hash")
            states.append(adapters[rec.family].load_state(rec, snapshot))
        except Unsupported as exc:
            unsupported.append((rec, str(exc)))
    return states, unsupported


def _dedupe(specs: Iterable[CallSpec]) -> list[CallSpec]:
    seen: set[tuple[str, str]] = set()
    out: list[CallSpec] = []
    for s in specs:
        key = (s.to, s.data)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
