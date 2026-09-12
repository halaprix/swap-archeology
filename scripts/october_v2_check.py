"""Independent V2 reserve/formula/router checks at three October crash blocks."""
import json
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.collection_quotes import prepared_collection_context
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

OUT = Path(__file__).resolve().parents[1] / 'outputs/october-validation'
ROUTER = '0x7a250d5630b4cf539739df2c5dacb4c659f2488d'


def spec(to, signature, types=(), values=()):
    return CallSpec(to, '0x' + (keccak(text=signature)[:4] + encode(types, values)).hex(), signature)


def main():
    client = RpcClient(cache_root=OUT / 'rpc-cache-v2')
    rows = []
    for b in (23550020, 23550044, 23550046):
        context, _, _ = prepared_collection_context(b)
        states = [s for s in context.states if s.record.family == 'uniswap_v2'
                  and {t.symbol for t in s.tokens()} in ({'WETH', 'USDC'}, {'WETH', 'USDT'}, {'WETH', 'DAI'})]
        calls, jobs = [], []
        for s in states:
            tin = next(t for t in s.tokens() if t.symbol == 'WETH')
            tout = next(t for t in s.tokens() if t.symbol != 'WETH')
            calls.extend([spec(s.record.pool, 'getReserves()'), spec(s.record.pool, 'token0()')])
            for size in (1, 10, 100):
                calls.append(spec(ROUTER, 'getAmountsOut(uint256,address[])', ['uint256', 'address[]'], [size*10**18, [tin.address, tout.address]]))
            jobs.append((s, tin, tout))
        results = Multicall3(client).call(calls, context.block)
        for i, (s, tin, tout) in enumerate(jobs):
            res = results[i*5:i*5+5]
            assert all(r.success for r in res)
            r0, r1, stamp = decode(['uint112', 'uint112', 'uint32'], bytes.fromhex(res[0].raw[2:]))
            token0 = decode(['address'], bytes.fromhex(res[1].raw[2:]))[0]
            assert token0 == s.token0.address
            assert (r0, r1) == (s.reserve0, s.reserve1)
            rin, rout = (r0, r1) if token0 == tin.address else (r1, r0)
            row = {'block': b, 'blockHash': context.block.hash, 'pool': s.record.pool,
                   'quoteToken': tout.symbol, 'spot': rout/rin * 10**(18-tout.decimals),
                   'reserveTimestamp': stamp, 'reservesMatch': True, 'checks': [], 'raw': [asdict(r) for r in res]}
            for size, result in zip((1, 10, 100), res[2:], strict=True):
                amount = size*10**18
                formula = amount*997*rout//(rin*1000+amount*997)
                model, _ = s.swap(tin.address, tout.address, amount)
                router = decode(['uint256[]'], bytes.fromhex(result.raw[2:]))[0][-1]
                assert formula == model == router
                row['checks'].append({'sizeWeth': size, 'formula': formula, 'model': model, 'router': router,
                                      'executionPrice': router/10**tout.decimals/size, 'matched': True})
            rows.append(row)
    OUT.mkdir(exist_ok=True)
    (OUT/'v2.json').write_text(json.dumps({'rows': rows, 'networkRequests': client.network_requests,
                                         'scope': 'pinned reserve and canonical Router02 comparison; spot units follow quoteToken'}, indent=2)+'\n')
    print(json.dumps({'pools': len(rows), 'quoteChecks': sum(len(r['checks']) for r in rows), 'matched': True, 'networkRequests': client.network_requests}))


if __name__ == '__main__':
    main()
