import gzip
import json

import pytest

from swaparch.core.types import BlockRef


def _write(path, value):
    with gzip.open(path, "wt") as handle:
        json.dump(value, handle)


def _artifact(tmp_path, *, snapshot_hash="0x" + "11" * 32, report_hash=None):
    archive = tmp_path / "inventory.json.gz"
    pool = {"family": "fixture", "chain": 1, "pool_id": "fixture:pool",
            "deployment": "0x0000000000000000000000000000000000000001",
            "pool": "0x0000000000000000000000000000000000000002",
            "tokens": [{"address": "0x0000000000000000000000000000000000000003",
                        "symbol": "WETH", "decimals": 18}], "config": {"overlay": "exact-block"},
            "created_block": 1, "discovered_by": {"deployed_by_block": 1}, "status": "supported"}
    _write(archive, {"fixture.json": {"family": "fixture", "status": "supported", "pools": [pool]}})
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "header.json").write_text("{}")
    (snapshot / "calls.json.gz").write_bytes(b"calls")
    header_root = tmp_path / "headers"
    header_root.mkdir()
    (header_root / "7.json").write_text(json.dumps({"response": {"result": {
        "hash": report_hash or snapshot_hash, "number": "0x7", "timestamp": "0x9"}}}))
    report = tmp_path / f"{(report_hash or snapshot_hash)}.json.gz"
    _write(report, {"mode": "state_collection", "block": {"chain": 1, "number": 7,
            "hash": report_hash or snapshot_hash, "timestamp": 9}, "snapshot": str(snapshot),
            "inventory_evidence": str(archive), "inventory_identity": "inventory", "read_plan_version": 4,
            "records": [{"record": pool, "quote_qualified_here": False}], "excluded": []})
    return report, snapshot_hash, header_root


def test_collection_context_loads_overlay_without_promoting_qualification(tmp_path, monkeypatch):
    import swaparch.collection_quotes as quotes

    _, _block_hash, header_root = _artifact(tmp_path)
    seen = []

    class Store:
        def load(self, chain, hash_):
            return type("Snapshot", (), {"block": BlockRef(chain, 7, hash_, 9)})()

    class Adapter:
        def load_state(self, record, snapshot):
            seen.append(record.config["overlay"])
            return type("State", (), {"record": record})()

    monkeypatch.setattr(quotes, "SnapshotStore", Store)
    monkeypatch.setattr(quotes, "implemented_adapters", lambda: {"fixture": Adapter()})
    monkeypatch.setattr(quotes, "HEADER_ROOT", header_root)
    context, annotations, client = quotes.prepared_collection_context(7, root=tmp_path)
    assert seen == ["exact-block"]
    assert len(context.states) == 1 and client.network_requests == 0
    assert annotations["qualification_mode"] == "collection-model-only"
    assert annotations["independently_qualified_at_block"] is False
    assert annotations["collection_state_records_with_prior_exact_hash_qualification"] == []


def test_collection_context_rejects_report_snapshot_hash_mismatch(tmp_path, monkeypatch):
    import swaparch.collection_quotes as quotes

    _, _, header_root = _artifact(tmp_path, report_hash="0x" + "22" * 32)

    class Store:
        def load(self, chain, hash_):
            return type("Snapshot", (), {"block": BlockRef(chain, 7, "0x" + "11" * 32, 9)})()

    monkeypatch.setattr(quotes, "SnapshotStore", Store)
    monkeypatch.setattr(quotes, "HEADER_ROOT", header_root)
    with pytest.raises(ValueError, match="disagree"):
        quotes.prepared_collection_context(7, root=tmp_path)


def test_collection_identity_changes_with_snapshot_or_frozen_inventory(tmp_path, monkeypatch):
    import swaparch.collection_quotes as quotes

    _report, _hash, header_root = _artifact(tmp_path)
    monkeypatch.setattr(quotes, "HEADER_ROOT", header_root)
    first = quotes.collection_artifact_identity(7, tmp_path)
    (tmp_path / "snapshot" / "calls.json.gz").write_bytes(b"changed calls")
    second = quotes.collection_artifact_identity(7, tmp_path)
    assert second != first
    _write(tmp_path / "inventory.json.gz", {"fixture.json": {"family": "fixture", "status": "supported",
           "pools": [], "revision": "changed"}})
    assert quotes.collection_artifact_identity(7, tmp_path) != second
