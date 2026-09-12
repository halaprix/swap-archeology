import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import uniswap_v3_top_pools_enrich as enrich

from swaparch.core.types import BlockRef


def test_selected_specs_are_pool_state_and_metadata_only():
    pool = "0x" + "a" * 40
    token = "0x" + "b" * 40
    specs = enrich.selected_specs([{"pool": pool, "token_addresses": [token]}])
    assert len(specs) == 8
    assert {spec.to for spec in specs} == {pool, token}
    assert all(spec.tag.startswith("enrich:") for spec in specs)


def test_main_rejects_report_block_hash_before_snapshot_extension(monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"block": 100, "block_hash": "0x" + "1" * 64,
                                  "selected_pools": [], "top5": [], "candidates": []}))

    class Client:
        network_requests = 0
        def __init__(self, **kwargs): pass
        def get_block(self, number): return BlockRef(1, number, "0x" + "2" * 64, 0)

    class Store:
        def __init__(self): raise AssertionError("snapshot must not be touched on hash mismatch")

    monkeypatch.setattr(enrich, "RpcClient", Client)
    monkeypatch.setattr(enrich, "SnapshotStore", Store)
    monkeypatch.setattr(sys, "argv", ["enrich", "--report", str(report)])
    try:
        enrich.main()
    except SystemExit as exc:
        assert "block hash" in str(exc)
    else:
        raise AssertionError("expected block hash mismatch")
