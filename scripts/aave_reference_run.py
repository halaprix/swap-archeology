"""Acquire the historical Aave pool/provider/oracle path and endpoint price ratios."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from check_inventory_claims import spec
from eth_abi import decode

from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import implemented_adapters, load_inventory

ROOT = Path(__file__).resolve().parents[1]
POOL = '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2'
ENDPOINTS = {'WETH', 'wstETH', 'sUSDe', 'USDC', 'USDT', 'DAI'}


def main(number):
    client, store = RpcClient(), SnapshotStore()
    block = client.get_block(number)
    calls = [spec(POOL, 'ADDRESSES_PROVIDER()')]
    snapshot = store.extend(block, calls, client)

    def address(call):
        result = snapshot.get(call)
        if not result.success:
            raise ValueError(f'Aave reference identity read failed: {call.tag}')
        value = decode(['address'], bytes.fromhex(result.raw[2:]))[0]
        if value == '0x' + '0' * 40:
            raise ValueError('Aave reference identity is zero')
        return value

    provider = address(calls[0])
    calls.append(spec(provider, 'getPriceOracle()'))
    snapshot = store.extend(block, calls, client)
    oracle = address(calls[1])
    tokens = {t.address: t for family in implemented_adapters()
              for record in load_inventory(family) for t in record.tokens if t.symbol in ENDPOINTS}
    price_specs = [spec(oracle, 'getAssetPrice(address)', ['address'], [token]) for token in tokens]
    calls.extend(price_specs)
    snapshot = store.extend(block, calls, client)
    prices = []
    for token, call in zip(tokens.values(), price_specs, strict=True):
        result = snapshot.get(call)
        value = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0] if result.success else None
        prices.append({'token': asdict(token), 'price_base': value,
                       'available': value is not None and value > 0})
    path = ROOT / f'data/validation/aave-reference/{block.hash}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'block': asdict(block), 'pool': POOL, 'provider': provider,
        'oracle': oracle, 'prices': prices, 'calls': [asdict(snapshot.get(call)) for call in calls],
        'network_requests': client.network_requests,
        'scope': 'Aave asset-price ratio at block end. Shared oracle base units cancel in a token-pair ratio; no stablecoin peg or fair-value assumption.'}, indent=2) + '\n')
    print(json.dumps({'block': number, 'prices': len(prices),
                      'available': sum(row['available'] for row in prices),
                      'network_requests': client.network_requests, 'evidence': str(path)}), flush=True)


if __name__ == '__main__':
    for number in sys.argv[1:] or ['25896003']:
        main(int(number))
