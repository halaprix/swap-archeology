"""Compressed call storage preserves pinned evidence and legacy cache reads."""

import json
import zlib
from dataclasses import replace

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.rpc.client import RpcClient


def test_sqlite_call_cache_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    writer = RpcClient("cache", offline=True)
    reader = RpcClient("cache", offline=True)
    block = BlockRef(1, 7, "0x" + "07" * 32, 0)
    spec = CallSpec("0x" + "11" * 20, "0x1234")
    result = CallResult(spec, False, "0xdead", "eth_call")
    writer.store_call_result(result, block)
    assert not writer._call_cache_path(spec, block).exists()
    assert reader.cached_call(spec, block).raw == "0xdead"
    assert not reader.cached_call(spec, block).success
    assert reader.cached_call(spec, replace(block, hash="0x" + "08" * 32)) is None
    payload = writer._call_db.execute("SELECT payload FROM calls").fetchone()[0]
    evidence = json.loads(zlib.decompress(payload))
    assert evidence["request"]["params"][1] == {"blockHash": block.hash}
    writer._call_db.close()
    writer._call_db = None
    assert reader.cached_call(spec, block).raw == result.raw
    legacy = replace(block, hash="0x" + "09" * 32)
    path = reader._call_cache_path(spec, legacy)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(evidence))
    assert reader.cached_call(spec, legacy).raw == result.raw
    assert reader.network_requests == writer.network_requests == 0
