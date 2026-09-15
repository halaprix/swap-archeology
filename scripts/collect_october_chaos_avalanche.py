"""Collect Avalanche Chaos WETH/USD aligned backwards to the October Ethereum pins."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from eth_abi import decode
from eth_utils import keccak

from scripts.collect_october_external_oracles import cross_rate
from scripts.collect_october_oracle_references import atomic_write_json, validate_source_dataset

ROOT = Path(__file__).resolve().parents[1]
RPC = 'https://api.avax.network/ext/bc/C/rpc'
ADDRESS = '0xC33FD9cC294371398a6C7880A05F6B039F3a138C'
SOURCE = 'chaos_avalanche_eth_usdc'
META = {
    'id': SOURCE, 'kind': 'oracle', 'chainId': 43114, 'address': ADDRESS,
    'label': 'Chaos WETH (Avalanche) / Chainlink USDC',
    'source': 'https://docs.benqi.fi/resources/contracts/price-feeds',
    'description': 'Avalanche Chaos WETH/USD divided by Ethereum Chainlink USDC/USD. '
                   'Latest Avalanche block at or before each Ethereum timestamp; cross-chain reference, '
                   'not an Ethereum feed or executable quote. Update ages and both block identities retained.',
}


def batch(calls: list[tuple], offline: bool) -> list:
    payload = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p} for i, (m, p) in enumerate(calls)]
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    path = ROOT / 'outputs/chaos-avalanche/cache' / f'{key}.json'
    if path.exists():
        response = json.loads(path.read_text())
    else:
        if offline:
            raise ValueError('offline cache miss')
        for attempt in range(4):
            try:
                r = requests.post(RPC, json=payload, timeout=40)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            response = r.json()
            break
        else:
            raise ValueError('public RPC rate limit')
    if not isinstance(response, list) or len(response) != len(calls):
        raise ValueError('incomplete RPC batch')
    indexed = {r['id']: r for r in response}
    if set(indexed) != set(range(len(calls))) or any('error' in r for r in response):
        raise ValueError('RPC batch error or identity mismatch')
    atomic_write_json(path, response)
    return [indexed[i]['result'] for i in range(len(calls))]


def collect(calls: list[tuple], offline: bool) -> list:
    size = 3 if calls and calls[0][0] == 'eth_call' else 20
    chunks = [calls[i:i + size] for i in range(0, len(calls), size)]
    with ThreadPoolExecutor(max_workers=12) as executor:
        return [value for chunk in executor.map(lambda c: batch(c, offline), chunks) for value in chunk]


def align(headers: list[dict], timestamp: int) -> dict:
    times = [int(h['timestamp'], 16) for h in headers]
    i = bisect_right(times, timestamp) - 1
    if i < 0 or i + 1 >= len(headers) or not times[i] <= timestamp < times[i + 1]:
        raise ValueError('timestamp outside bracketed Avalanche headers')
    return headers[i]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if int(batch([('eth_chainId', [])], args.offline)[0], 16) != 43114:
        raise ValueError('Avalanche C-chain required')
    target = ROOT / 'frontend/public/october-oracle-references.json'
    data = json.loads(target.read_text())
    rows = validate_source_dataset(data)
    # Fixed study boundaries verified by explorer timestamp lookup; include bounding successors.
    numbers = range(70101072, 70102666)
    headers = collect([('eth_getBlockByNumber', [hex(b), False]) for b in numbers], args.offline)
    for i, (number, h) in enumerate(zip(numbers, headers, strict=True)):
        if int(h['number'], 16) != number or (i and h['parentHash'] != headers[i - 1]['hash']):
            raise ValueError('Avalanche header continuity mismatch')
    pins = [align(headers, int(datetime.fromisoformat(row['timestamp']).timestamp())) for row in rows]
    signatures = ['description()', 'decimals()', 'latestRoundData()']
    calls = [('eth_call', [{'to': ADDRESS, 'data': '0x' + keccak(text=sig)[:4].hex()},
                          {'blockHash': h['hash']}]) for h in pins for sig in signatures]
    results = collect(calls, args.offline)
    for i, (row, h) in enumerate(zip(rows, pins, strict=True)):
        timestamp = int(datetime.fromisoformat(row['timestamp']).timestamp())
        raw = results[i * 3:i * 3 + 3]
        description, = decode(['string'], bytes.fromhex(raw[0][2:]))
        decimals, = decode(['uint8'], bytes.fromhex(raw[1][2:]))
        rid, answer, observed, updated, answered = decode(
            ['uint80', 'int256', 'uint256', 'uint256', 'uint80'], bytes.fromhex(raw[2][2:]))
        if (description != 'WETH / USD' or decimals != 8 or answer <= 0 or answered < rid
                or not 0 < observed <= updated <= int(h['timestamp'], 16)):
            raise ValueError('Chaos historical identity or round invalid')
        eth = {'answer': str(answer), 'decimals': decimals, 'description': description,
               'roundId': str(rid), 'answeredInRound': str(answered), 'startedAt': observed,
               'updatedAt': updated, 'ageSeconds': timestamp - updated}
        usdc = row['values']['redstone_eth_usdc']['usdcUsd']
        value = cross_rate(eth, usdc)
        value.update({'reason': f"Update age: Chaos Avalanche {timestamp - updated}s; "
                               f"Chainlink USDC {usdc['ageSeconds']}s; "
                               f"Avalanche block {int(h['number'], 16)}",
                      'avalancheBlock': int(h['number'], 16), 'avalancheBlockHash': h['hash'],
                      'avalancheTimestamp': int(h['timestamp'], 16), 'chainId': 43114})
        row['values'][SOURCE] = value
    data['sources'] = [s for s in data['sources'] if s['id'] != SOURCE] + [META]
    atomic_write_json(target, data)
    print(json.dumps({'blocks': len(rows), 'available': sum(r['values'][SOURCE]['status'] == 'ok' for r in rows)}))


if __name__ == '__main__':
    main()
