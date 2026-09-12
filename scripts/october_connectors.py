"""Acquire only Curve 3pool's missing state for the 254-block October window."""
from __future__ import annotations

import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from curve_discovery_run import META, spec, unpack

from swaparch.adapters.curve_legacy_3pool import POOL, REGISTRY, ZERO, CurveLegacy3PoolAdapter
from swaparch.core.types import BlockRef
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import pool_record_from_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/october-connectors'


def collect(row, template):
    block = BlockRef(1, row['block'], row['blockHash'], row['timestamp'])
    client = RpcClient(cache_root=OUT / 'rpc-cache')
    store = SnapshotStore(OUT / 'snapshots')
    identity = [spec(REGISTRY, 'get_coins(address)', ['address'], [POOL]),
                spec(REGISTRY, 'get_decimals(address)', ['address'], [POOL]),
                spec(META, 'get_base_pool(address)', ['address'], [POOL])]
    snapshot = store.extend(block, identity, client)
    coins = list(unpack(snapshot.get(identity[0]), 'address[8]'))[:3]
    decimals = list(unpack(snapshot.get(identity[1]), 'uint256[8]'))[:3]
    base = unpack(snapshot.get(identity[2]), 'address')
    record = copy.deepcopy(template)
    assert coins == [t['address'] for t in record['tokens']]
    assert decimals == [18, 6, 6] and base == ZERO
    record['config']['registry_observations'] = {block.hash: {
        'block': block.number, 'coins': coins, 'decimals': decimals,
        'base_pool': base, 'base_registries': [REGISTRY],
        'method': 'direct canonical registry get_coins/get_decimals; MetaRegistry get_base_pool'}}
    record['config']['curve_legacy_3pool'] = True
    record['config']['transfer_semantics'] = {coin: 'standard' for coin in coins}
    record['config']['validated_block_hashes'] = []
    record['discovered_by'] = {'deployed_by_block': block.number,
                               'evidence': [str(OUT / 'snapshots' / '1' / block.hash)]}
    record['status'] = 'supported'
    record['notes'] = 'Supplemental collection-model state; selected independent get_dy checks only.'
    adapter = CurveLegacy3PoolAdapter()
    parsed = pool_record_from_json(record)
    snapshot = store.extend(block, adapter.read_requests(parsed, block), client)
    adapter.load_state(parsed, snapshot)
    path = OUT / 'records' / f'{block.hash}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'blockHash': block.hash, 'records': [record]}, indent=1)+'\n')
    tmp.replace(path)
    return {'block': block.number, 'networkRequests': client.network_requests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', help='comma-separated bounded subset')
    args = parser.parse_args()
    rows = [r for r in json.loads((ROOT / 'outputs/dense-crash/references.json').read_text())['rows']
            if 23549939 <= r['block'] <= 23550192]
    if args.blocks:
        wanted = {int(b) for b in args.blocks.split(',')}
        assert wanted <= {r['block'] for r in rows}
        rows = [r for r in rows if r['block'] in wanted]
    template = next(r for r in json.loads((ROOT / 'data/discovery/1/curve.json').read_text())['pools'] if r['pool'] == POOL)
    with ThreadPoolExecutor(max_workers=3) as executor:
        for result in executor.map(lambda row: collect(row, template), rows):
            print(json.dumps(result), flush=True)

if __name__ == '__main__':
    main()
