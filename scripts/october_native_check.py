"""Bounded independent native ETH/USDC quoter comparisons; read-only."""
import json
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode, encode
from fluid_dex_run import SEL_ESTIMATE_SWAP_IN
from uniswap_v4_run import _quoter_spec

from swaparch.adapters.fluid_dex import resolver_for_block
from swaparch.collection_quotes import prepared_collection_context
from swaparch.core.types import NATIVE_ETH, CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

OUT = Path(__file__).resolve().parents[1] / 'outputs/october-native-validation'
POOLS = {'0x836951eb21f3df98273517b7249dceff270d34bf',
         '0x21c67e77068de97969ba93d4aab21826d33ca12bb9f565d8496e8fda8a82ca27',
         '0xdce6394339af00981949f5f3baf27e3610c76326a700af57e4b3e3ae4977f78d'}


def main():
    client = RpcClient(cache_root=OUT / 'rpc-cache')
    rows = []
    for b in (23550020, 23550044, 23550046, 23550125):
        c, _, _ = prepared_collection_context(b)
        specs, pending = [], []
        for s in c.states:
            if s.record.pool not in POOLS:
                continue
            tokens = {t.symbol: t for t in s.tokens()}
            zero_for_one = s.token0.address == NATIVE_ETH
            for amount in (10**18, 10*10**18, 100*10**18):
                output = s.quote_exact_in(NATIVE_ETH, tokens['USDC'].address, amount)
                if s.record.family == 'uniswap_v4':
                    spec = _quoter_spec(s.record, zero_for_one, amount)
                else:
                    data = SEL_ESTIMATE_SWAP_IN + encode(['address', 'bool', 'uint256', 'uint256'], [s.record.pool, zero_for_one, amount, 0]).hex()
                    spec = CallSpec(resolver_for_block(b), data, 'estimateSwapIn')
                specs.append(spec)
                pending.append({'block': b, 'blockHash': c.block.hash, 'family': s.record.family,
                                'pool': s.record.pool, 'amountIn': amount, 'modelOut': output})
        results = Multicall3(client, chunk_size=5).call(specs, c.block)
        for row, result in zip(pending, results, strict=True):
            row['raw'] = asdict(result)
            if result.success:
                types = ['uint256', 'uint256'] if row['family'] == 'uniswap_v4' else ['uint256']
                row['referenceOut'] = decode(types, bytes.fromhex(result.raw[2:]))[0]
                row['matched'] = row['referenceOut'] == row['modelOut']
            else:
                row['matched'] = False
            rows.append(row)
    OUT.mkdir(exist_ok=True)
    (OUT / 'checks.json').write_text(json.dumps({'rows': rows, 'networkRequests': client.network_requests,
        'scope': 'V4 Quoter and Fluid pre-operation resolver equality; not transaction settlement'}, indent=2)+'\n')
    assert len(rows) == 36 and all(r['matched'] for r in rows)
    print(json.dumps({'checks': len(rows), 'matched': True, 'networkRequests': client.network_requests}))


if __name__ == '__main__':
    main()
