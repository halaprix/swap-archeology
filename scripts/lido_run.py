"""Qualify wstETH wrapper share quotes against pinned public conversion getters."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from curve_discovery_run import spec
from eth_abi import decode

from swaparch.adapters.lido import STETH, WSTETH, LidoAdapter
from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory, observe_singleton_activation

ROOT = Path(__file__).resolve().parents[1]


def main(number):
    client, adapter = RpcClient(), LidoAdapter()
    block = client.get_block(number)
    record = next(r for r in load_inventory('lido') if r.pool == WSTETH)
    record = observe_singleton_activation(record, block, client)
    if record is None:
        print(json.dumps({'block': number, 'reason': 'wstETH unavailable at block',
                          'network_requests': client.network_requests}))
        return 0
    snapshot, _ = acquire({'lido': adapter}, [record], block, SnapshotStore(), client)
    state = adapter.load_state(record, snapshot)
    specs, checks = [], []
    for source, target, getter in ((STETH, WSTETH, 'getWstETHByStETH(uint256)'),
                                   (WSTETH, STETH, 'getStETHByWstETH(uint256)')):
        for amount in (1, 10**9, 10**18, 100 * 10**18):
            local_out, after = state.swap(source, target, amount)
            if source == STETH:
                supply = state.wsteth_supply + local_out
                backing = state.wrapper_steth_shares + local_out
            else:
                supply = state.wsteth_supply - amount
                backing = state.wrapper_steth_shares - (local_out * state.total_shares // state.total_pooled_ether)
            specs.append(spec(WSTETH, getter, ['uint256'], [amount]))
            checks.append({'token_in': source, 'token_out': target, 'amount_in': amount,
                           'local_out': local_out, 'state_matched':
                           after.wsteth_supply == supply and after.wrapper_steth_shares == backing})
    for check, result in zip(checks, Multicall3(client).call(specs, block), strict=True):
        check['reference'] = asdict(result)
        check['matched'] = False
        if result.success:
            check['reference_out'] = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0]
            check['matched'] = check['local_out'] == check['reference_out'] and check['state_matched']
    try:
        state.swap(WSTETH, STETH, state.wsteth_supply + 1)
        boundary_reason = None
    except Unsupported as exc:
        boundary_reason = str(exc)
    boundary = {'name': 'unwrap exceeds total supply', 'reason': boundary_reason,
                'matched': boundary_reason is not None and 'but supply is' in boundary_reason}
    supported = all(c['matched'] for c in checks) and boundary['matched']
    path = ROOT / f'data/validation/lido/{block.hash}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'block': asdict(block), 'pool': WSTETH,
        'calls': [asdict(snapshot.get(s)) for s in adapter.read_requests(record, block)],
        'checks': checks, 'boundary': boundary, 'network_requests': client.network_requests,
        'scope': 'Public conversion getter quotes with finite wrapper supply/backing. Requested transfer amount is not proven recipient balance; no atomic settlement claim.'}, indent=2) + '\n')
    inventory_path = ROOT / 'data/discovery/1/lido.json'
    inventory = json.loads(inventory_path.read_text())
    row = next(r for r in inventory['pools'] if r['pool'] == WSTETH)
    row['discovered_by'] = dict(record.discovered_by)
    hashes = set(row['config'].get('validated_block_hashes', []))
    hashes.discard(block.hash)
    if supported:
        hashes.add(block.hash)
    row['config']['validated_block_hashes'] = sorted(hashes)
    row['status'] = 'supported' if hashes else 'discovered_unsupported'
    row['discovered_by']['deployed_by_block'] = min(number, row['discovered_by'].get('deployed_by_block', number))
    row['discovered_by']['evidence'] = sorted(set(row['discovered_by']['evidence'] + [str(path.relative_to(ROOT))]))
    row['notes'] = 'Per-hash public getter quotes qualified; finite backing/supply tracked. Actual stETH recipient balance and settlement unverified.'
    row['config']['state_needed_per_block'] = [s.tag for s in adapter.read_requests(record, block)]
    row['config']['directions'][0]['capacity'] = 'Conservative input domain <2**128-1 and checked math; newly minted wrapper shares add backing.'
    inventory['status'] = 'supported' if hashes else 'discovered_unsupported'
    inventory_path.write_text(json.dumps(inventory, indent=1) + '\n')
    print(json.dumps({'block': number, 'matched': sum(c['matched'] for c in checks),
                      'checks': len(checks), 'boundary': boundary['matched'],
                      'network_requests': client.network_requests}), flush=True)
    return int(not supported)


if __name__ == '__main__':
    raise SystemExit(max(main(int(n)) for n in (sys.argv[1:] or ['25896003'])))
