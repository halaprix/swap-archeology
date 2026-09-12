"""Pinned contract checks of representative October spike routes and depth."""
import gzip
import json
from dataclasses import asdict
from pathlib import Path

from curve_discovery_run import spec
from eth_abi import decode, encode
from uniswap_v3_online_check import QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE
from uniswap_v4_run import _quoter_spec

from swaparch.collection_quotes import prepared_collection_context
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/october-spikes'

def quote_spec(state, tin, tout, amount):
    if state.record.family == 'uniswap_v4':
        return _quoter_spec(state.record, state.token0.address == tin, amount)
    if state.record.family == 'uniswap_v3':
        return CallSpec(QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE + encode(
            ['(address,address,uint256,uint24,uint160)'], [(tin, tout, amount, state.fee, 0)]).hex(), 'quoteExactInputSingle')
    assert state.record.family == 'curve'
    addresses = [t.address for t in state.tokens()]
    return spec(state.record.pool, 'get_dy(int128,int128,uint256)',
                ['int128', 'int128', 'uint256'], [addresses.index(tin), addresses.index(tout), amount])

def main():
    OUT.mkdir(exist_ok=True)
    rows = json.loads((ROOT / 'outputs/october-sources/prices.json').read_text())['rows']
    client = RpcClient(cache_root=OUT / 'rpc-cache')
    checks = []
    for block, token in [(23550060, 'ETH'), (23550094, 'ETH'), (23550094, 'WETH')]:
        row = next(r for r in rows if r['block'] == block)
        with gzip.open(ROOT / row['rawIndex']['aggregates'], 'rt') as f:
            route = json.load(f)['reports'][token + ':1']['best_split']['steps']
        assert len({s['pool_id'] for s in route}) == len(route)
        for b in [block-1, block, block+1]:
            context, _, _ = prepared_collection_context(b)
            states = {s.record.pool_id: s for s in context.states}
            pending, specs = [], []
            for idx, step in enumerate(route):
                if b != block and idx > 0:
                    continue
                state = states[step['pool_id']]
                amounts = [10**18, 10**19, 10**20] if idx == 0 else [step['amount_in']]
                for amount in amounts:
                    model = state.quote_exact_in(step['token_in'], step['token_out'], amount)
                    if b == block and amount == step['amount_in']:
                        assert model == step['amount_out']
                    specs.append(quote_spec(state, step['token_in'], step['token_out'], amount))
                    pending.append({'block': b, 'blockHash': context.block.hash, 'pool': state.record.pool,
                                    'tokenIn': step['token_in'], 'tokenOut': step['token_out'],
                                    'amountIn': amount, 'modelOut': model})
            results = Multicall3(client).call(specs, context.block)
            for check, result in zip(pending, results, strict=True):
                check['raw'] = asdict(result)
                check['referenceOut'] = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0] if result.success else None
                check['matched'] = check['referenceOut'] == check['modelOut']
                checks.append(check)
    (OUT / 'checks.json').write_text(json.dumps({'checks': checks, 'networkRequests': client.network_requests,
        'scope': 'Distinct pool quote checks and first-leg depth; not atomic settlement'}, indent=2)+'\n')
    assert all(c['matched'] for c in checks)
    print(json.dumps({'checks': len(checks), 'matched': True, 'networkRequests': client.network_requests}))

if __name__ == '__main__':
    main()
