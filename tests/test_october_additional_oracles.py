import pytest
from eth_abi import encode

from scripts.collect_october_chaos_avalanche import align
from scripts.collect_october_chronicle import decode_value
from swaparch.core.types import CallResult, CallSpec


def test_cross_chain_alignment_never_uses_a_future_block():
    headers = [{'timestamp': hex(t)} for t in [100, 102, 105]]
    assert align(headers, 101) == headers[0]
    assert align(headers, 102) == headers[1]
    for t in [99, 105]:
        with pytest.raises(ValueError):
            align(headers, t)


def test_chronicle_units_age_and_nonpeg_denominator():
    spec = CallSpec('0x' + '11' * 20, '0x')
    results = [CallResult(spec, True, '0x' + encode(types, values).hex()) for types, values in [
        (['bytes32'], [b'ETH/USD']), (['uint8'], [18]),
        (['uint256', 'uint256'], [3700 * 10**18, 100]), (['uint16'], [1200])]]
    usdc = {'answer': '99900000', 'decimals': 8, 'ageSeconds': 20}
    value = decode_value(results, 150, usdc)
    assert value['price'] == pytest.approx(3700 / 0.999)
    assert value['ethUsd']['ageSeconds'] == 50
    assert value['challengePeriodSeconds'] == 1200
    with pytest.raises(ValueError):
        decode_value(results, 99, usdc)
