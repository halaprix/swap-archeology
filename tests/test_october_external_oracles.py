import pytest
from eth_abi import encode

from scripts.collect_october_external_oracles import cross_rate, decode_feed, merge_values
from swaparch.core.types import CallResult, CallSpec


def feed(answer=370012345678, updated=990, decimals=8):
    spec = CallSpec("0x" + "11" * 20, "0x")
    return [CallResult(spec, True, "0x" + encode(types, values).hex()) for types, values in [
        (["uint8"], [decimals]), (["string"], ["RedStone Price Feed for ETH"]),
        (["uint80", "int256", "uint256", "uint256", "uint80"], [1, answer, updated, updated, 1]),
    ]]


def test_decode_real_units_and_nonpeg_cross_rate():
    eth = decode_feed(feed(), 1000, "RedStone Price Feed for ETH")
    usdc = {**eth, "answer": "99900000"}
    value = cross_rate(eth, usdc)
    assert value["price"] == pytest.approx(3700.12345678 / 0.999)
    assert int(value["ratioNumerator"]) * 99900000 == 370012345678 * int(value["ratioDenominator"])
    assert eth["ageSeconds"] == 10 and eth["roundId"] == "1"


@pytest.mark.parametrize("kwargs", [{"answer": 0}, {"answer": -1}, {"updated": 0},
                                    {"updated": 1001}, {"decimals": 18}])
def test_invalid_feed_is_rejected(kwargs):
    with pytest.raises(ValueError):
        decode_feed(feed(**kwargs), 1000, "RedStone Price Feed for ETH")


def test_stale_value_is_not_plotted():
    eth = decode_feed(feed(updated=1), 86402, "RedStone Price Feed for ETH")
    assert cross_rate(eth, {**eth, "answer": "100000000"})["price"] is None


def test_merge_preserves_old_series_and_rejects_wrong_hash():
    rows = [{"block": b, "blockHash": "0x" + "ab" * 32, "timestamp": "2025-10-10T21:14:11Z",
             "values": {"oneinch_spot": {"price": 3500, "status": "ok"}}}
            for b in range(23549939, 23550193)]
    base = {"schemaVersion": 1, "sources": [{"id": "oneinch_spot"}], "rows": rows}
    values = {r["block"]: {"price": 3700, "status": "ok"} for r in rows}
    result = merge_values(base, rows, values)
    assert result["rows"][0]["values"]["oneinch_spot"] == rows[0]["values"]["oneinch_spot"]
    assert "redstone_eth_usdc" not in rows[0]["values"]
    assert result["rows"][0]["values"]["chaos_eth_usdc"]["price"] is None
    assert result["rows"][0]["values"]["chaos_eth_usdc"]["status"] == "unresolved"
    assert merge_values(result, rows, values) == result
    with pytest.raises(ValueError, match="incomplete"):
        merge_values(base, rows, {})
    result["rows"][0]["blockHash"] = "0x" + "cd" * 32
    with pytest.raises(ValueError, match="identity"):
        merge_values(result, rows, values)
