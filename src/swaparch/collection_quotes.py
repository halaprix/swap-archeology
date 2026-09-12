"""Offline quote contexts reconstructed from state-collection artifacts.

These contexts are deliberately distinct from the normal exact-hash-qualified
inventory path.  A successful state load is useful research evidence, but is
not an independent quote qualification.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import PROJECT_ROOT
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import implemented_adapters, pool_record_from_json

COLLECTION_ROOT = PROJECT_ROOT / "data/collection/1"
HEADER_ROOT = PROJECT_ROOT / "data/rpc-cache/headers/1"
_FILE_DIGESTS: dict[Path, tuple[int, int, str]] = {}
_COLLECTION_INPUTS: OrderedDict[Path, tuple[tuple[tuple[int, int], ...], dict[str, Any], str, str]] = OrderedDict()
_CONTEXTS: OrderedDict[tuple[str, tuple[str, ...]], tuple[object, dict[str, Any]]] = OrderedDict()


def _read(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt") as handle:
        return json.load(handle)


def collection_report_path(number: int, root: Path = COLLECTION_ROOT) -> Path:
    """Find the one cached collection report for ``number`` without RPC."""
    header = HEADER_ROOT / f"{number}.json"
    try:
        block_hash = str(json.loads(header.read_text())["response"]["result"]["hash"]).lower()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cached canonical header is missing or malformed for block {number}") from exc
    candidate = root / f"{block_hash}.json.gz"
    if not candidate.is_file():
        raise ValueError(f"collection report is missing for block {number}")
    return candidate


def collection_artifact_identity(number: int, root: Path = COLLECTION_ROOT) -> str:
    _report, identity, _report_digest = _collection_inputs(number, root)
    return identity


def _file_digest(path: Path) -> str:
    stat = path.stat()
    cached = _FILE_DIGESTS.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _FILE_DIGESTS[path] = (stat.st_mtime_ns, stat.st_size, digest)
    return digest


def _collection_inputs(number: int, root: Path) -> tuple[dict[str, Any], str, str]:
    path = collection_report_path(number, root)
    report_stat = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = _COLLECTION_INPUTS.get(path)
    report = cached[1] if cached and cached[0][0] == report_stat else _read(path)
    block = report.get("block", {})
    archive = Path(report.get("inventory_evidence", ""))
    snapshot = Path(report.get("snapshot", ""))
    header = HEADER_ROOT / f"{number}.json"
    paths = (path, archive, snapshot / "header.json", snapshot / "calls.json.gz", header)
    try:
        signature = tuple((item.stat().st_mtime_ns, item.stat().st_size) for item in paths)
    except OSError as exc:
        raise ValueError("collection evidence is missing") from exc
    if cached and cached[0] == signature:
        _COLLECTION_INPUTS.move_to_end(path)
        return cached[1], cached[2], _file_digest(path)
    canonical = json.loads(header.read_text())["response"]["result"]
    if (canonical.get("hash", "").lower() != str(block.get("hash", "")).lower()
            or int(canonical.get("number", "0x0"), 16) != number
            or int(canonical.get("timestamp", "0x0"), 16) != int(block.get("timestamp", -1))):
        raise ValueError("collection report and cached canonical header disagree")
    identity = hashlib.sha256("".join(_file_digest(item) for item in paths).encode()).hexdigest()
    _COLLECTION_INPUTS[path] = (signature, report, identity, _file_digest(path))
    _COLLECTION_INPUTS.move_to_end(path)
    while len(_COLLECTION_INPUTS) > 8:
        _COLLECTION_INPUTS.popitem(last=False)
    return report, identity, _file_digest(path)


def prepared_collection_context(number: int, selected_families: set[str] | None = None, *,
                                root: Path = COLLECTION_ROOT):
    """Return a state-loaded context and report annotations, with no RPC client."""
    # Imported here so normal CLI imports do not depend on this optional mode.
    from swaparch.cli import PreparedQuoteContext

    report_path = collection_report_path(number, root)
    report, collection_identity, artifact_digest = _collection_inputs(number, root)
    cache_key = (collection_identity, tuple(sorted(selected_families or ())))
    cached = _CONTEXTS.get(cache_key)
    if cached is not None:
        _CONTEXTS.move_to_end(cache_key)
        context, annotations = cached
        return context, dict(annotations), SimpleNamespace(network_requests=0)
    block_row = report.get("block", {})
    if report.get("mode") != "state_collection" or block_row.get("number") != number:
        raise ValueError(f"invalid collection report for block {number}")
    snapshot = SnapshotStore().load(int(block_row["chain"]), str(block_row["hash"]))
    if snapshot.block != type(snapshot.block)(int(block_row["chain"]), number,
                                               str(block_row["hash"]).lower(), int(block_row["timestamp"])):
        raise ValueError("collection report and snapshot header disagree")

    archive = Path(report.get("inventory_evidence", ""))
    if not archive.is_file():
        raise ValueError("collection inventory archive is missing")
    inventories_by_name = _read(archive)
    inventories = tuple(inventories_by_name[name] for name in sorted(inventories_by_name))
    known_families = {row["family"] for row in inventories}
    selected_families = known_families if selected_families is None else selected_families
    if not selected_families or selected_families - known_families:
        raise ValueError(f"unknown or empty source selection: {sorted(selected_families - known_families)}")

    adapters = implemented_adapters()
    # Collection acquired V3/V4 tick values through their bulk StateView plans.
    from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
    from swaparch.adapters.uniswap_v4 import UniswapV4Adapter
    adapters["uniswap_v3"] = UniswapV3Adapter(bulk_ticks=True)
    adapters["uniswap_v4"] = UniswapV4Adapter(bulk_ticks=True)
    states, unavailable, records = [], [], []
    for row in report.get("excluded", []):
        unavailable.append(dict(row))
    qualified_records = []
    for row in report.get("records", []):
        record = pool_record_from_json(row["record"])
        records.append(record)
        qualified = bool(row.get("quote_qualified_here"))
        if record.family not in selected_families:
            unavailable.append({"family": record.family, "pool": record.pool,
                                "status": "excluded", "reason": "excluded by source selection"})
            continue
        adapter = adapters.get(record.family)
        if adapter is None:
            unavailable.append({"family": record.family, "pool": record.pool,
                                "status": "discovered_unsupported", "reason": "adapter not implemented"})
            continue
        try:
            states.append(adapter.load_state(record, snapshot))
            if qualified:
                qualified_records.append(record.pool_id)
        except (Unsupported, KeyError, ValueError) as exc:
            unavailable.append({"family": record.family, "pool": record.pool,
                                "status": "discovered_unsupported", "reason": str(exc)})
    tokens = {token.address: token for inventory in inventories for row in inventory.get("pools", [])
              for token in pool_record_from_json(row).tokens}
    context = PreparedQuoteContext(inventories, frozenset(adapters), tuple(records), tokens,
                                   snapshot.block, tuple(states), tuple(unavailable))
    annotations = {
        "qualification_mode": "collection-model-only",
        "independently_qualified_at_block": False,
        "collection_artifact": str(report_path),
        "collection_artifact_digest": artifact_digest,
        "collection_evidence_identity": collection_identity,
        "collection_inventory_identity": report.get("inventory_identity"),
        "collection_read_plan_version": report.get("read_plan_version"),
        "collection_state_records_with_prior_exact_hash_qualification": qualified_records,
    }
    _CONTEXTS[cache_key] = (context, annotations)
    _CONTEXTS.move_to_end(cache_key)
    while len(_CONTEXTS) > 8:
        _CONTEXTS.popitem(last=False)
    return context, annotations, SimpleNamespace(network_requests=0)
