"""Record the historical Lido ARM implementation and reserve-getter availability."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from check_inventory_claims import ARM, spec
from eth_abi import decode

from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]


def main(number):
    client = RpcClient()
    block = client.get_block(number)
    calls = [spec(ARM, 'implementation()'), spec(ARM, 'getReserves()'), spec(ARM, 'paused()')]
    snapshot = SnapshotStore().extend(block, calls, client)
    implementation, reserves, paused = [snapshot.get(call) for call in calls]
    if not implementation.success or implementation.raw == '0x':
        raise ValueError('ARM implementation getter unavailable')
    address = decode(['address'], bytes.fromhex(implementation.raw[2:]))[0]
    if reserves.success:
        decode(['uint256', 'uint256'], bytes.fromhex(reserves.raw[2:]))
    if paused.success:
        decode(['bool'], bytes.fromhex(paused.raw[2:]))
    observation = {'number': number, 'get_reserves': reserves.success,
                   'implementation': address, 'paused_getter': paused.success}
    path = ROOT / f'data/discovery-evidence/origin-arm-layout/{block.hash}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'block': asdict(block), 'observation': observation,
        'calls': [asdict(snapshot.get(call)) for call in calls],
        'network_requests': client.network_requests}, indent=2) + '\n')
    inventory_path = ROOT / 'data/discovery/1/origin_arm.json'
    inventory = json.loads(inventory_path.read_text())
    row = next(row for row in inventory['pools'] if row['pool'] == ARM)
    row['config'].setdefault('historical_observations', {})[block.hash] = observation
    discovery = row['discovered_by']
    discovery['deployed_by_block'] = min(number, discovery.get('deployed_by_block', number))
    discovery['evidence'] = sorted(set(discovery['evidence'] + [str(path.relative_to(ROOT))]))
    inventory_path.write_text(json.dumps(inventory, indent=1) + '\n')
    print(json.dumps({'block': number, **observation,
                      'network_requests': client.network_requests}), flush=True)


if __name__ == '__main__':
    for number in sys.argv[1:] or ['25896003']:
        main(int(number))
