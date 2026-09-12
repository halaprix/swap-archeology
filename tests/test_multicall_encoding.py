from pathlib import Path

import pytest
import requests

from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient, RpcError
from swaparch.rpc.multicall import decode_aggregate3, encode_aggregate3


def test_aggregate3_one_slot0_call_matches_hand_computed_calldata() -> None:
    spec = CallSpec(
        "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        "0x3850c7bd",
        "slot0",
    )
    expected = (
        "0x82ad56cb"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000001"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "00000000000000000000000088e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        "0000000000000000000000000000000000000000000000000000000000000001"
        "0000000000000000000000000000000000000000000000000000000000000060"
        "0000000000000000000000000000000000000000000000000000000000000004"
        "3850c7bd00000000000000000000000000000000000000000000000000000000"
    )

    assert encode_aggregate3((spec,)) == expected


def test_aggregate3_result_decode_round_trip() -> None:
    encoded_result = (
        "0x"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000001"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000001"
        "0000000000000000000000000000000000000000000000000000000000000040"
        "0000000000000000000000000000000000000000000000000000000000000004"
        "deadbeef00000000000000000000000000000000000000000000000000000000"
    )

    assert decode_aggregate3(encoded_result) == ((True, "0xdeadbeef"),)


def test_rpc_connection_errors_do_not_expose_endpoint_parts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RPC_MAINNET", "https://private.invalid/v2/credential")
    session = requests.Session()

    def fail(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("private.invalid /v2/credential")

    monkeypatch.setattr(session, "post", fail)
    monkeypatch.setattr("swaparch.rpc.client.time.sleep", lambda _: None)
    client = RpcClient(tmp_path, session)

    with pytest.raises(RpcError) as caught:
        client.chain_id()
    message = str(caught.value)
    assert "private.invalid" not in message
    assert "credential" not in message
