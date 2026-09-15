import pytest

from scripts.collect_october_market_references import coingecko_value
from scripts.five_crash_oracle_check import match_binance_candle


def test_market_observation_boundaries_and_units():
    usdc = {'answer': '99900000', 'decimals': 8, 'ageSeconds': 10}
    prices = [[100000, 3700], [200000, 3800]]
    assert coingecko_value(prices, 99, usdc)['price'] is None
    assert coingecko_value(prices, 199, usdc)['price'] == pytest.approx(3700 / .999)
    assert coingecko_value(prices, 200, usdc)['observationTimestampMs'] == 200000
    assert coingecko_value(prices, 8000, usdc)['price'] is None
    candle = {'openTime': 1760130000000, 'closeTime': 1760130059999, 'open': 3700,
              'high': 3800, 'low': 3600, 'close': 3750}
    data = {'symbol': 'ETHUSDC', 'candles': [candle]}
    assert match_binance_candle(data, 1760130059)['price'] is None
    assert match_binance_candle(data, 1760130060)['price'] == 3750
