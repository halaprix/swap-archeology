from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

from swaparch.core.types import BlockRef

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "balancer_user_pools_probe.py"
spec = importlib.util.spec_from_file_location("balancer_user_pools_probe", SCRIPT)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_code_presence_is_strict() -> None:
    assert probe.code_is_present("0x") is False
    assert probe.code_is_present("0x60006000") is True


def test_online_probe_skips_all_getters_when_pool_has_no_code(monkeypatch, tmp_path: Path) -> None:
    pool = next(iter(probe.POOLS))
    vault = probe.DEFAULT_VAULT

    class FakeClient:
        network_requests = 0

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def get_block(self, number: int) -> BlockRef:
            return BlockRef(1, number, "0x" + "ab" * 32, 123)

        def _rpc(self, method: str, params: list[object]) -> str:
            assert method == "eth_getCode"
            address = str(params[0]).lower()
            return "0x60006000" if address == vault else "0x"

        def eth_call(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("pool getters must not run for an absent pool")

    class NoCallsStore:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def extend(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("SnapshotStore must not run for an absent pool")

    monkeypatch.setattr(probe, "RpcClient", FakeClient)
    monkeypatch.setattr(probe, "SnapshotStore", NoCallsStore)
    output_dir = tmp_path / "evidence"
    probe.run_online(
        Namespace(
            vault=vault,
            blocks=[23549991],
            pools=[pool],
            cache_root=tmp_path / "rpc-cache",
            snapshot_root=tmp_path / "snapshots",
            output_dir=output_dir,
            events=False,
            log_chunk=100,
        )
    )
    output = __import__("json").loads(next(output_dir.glob("*.json")).read_text())
    row = output["rows"][0]
    assert row["status"] == "absent_at_pin"
    assert row["calls"] == []
    assert row["buffers"] == []
