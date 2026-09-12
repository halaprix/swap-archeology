"""Prepared quote contexts reuse only exact immutable artifact identities."""

from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

from swaparch import cli, universe
from swaparch.core.types import BlockRef


def _inventory(path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"family": "fixture", "status": "supported", "pools": [], "note": note}))


def _snapshot(root, block_hash: str, calls: str) -> None:
    directory = root / "1" / block_hash
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "header.json").write_text('{"hash":"' + block_hash + '"}')
    with gzip.open(directory / "calls.json.gz", "wt") as handle:
        handle.write(calls)


def test_prepared_context_reuses_same_identity_and_invalidates_artifact_changes(tmp_path, monkeypatch):
    discovery = tmp_path / "discovery"
    inventory = discovery / "1" / "fixture.json"
    _inventory(inventory, "one")
    snapshots = tmp_path / "snapshots"
    block = BlockRef(1, 100, "0xabc", 0)
    _snapshot(snapshots, block.hash, "one")

    monkeypatch.setattr(universe, "DISCOVERY_ROOT", discovery)
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", snapshots)
    monkeypatch.setattr(universe, "implemented_adapters", lambda: {"fixture": object()})
    calls = {"acquire": 0}

    def acquire(*_args):
        calls["acquire"] += 1
        return None, SimpleNamespace(unsupported={})

    monkeypatch.setattr(universe, "acquire", acquire)
    monkeypatch.setattr(universe, "load_states", lambda *_args: ([], []))
    monkeypatch.setattr(cli, "RpcClient", lambda **_kwargs: SimpleNamespace(
        get_block=lambda _number: block, network_requests=0
    ))
    cli._PREPARED_CONTEXTS.clear()
    cli._PARSED_INVENTORIES.clear()

    paths, inventory_id, inventories = cli._inventory_inputs()
    first, _ = cli._prepared_quote_context(
        100, {"fixture"}, False, inventory_paths=paths,
        inventory_id=inventory_id, inventories=inventories,
    )
    second, _ = cli._prepared_quote_context(
        100, {"fixture"}, False, inventory_paths=paths,
        inventory_id=inventory_id, inventories=inventories,
    )
    assert first is second
    assert calls["acquire"] == 1

    _inventory(inventory, "two")
    paths, inventory_id, inventories = cli._inventory_inputs()
    third, _ = cli._prepared_quote_context(
        100, {"fixture"}, False, inventory_paths=paths,
        inventory_id=inventory_id, inventories=inventories,
    )
    assert third is not first
    assert calls["acquire"] == 2

    _snapshot(snapshots, block.hash, "two")
    fourth, _ = cli._prepared_quote_context(
        100, {"fixture"}, False, inventory_paths=paths,
        inventory_id=inventory_id, inventories=inventories,
    )
    assert fourth is not third
    assert calls["acquire"] == 3
