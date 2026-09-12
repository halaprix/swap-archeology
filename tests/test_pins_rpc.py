import json
import sys
from pathlib import Path

import pytest

from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient, rpc_available
from swaparch.rpc.multicall import Multicall3

EVIDENCE = Path(__file__).parents[1] / "evidence/crash-rescue-simulation/standing-prices.json"


def rpc_marker_selected() -> bool:
    return any(
        arg == "rpc" and index and sys.argv[index - 1] == "-m"
        or arg.startswith("-m") and "rpc" in arg[2:]
        for index, arg in enumerate(sys.argv)
    )


pytestmark = [
    pytest.mark.rpc,
    pytest.mark.skipif(
        not rpc_marker_selected() or not rpc_available(),
        reason="requires -m rpc and a resolvable RPC_MAINNET",
    ),
]


def replay_pins(client: RpcClient) -> None:
    rows = json.loads(EVIDENCE.read_text())["rows"]
    multicall = Multicall3(client)
    for row in rows:
        block = client.get_block(row["block"])
        assert block.hash == row["block_hash"].lower()
        calls = [call for call in row["calls"] if call["request"]["method"] == "eth_call"]
        specs = tuple(
            CallSpec(call["request"]["params"][0]["to"], call["request"]["params"][0]["data"])
            for call in calls
        )
        expected = tuple(call["response"]["result"].lower() for call in calls)

        batched = multicall.call(specs, block)
        assert tuple(result.raw for result in batched) == expected
        direct = tuple(client.eth_call(spec.to, spec.data, block) for spec in specs)
        assert tuple(result.raw for result in direct) == expected


def test_saved_pin_headers_and_calls_match_rpc_and_then_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / "rpc-cache"
    first = RpcClient(cache_root=cache_root)
    replay_pins(first)
    assert 0 < first.network_requests < 300

    second = RpcClient(cache_root=cache_root)
    replay_pins(second)
    assert second.network_requests == 0
