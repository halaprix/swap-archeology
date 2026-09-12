"""Verify all four independent pool legs of the improved 1 ETH route at 23549999."""
import gzip
import json
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode, encode
from uniswap_v3_online_check import QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE
from uniswap_v4_run import _quoter_spec

from swaparch.collection_quotes import prepared_collection_context
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
path = next((ROOT / 'outputs/october-eth-prices/raw').glob('23549999-*-1.json.gz'))
with gzip.open(path, 'rt') as f:
    report = json.load(f)['report']
c, _, _ = prepared_collection_context(23549999)
assert c.block.hash == report['block_hash']
steps = report['best_split']['steps']
assert len({s['pool_id'] for s in steps}) == len(steps), 'Repeated pools require stateful execution checks'
states = {s.record.pool_id: s for s in c.states}
specs = []
for step in steps:
    s = states[step['pool_id']]
    if s.record.family == 'uniswap_v4':
        specs.append(_quoter_spec(s.record, s.token0.address == step['token_in'], step['amount_in']))
    else:
        assert s.record.family == 'uniswap_v3'
        data = SEL_QUOTE_EXACT_INPUT_SINGLE + encode(['(address,address,uint256,uint24,uint160)'],
            [(step['token_in'], step['token_out'], step['amount_in'], s.fee, 0)]).hex()
        specs.append(CallSpec(QUOTER_V2, data, 'quoteExactInputSingle'))
client = RpcClient(cache_root=ROOT / 'outputs/october-native-validation/rpc-cache')
results = Multicall3(client).call(specs, c.block)
checks = []
for step, result in zip(steps, results, strict=True):
    output = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0] if result.success else None
    checks.append({'step': step, 'referenceOut': output, 'matched': output == step['amount_out'], 'raw': asdict(result)})
(ROOT / 'outputs/october-native-validation/improved-route.json').write_text(json.dumps({
    'block': c.block.number, 'blockHash': c.block.hash, 'checks': checks,
    'networkRequests': client.network_requests, 'scope': 'Distinct-pool quoter checks, not atomic settlement'}, indent=2)+'\n')
assert all(r['matched'] for r in checks)
print(json.dumps({'checks': len(checks), 'matched': True, 'networkRequests': client.network_requests}))
