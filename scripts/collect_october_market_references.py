"""Add timestamp-aligned Binance minute closes and CoinGecko hourly observations."""
from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_right
from datetime import datetime
from fractions import Fraction
from itertools import pairwise

import requests

from scripts.collect_october_external_oracles import ROOT
from scripts.collect_october_oracle_references import atomic_write_json, validate_source_dataset
from scripts.five_crash_oracle_check import match_binance_candle

BINANCE_URL = 'https://data-api.binance.vision/api/v3/klines'
COINGECKO_URL = 'https://api.coingecko.com/api/v3/coins/ethereum/market_chart/range'
SOURCES = [
    {'id': 'binance_eth_usdc', 'kind': 'market', 'label': 'Binance ETH/USDC · 1m close',
     'source': BINANCE_URL, 'windowSeconds': 60,
     'description': 'Most recent completed Binance ETHUSDC one-minute candle close. '
                    'No unfinished candle or USDT substitution; completion time and OHLC retained.'},
    {'id': 'coingecko_eth_usdc', 'kind': 'market', 'label': 'CoinGecko ETH · hourly / Chainlink USDC',
     'source': COINGECKO_URL, 'windowSeconds': 3600,
     'description': 'CoinGecko hourly ETH/USD observations divided by Ethereum Chainlink USDC/USD. '
                    'Last timestamped observation carried forward, no future observation or interpolation. '
                    'Coarse historical reference, not minute-level crash resolution or an on-chain oracle. '
                    'Historical timestamps do not prove real-time API publication latency.'},
]


def coingecko_value(prices: list[list], timestamp: int, usdc: dict) -> dict:
    i = bisect_right([p[0] for p in prices], timestamp * 1000) - 1
    if i < 0:
        return {'price': None, 'status': 'unavailable', 'reason': 'No prior CoinGecko observation'}
    ms, price = prices[i]
    if not math.isfinite(price) or price <= 0 or int(usdc['answer']) <= 0:
        raise ValueError('Invalid reference price')
    lag = timestamp - ms / 1000
    ratio = Fraction(str(price)) / Fraction(int(usdc['answer']), 10 ** usdc['decimals'])
    stale = lag > 7200 or usdc['ageSeconds'] > 86400
    return {'price': None if stale else float(ratio), 'status': 'stale' if stale else 'ok',
            'observationTimestampMs': ms, 'ethUsd': price, 'usdcUsd': usdc,
            'ratioNumerator': str(ratio.numerator), 'ratioDenominator': str(ratio.denominator),
            'reason': f'Hourly observation age {lag:g}s; Chainlink USDC age {usdc["ageSeconds"]}s'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    out = ROOT / 'outputs/october-market-references'
    responses = {}
    for name, url, params in [
        ('binance', BINANCE_URL, {'symbol': 'ETHUSDC', 'interval': '1m', 'startTime': 1760130720000,
                                  'endTime': 1760133960000, 'limit': 1000}),
        ('coingecko', COINGECKO_URL, {'vs_currency': 'usd', 'from': 1760126400, 'to': 1760134200}),
    ]:
        path = out / f'{name}.json'
        if path.exists():
            responses[name] = json.loads(path.read_text())
        else:
            if args.offline:
                raise ValueError('Missing market reference cache')
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            responses[name] = response.json()
            atomic_write_json(path, responses[name])
    candles = [{'openTime': int(c[0]), 'closeTime': int(c[6]), 'open': float(c[1]),
                'high': float(c[2]), 'low': float(c[3]), 'close': float(c[4])} for c in responses['binance']]
    if any(c['openTime'] + 59999 != c['closeTime'] for c in candles):
        raise ValueError('Unexpected Binance timestamp units or interval')
    if any(a['openTime'] >= b['openTime'] for a, b in pairwise(candles)):
        raise ValueError('Unordered Binance candles')
    prices = responses['coingecko']['prices']
    if any(a[0] >= b[0] for a, b in pairwise(prices)):
        raise ValueError('Unordered CoinGecko observations')
    target = ROOT / 'frontend/public/october-oracle-references.json'
    data = json.loads(target.read_text())
    rows = validate_source_dataset(data)
    for row in rows:
        ts = int(datetime.fromisoformat(row['timestamp']).timestamp())
        value = match_binance_candle({'symbol': 'ETHUSDC', 'candles': candles}, ts)
        if value['status'] != 'ok' or value['lag_seconds'] >= 60 or not math.isfinite(value['price']) or value['price'] <= 0:
            raise ValueError('Missing or invalid completed Binance candle')
        value['reason'] = f'Completed 1m candle; age {value["lag_seconds"]}s'
        row['values']['binance_eth_usdc'] = value
        row['values']['coingecko_eth_usdc'] = coingecko_value(prices, ts, row['values']['redstone_eth_usdc']['usdcUsd'])
    data['sources'] = [s for s in data['sources'] if s['id'] not in {m['id'] for m in SOURCES}] + SOURCES
    atomic_write_json(target, data)
    print(json.dumps({'blocks': len(rows), 'binanceCandles': len(candles), 'coingeckoObservations': len(prices)}))


if __name__ == '__main__':
    main()
