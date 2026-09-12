"""Qualify the bounded plain NG state model against same-hash pool get_dy views."""
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from curve_discovery_run import spec
from eth_abi import decode

from swaparch.adapters.curve_ng import CurveNGAdapter
from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory

ROOT = Path(__file__).resolve().parents[1]
STETH = '0xae7ab96520de3a18e5e111b5eaab095312d7fe84'


def main(number):
    client, adapter = RpcClient(), CurveNGAdapter()
    block = client.get_block(number)
    known = {t.address for p in load_inventory('uniswap_v3') for t in p.tokens} - {STETH}
    records = [replace(r, config={**r.config, 'transfer_semantics': {
        t.address: 'standard' for t in r.tokens if t.address in known}})
        for r in load_inventory('curve') if block.hash in r.config.get('registry_observations', {})]
    snapshot, acquisition = acquire({'curve': adapter}, records, block, SnapshotStore(), client)
    rows, specs, pending = {}, [], []
    for record in records:
        row = rows[record.pool_id] = {'pool': record.pool, 'supported': False, 'checks': []}
        try:
            if record.pool_id in acquisition.unsupported:
                raise Unsupported(acquisition.unsupported[record.pool_id])
            state = adapter.load_state(record, snapshot)
            row['calls'] = [asdict(snapshot.get(s)) for s in adapter.read_requests(record, block)]
            for i, source in enumerate(state.tokens()):
                for j, target in enumerate(state.tokens()):
                    if i == j:
                        continue
                    for units in (1, 100):
                        amount = units * 10**source.decimals
                        check = {'token_in': source.address, 'token_out': target.address,
                                 'amount_in': amount, 'matched': False}
                        row['checks'].append(check)
                        try:
                            check['local_out'] = state.swap(source.address, target.address, amount)[0]
                        except Unsupported as exc:
                            check['reason'] = str(exc)
                            continue
                        specs.append(spec(record.pool, 'get_dy(int128,int128,uint256)',
                                          ['int128', 'int128', 'uint256'], [i, j, amount]))
                        pending.append(check)
        except Unsupported as exc:
            row['reason'] = str(exc)
    results = Multicall3(client).call(specs, block)
    for check, result in zip(pending, results, strict=True):
        check['reference'] = asdict(result)
        if result.success:
            check['reference_out'] = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0]
            check['matched'] = check['reference_out'] == check['local_out']
    for row in rows.values():
        row['supported'] = bool(row['checks']) and all(c['matched'] for c in row['checks'])
        if not row['supported']:
            row.setdefault('reason', 'not all size/direction reference checks passed')
    out = ROOT / f'data/validation/curve-ng/{block.hash}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'block': asdict(block), 'rows': rows,
        'network_requests': client.network_requests,
        'scope': 'Type-0 plain NG standard-transfer quote assumptions; precise A agrees with view A. No metapool, external rate, rebasing or settlement claim.'}, indent=1) + '\n')
    path = ROOT / 'data/discovery/1/curve.json'
    inventory = json.loads(path.read_text())
    configs = {r.pool_id: dict(r.config) for r in records}
    for record in inventory['pools']:
        row = rows.get(record['pool_id'])
        if row is None:
            continue
        record['config'] = configs[record['pool_id']]
        hashes = set(record['config'].get('validated_block_hashes', []))
        hashes.discard(block.hash)
        if row['supported']:
            hashes.add(block.hash)
        record['config']['validated_block_hashes'] = sorted(hashes)
        record['status'] = 'supported' if hashes else 'discovered_unsupported'
        record['notes'] = ('Per-hash plain NG get_dy qualification in data/validation/curve-ng; source-model transfer assumptions only.'
                           if hashes else row['reason'])
    inventory['status'] = 'supported' if any(r['status'] == 'supported' for r in inventory['pools']) else 'discovered_unsupported'
    path.write_text(json.dumps(inventory, indent=1) + '\n')
    checks = [c for row in rows.values() for c in row['checks']]
    print(json.dumps({'block': number, 'observed': len(rows),
        'supported': sum(r['supported'] for r in rows.values()), 'checks': len(checks),
        'matched': sum(c['matched'] for c in checks), 'network_requests': client.network_requests,
        'evidence': str(out)}), flush=True)


if __name__ == '__main__':
    for number in (sys.argv[1:] or ['25896003']):
        main(int(number))
