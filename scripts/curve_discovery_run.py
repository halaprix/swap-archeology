"""Hash-pinned MetaRegistry enumeration; indices are never reused across blocks."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.universe import load_inventory

ROOT = Path(__file__).resolve().parents[1]
META = '0xf98b45fa17de75fb1ad0e7afd971b0ca00e379fc'
ZERO = '0x' + '00' * 20
NATIVE = '0x' + 'ee' * 20


def spec(target, signature, types=(), args=()):
    return CallSpec(target, '0x' + keccak(text=signature)[:4].hex() + encode(types, args).hex(), signature)


def unpack(result, abi):
    if not result.success or not result.raw[2:]:
        raise ValueError(f'failed {result.spec.tag} on {result.spec.to}')
    return decode([abi], bytes.fromhex(result.raw[2:]))[0]


def main(number):
    client = RpcClient()
    block = client.get_block(number)
    base = ROOT / f'data/discovery-evidence/curve/{block.hash}'
    base.mkdir(parents=True, exist_ok=True)
    multicall = Multicall3(client, chunk_size=100)

    def batch(name, specs):
        results = multicall.call(specs, block)
        (base / f'{name}.json').write_text(json.dumps({'block': asdict(block),
            'calls': [asdict(r) for r in results]}, indent=1) + '\n')
        return results

    count, registry_count = [unpack(r, 'uint256') for r in batch('counts',
        [spec(META, 'pool_count()'), spec(META, 'registry_length()')])]
    if count > 100_000 or registry_count > 10:
        raise ValueError('registry count exceeds canonical ABI bounds or enumeration budget')
    handlers = [unpack(r, 'address') for r in batch('handlers',
        [spec(META, 'get_registry(uint256)', ['uint256'], [i]) for i in range(registry_count)])]
    bases = dict(zip(handlers, [unpack(r, 'address') for r in batch('base-registries',
        [spec(META, 'get_base_registry(address)', ['address'], [h]) for h in handlers])], strict=True))
    indexed = [unpack(r, 'address') for r in batch('pool-list',
        [spec(META, 'pool_list(uint256)', ['uint256'], [i]) for i in range(count)])]
    addresses = sorted(set(indexed) - {ZERO})
    coin_calls = batch('coins', [spec(META, signature, ['address'], [p]) for p in addresses
        for signature in ('get_coins(address)', 'get_underlying_coins(address)')])
    known = {t.address: t for p in load_inventory('uniswap_v3') for t in p.tokens}
    desired = set(known) | {NATIVE}
    selected, failures = {}, []
    for i, address in enumerate(addresses):
        try:
            coins, underlying = [tuple(a for a in unpack(r, 'address[8]') if a != ZERO)
                                 for r in coin_calls[2*i:2*i+2]]
        except ValueError as exc:
            failures.append({'pool': address, 'reason': str(exc)})
            continue
        if len(desired.intersection(coins) | desired.intersection(underlying)) >= 2:
            selected[address] = {'coins': coins, 'underlying': underlying}
    enrichment = batch('selected-metadata', [spec(META, signature, ['address'], [p]) for p in selected
        for signature in ('get_decimals(address)', 'get_base_pool(address)', 'get_registry_handlers_from_pool(address)')])
    path = ROOT / 'data/discovery/1/curve.json'
    inventory = json.loads(path.read_text())
    old = {r['pool']: r for r in inventory['pools']}
    for i, (address, item) in enumerate(selected.items()):
        try:
            decimals = unpack(enrichment[3*i], 'uint256[8]')
            base_pool = unpack(enrichment[3*i+1], 'address')
            owners = [a for a in unpack(enrichment[3*i+2], 'address[10]') if a != ZERO]
        except ValueError as exc:
            failures.append({'pool': address, 'reason': str(exc)})
            continue
        tokens = [{'chain': 1, 'address': a, 'decimals': decimals[j],
                   'symbol': known[a].symbol if a in known else ('ETH' if a == NATIVE else a)}
                  for j, a in enumerate(item['coins'])]
        row = old.get(address, {'family': 'curve', 'chain': 1,
            'pool_id': f'curve:{META}:{address}', 'deployment': META, 'pool': address,
            'tokens': tokens, 'config': {}, 'created_block': None,
            'discovered_by': {'method': 'MetaRegistry.pool_list/get_coins', 'evidence': []},
            'status': 'discovered_unsupported', 'notes': 'Local quote model not yet qualified'})
        row['config'].setdefault('registry_observations', {})[block.hash] = {
            'block': number, 'coins': list(item['coins']), 'underlying_coins': list(item['underlying']),
            'decimals': list(decimals[:len(item['coins'])]), 'base_pool': base_pool,
            'registry_handlers': owners, 'base_registries': [bases[h] for h in owners]}
        row['discovered_by']['deployed_by_block'] = min(number, row['discovered_by'].get('deployed_by_block', number))
        reference = str(base.relative_to(ROOT))
        if reference not in row['discovered_by']['evidence']:
            row['discovered_by']['evidence'].append(reference)
        old[address] = row
    inventory['pools'] = sorted(old.values(), key=lambda row: row['pool'])
    inventory['coverage'] = [r for r in inventory['coverage'] if r.get('to_block_hash') != block.hash]
    inventory['coverage'].append({'method': 'MetaRegistry.pool_list', 'deployment': META,
        'from_block': number, 'to_block': number, 'to_block_hash': block.hash,
        'enumerated_indexes': count, 'unique_addresses': len(addresses),
        'filter': {'tokens': sorted(desired), 'minimum_matching_coins_or_underlying': 2},
        'selected_pools': len(selected), 'failures': failures, 'evidence': str(base.relative_to(ROOT))})
    inventory['notes'] = 'Per-block full MetaRegistry index enumeration, deduplicated addresses, explicit coin/underlying filter. Membership proves activation by that block; absence does not prove nonexistence. All quote models still require implementation-specific qualification.'
    inventory['unresolved'] = [r for r in inventory['unresolved'] if not r['what'].startswith('the pool list')]
    path.write_text(json.dumps(inventory, indent=1) + '\n')
    print(json.dumps({'block': number, 'indexed': count, 'unique': len(addresses),
                      'selected': len(selected), 'failures': len(failures),
                      'network_requests': client.network_requests}), flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 25896003))
