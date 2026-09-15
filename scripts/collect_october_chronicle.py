"""Collect Chronicle's exposed ETH/USD price at the canonical October blocks."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime

from eth_abi import decode
from eth_utils import keccak

from scripts.collect_october_external_oracles import ROOT, cross_rate
from scripts.collect_october_oracle_references import atomic_write_json, validate_source_dataset
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ADDRESS = '0x46ef0071b1E2fF6B42d36e5A177EA43Ae5917f4E'
SOURCE = 'chronicle_eth_usdc'
META = {
    'id': SOURCE, 'kind': 'oracle', 'chainId': 1, 'address': ADDRESS,
    'label': 'Chronicle ETH / Chainlink USDC',
    'description': 'Chronicle readWithAge ETH/USD divided by same-block Chainlink USDC/USD. '
                   'Exposed ScribeOptimistic value, not a pending optimistic update or the downstream OSM. '
                   'Read age and challenge period retained; not a swap quote.',
}


def decode_value(results, timestamp: int, usdc: dict) -> dict:
    if len(results) != 4 or not all(r.success for r in results):
        raise ValueError('Chronicle call unavailable')
    raw = [bytes.fromhex(r.raw[2:]) for r in results]
    wat, = decode(['bytes32'], raw[0])
    decimals, = decode(['uint8'], raw[1])
    answer, updated = decode(['uint256', 'uint256'], raw[2])
    challenge, = decode(['uint16'], raw[3])
    if wat.rstrip(b'\0') != b'ETH/USD' or decimals != 18 or answer <= 0 or not 0 < updated <= timestamp:
        raise ValueError('Chronicle identity or value invalid')
    eth = {'answer': str(answer), 'decimals': decimals, 'updatedAt': updated,
           'ageSeconds': timestamp - updated, 'description': 'ETH/USD'}
    value = cross_rate(eth, usdc)
    value.update({'challengePeriodSeconds': challenge,
                  'reason': f"Read age: Chronicle {timestamp - updated}s; "
                            f"Chainlink USDC {usdc['ageSeconds']}s; challenge period {challenge}s"})
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--collect-only', action='store_true', help='cache reads without replacing the frontend sidecar')
    args = parser.parse_args()
    client = RpcClient(offline=args.offline)
    if client.chain_id() != 1:
        raise ValueError('Ethereum mainnet required')
    target = ROOT / 'frontend/public/october-oracle-references.json'
    data = json.loads(target.read_text())
    rows = validate_source_dataset(data)
    specs = [CallSpec(ADDRESS, '0x' + keccak(text=sig)[:4].hex(), sig)
             for sig in ['wat()', 'decimals()', 'readWithAge()', 'opChallengePeriod()']]
    mc = Multicall3(client)
    for row in rows:
        block = client.get_block(row['block'])
        if (block.hash != row['blockHash']
                or block.timestamp != int(datetime.fromisoformat(row['timestamp']).timestamp())):
            raise ValueError('Canonical block mismatch')
        results = mc.call(specs, block)
        atomic_write_json(ROOT / 'outputs/october-chronicle' / f'{block.hash}.json',
                          {'block': asdict(block), 'calls': [asdict(r) for r in results]})
        row['values'][SOURCE] = decode_value(results, block.timestamp, row['values']['redstone_eth_usdc']['usdcUsd'])
    if args.collect_only:
        print(json.dumps({'blocks': len(rows), 'networkRequests': client.network_requests, 'published': False}))
        return
    # Reload to preserve other collectors' independent series, then merge only ours.
    latest = json.loads(target.read_text())
    for row, collected in zip(validate_source_dataset(latest), rows, strict=True):
        if row['blockHash'] != collected['blockHash']:
            raise ValueError('Sidecar changed block identity')
        row['values'][SOURCE] = collected['values'][SOURCE]
    latest['sources'] = [s for s in latest['sources'] if s['id'] != SOURCE] + [META]
    atomic_write_json(target, latest)
    print(json.dumps({'blocks': len(rows), 'networkRequests': client.network_requests}))


if __name__ == '__main__':
    main()
