"""HTTP transport handling for JSON-RPC errors."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from swaparch.core.types import BlockRef
from swaparch.rpc.client import RpcClient, RpcError


@dataclass
class Response:
    status_code: int
    body: object

    def json(self) -> object:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Session:
    def __init__(self, responses: list[Response]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, _url: str, *, json: dict[str, object], timeout: int) -> Response:
        assert timeout == 60
        self.calls.append(json)
        return self.responses.pop(0)


class LogsClient(RpcClient):
    def chain_id(self) -> int:
        return 1

    def get_block(self, number_or_hash: int | str) -> BlockRef:
        assert isinstance(number_or_hash, int)
        return BlockRef(1, number_or_hash, f"0x{number_or_hash:064x}", 0)


def test_http_400_json_rpc_log_range_error_halves_the_log_chunk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ETH_RPC_URL", "http://rpc.invalid")
    session = Session(
        [
            Response(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32602,
                        "message": "Log response size exceeded; 10,000 block range limit",
                    },
                },
            ),
            Response(200, {"jsonrpc": "2.0", "id": 2, "result": []}),
            Response(200, {"jsonrpc": "2.0", "id": 3, "result": []}),
        ]
    )
    client = LogsClient(cache_root=tmp_path, session=session)

    assert client.get_logs("0x0000000000000000000000000000000000000001", [], 1, 8, 8) == []
    ranges = [call["params"][0] for call in session.calls]
    assert [(item["fromBlock"], item["toBlock"]) for item in ranges] == [
        ("0x1", "0x8"),
        ("0x1", "0x4"),
        ("0x5", "0x8"),
    ]


def test_non_json_http_400_remains_a_generic_transport_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ETH_RPC_URL", "http://rpc.invalid")
    client = RpcClient(cache_root=tmp_path, session=Session([Response(400, ValueError("not json"))]))

    with pytest.raises(RpcError, match="eth_getLogs failed with HTTP 400"):
        client._rpc("eth_getLogs", [], max_attempts=1)
