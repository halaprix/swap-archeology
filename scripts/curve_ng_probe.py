"""Read exact StableSwap-NG plain-pool identities and local-math inputs."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from curve_discovery_run import ZERO, spec
from eth_abi import decode

from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import load_inventory

ROOT = Path(__file__).resolve().parents[1]
FACTORY = '0x6a8cbed756804b16e05e741edabd5cb544ae21bf'
READS = {'N_COINS()': 'uint256', 'A()': 'uint256', 'A_precise()': 'uint256',
         'get_balances()': 'uint256[]', 'stored_rates()': 'uint256[]',
         'fee()': 'uint256', 'offpeg_fee_multiplier()': 'uint256', 'admin_fee()': 'uint256'}


def main(number):
    client = RpcClient()
    block = client.get_block(number)
    records = []
    for record in load_inventory('curve'):
        observed = record.config.get('registry_observations', {}).get(block.hash)
        if observed and observed['base_pool'] == ZERO and FACTORY in observed['base_registries']:
            records.append(record)
    specs = [spec(r.pool, signature) for r in records for signature in READS]
    specs += [spec(FACTORY, signature, ['address'], [r.pool]) for r in records
              for signature in ('get_pool_asset_types(address)', 'get_implementation_address(address)')]
    snapshot = SnapshotStore().extend(block, specs, client)
    rows = []
    for r in records:
        reads = [(spec(r.pool, sig), abi) for sig, abi in READS.items()]
        reads += [(spec(FACTORY, 'get_pool_asset_types(address)', ['address'], [r.pool]), 'uint8[]'),
                  (spec(FACTORY, 'get_implementation_address(address)', ['address'], [r.pool]), 'address')]
        rows.append({'pool': r.pool, 'pool_id': r.pool_id, 'values': {}, 'failures': [],
                     'calls': [asdict(snapshot.get(s)) for s, _ in reads]})
        for call, abi in reads:
            raw = snapshot.get(call)
            if raw.success and raw.raw != '0x':
                rows[-1]['values'][call.tag] = decode([abi], bytes.fromhex(raw.raw[2:]))[0]
            else:
                rows[-1]['failures'].append(call.tag)
    out = ROOT / f'data/discovery-evidence/curve-ng/{block.hash}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'block': asdict(block), 'factory': FACTORY,
                              'rows': rows, 'network_requests': client.network_requests}, indent=1) + '\n')
    print(json.dumps({'block': number, 'plain_ng_pools': len(rows),
        'failed_pools': sum(bool(r['failures']) for r in rows),
        'network_requests': client.network_requests, 'evidence': str(out)}), flush=True)


if __name__ == '__main__':
    for number in (sys.argv[1:] or ['25896003']):
        main(int(number))
