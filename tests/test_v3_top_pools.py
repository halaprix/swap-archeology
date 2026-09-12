import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import uniswap_v3_top_pools as top

from swaparch.core.types import BlockRef, CallResult


def test_balance_spec_and_deterministic_deduplicated_ranking():
    pool_a = "0x" + "a" * 40
    pool_b = "0x" + "b" * 40
    weth = top.ENDPOINT_TOKENS["WETH"]
    block = BlockRef(1, 10, "0x" + "1" * 64, 0)
    rows = [
        {"pool_id": "a", "pool": pool_a, "tokens": ["WETH", "USDC"],
         "endpoint_symbols": ["WETH", "USDC"], "created_block": 1,
         "inventory_status": "supported", "not_deployed_at_block": False},
        {"pool_id": "b", "pool": pool_b, "tokens": ["WETH", "DAI"],
         "endpoint_symbols": ["WETH"], "created_block": 1,
         "inventory_status": "supported", "not_deployed_at_block": False},
    ]
    values = {top.balance_spec(pool_a, weth, "WETH").data: 20,
              top.balance_spec(pool_b, weth, "WETH").data: 20}
    class Snapshot:
        def has(self, spec): return spec.data in values
        def get(self, spec): return CallResult(spec, True, hex(values[spec.data])[2:].rjust(64, "0") and "0x" + hex(values[spec.data])[2:].rjust(64, "0"), "cache")
    report = top.rank_candidates(rows, block, Snapshot(), top_n=5)
    assert [r["pool"] for r in report["top5"]["WETH"]] == sorted([pool_a, pool_b])
    assert len(report["selected_pools"]) == 2
    assert top.BALANCE_SELECTOR == "0x70a08231"


def test_exclusion_semantics_are_distinct():
    block = BlockRef(1, 10, "0x" + "2" * 64, 0)
    pool = "0x" + "c" * 40
    rows = [
        {"pool_id": "future", "pool": pool, "tokens": ["WETH"], "endpoint_symbols": ["WETH"],
         "created_block": 11, "inventory_status": "supported", "not_deployed_at_block": True},
        {"pool_id": "missing", "pool": "0x" + "d" * 40, "tokens": ["WETH"], "endpoint_symbols": ["WETH"],
         "created_block": 1, "inventory_status": "supported", "not_deployed_at_block": False},
        {"pool_id": "quote", "pool": "0x" + "e" * 40, "tokens": ["WETH"], "endpoint_symbols": ["WETH"],
         "created_block": 1, "inventory_status": "discovered_unsupported", "not_deployed_at_block": False},
    ]
    class Snapshot:
        def has(self, spec): return False
    statuses = [r["status"] for r in top.rank_candidates(rows, block, Snapshot())["candidates"]]
    assert statuses == ["not_deployed_at_block", "missing_balance", "missing_balance"]


def test_discovery_specs_cover_both_indexed_positions_and_all_fees():
    specs = top._discovery_specs(123)
    assert len(specs) == 12
    assert all(len(request["topics"]) == 3 for request in specs)
    assert {request["position"] for request in specs} == {1, 2}


def test_online_main_discovers_extends_snapshot_and_ranks(monkeypatch, tmp_path):
    pool = "0x" + "f" * 40
    weth = top.ENDPOINT_TOKENS["WETH"]
    usdc = top.ENDPOINT_TOKENS["USDC"]
    block = BlockRef(1, 100, "0x" + "3" * 64, 0)
    log = {
        "topics": [top.POOL_CREATED_TOPIC, "0x" + usdc[2:].rjust(64, "0"),
                   "0x" + weth[2:].rjust(64, "0"), "0x" + (500).to_bytes(32, "big").hex()],
        "data": "0x" + (60).to_bytes(32, "big", signed=True).hex() + bytes.fromhex(pool[2:]).rjust(32, b"\0").hex(),
        "blockNumber": hex(block.number),
    }
    calls = []

    class Client:
        network_requests = 0
        def get_block(self, number): return block
        def get_logs(self, address, topics, start, end, chunk):
            calls.append(topics)
            return [log]

    class Snapshot:
        def has(self, spec): return spec.data in {top.balance_spec(pool, weth, "WETH").data}
        def get(self, spec): return CallResult(spec, True, "0x" + hex(10**18)[2:].rjust(64, "0"), "cache")

    class Store:
        def extend(self, block_ref, specs, client):
            self.specs = specs
            return Snapshot()

    monkeypatch.setattr(top, "RpcClient", lambda **kwargs: Client())
    monkeypatch.setattr(top, "SnapshotStore", Store)
    monkeypatch.setattr(top, "EVIDENCE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["top", "--discover", "--online", "--block", "100"])
    assert top.main() == 0
    report = json.loads(next(tmp_path.glob("top5-*.json")).read_text())
    assert report["top5"]["WETH"]
    assert report["top5"]["WETH"][0]["quote_status"] == "excluded_quote"
    assert len(calls) == 12
    assert {topics[1] for topics in calls} == {
        None, *("0x" + address[2:].rjust(64, "0") for address in top.ENDPOINT_TOKENS.values())
    }


def test_offline_main_uses_cached_block_and_never_reads_future_balance(monkeypatch):
    pool = "0x" + "1" * 40
    block = BlockRef(1, 10, "0x" + "4" * 64, 0)
    requested = []
    class Snapshot:
        def has(self, spec): requested.append(spec); return False
    class Store:
        def load(self, chain, block_hash): return Snapshot()
    class Client:
        def __init__(self, **kwargs): assert kwargs == {"offline": True}
        def get_block(self, number): return block
    future = {"pool_id": "future", "pool": pool, "tokens": ["WETH"], "endpoint_symbols": ["WETH"],
              "created_block": 11, "inventory_status": "supported", "not_deployed_at_block": True}
    monkeypatch.setattr(top, "RpcClient", Client)
    monkeypatch.setattr(top, "SnapshotStore", Store)
    report = top.rank_candidates([future], block, Snapshot())
    assert report["candidates"][0]["status"] == "not_deployed_at_block"
    assert requested == []
