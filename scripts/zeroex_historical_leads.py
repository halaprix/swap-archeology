"""Check selected live0x discovery leads at the start of the historical window."""
import json
from dataclasses import asdict
from pathlib import Path

from curve_discovery_run import spec, unpack
from eth_abi import decode
from zeroex_discovery import TOKENS

from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/0x-discovery'
FACTORY = '0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865'
QUOTER = '0xb048bbc1ee6b733fffcfb9e9cef7375518e25997'
LEADS = {'daiUsds':'0x3225737a9bbb6473cb4a45b7244aca2befdb276a',
         'usdsPsmWrapper':'0xa188eec8f81263234da3622a406892f3d630f98c',
         'pancakeV3Factory':FACTORY,'ekuboV2Core':'0xe0e0e08a6a4b9dc7bd67bcb7aade5cf48157d444',
         'listaFactory':'0xf6c9ffa64bd0ae8a068dd7b7d954c654a3e7f8a6','ekuboV3Core':'0x00000000000014aa86c5d3c41765bb24e11bd701'}


def main():
    client = RpcClient(cache_root=OUT/'historical-rpc-cache')
    block = client.get_block(23549939)
    codes = {name:{'address':address, 'code':client._rpc('eth_getCode',[address,{'blockHash':block.hash,'requireCanonical':True}])}
             for name,address in LEADS.items()}
    pairs = [(b,fee) for b in ('USDC','USDT','DAI','USDS','WBTC') for fee in (100,500,2500,10000)]
    calls = [spec(FACTORY,'getPool(address,address,uint24)',['address','address','uint24'],[TOKENS['WETH'][0],TOKENS[b][0],fee]) for b,fee in pairs]
    results = Multicall3(client).call(calls,block)
    pools = []
    for (b,fee),r in zip(pairs,results,strict=True):
        address=unpack(r,'address')
        if int(address,16):pools.append({'buySymbol':b,'fee':fee,'pool':address,'discovery':asdict(r)})
    states = Multicall3(client).call([spec(p['pool'],'liquidity()') for p in pools],block)
    for p,r in zip(pools,states,strict=True):p.update(liquidity=unpack(r,'uint128'),liquidityRaw=asdict(r))
    spike=client.get_block(23550094)
    quote_pairs=[(p,n) for p in pools if p['buySymbol']=='USDC' for n in (1,10,100)]
    qs=[spec(QUOTER,'quoteExactInputSingle((address,address,uint256,uint24,uint160))',
             ['(address,address,uint256,uint24,uint160)'],[(TOKENS['WETH'][0],TOKENS['USDC'][0],n*10**18,p['fee'],0)]) for p,n in quote_pairs]
    answers=Multicall3(client).call(qs,spike)
    quotes=[]
    for (p,n),r in zip(quote_pairs,answers,strict=True):
        out=decode(['uint256'],bytes.fromhex(r.raw[2:]))[0] if r.success else None
        quotes.append({'pool':p['pool'],'fee':p['fee'],'sellWeth':n,'buyUsdcRaw':out,'nominalOutputPerRequestedWeth':out/1e6/n if out is not None else None,'fullInputFillVerified':False,'raw':asdict(r)})
    connector_pairs=[(a,b,fee) for a,b in [('WETH','WBTC'),('WBTC','USDT'),('WBTC','USDC')] for fee in (100,500,3000,10000)]
    connector_calls=[spec('0x1f98431c8ad98523631ae4a59f267346ea31f984','getPool(address,address,uint24)',
                         ['address','address','uint24'],[TOKENS[a][0],TOKENS[b][0],fee]) for a,b,fee in connector_pairs]
    connector_results=Multicall3(client).call(connector_calls,block)
    connector_pools=[]
    for (a,b,fee),r in zip(connector_pairs,connector_results,strict=True):
        address=unpack(r,'address')
        if int(address,16):connector_pools.append({'sellSymbol':a,'buySymbol':b,'fee':fee,'pool':address,'discovery':asdict(r)})
    ls=Multicall3(client).call([spec(p['pool'],'liquidity()') for p in connector_pools],block)
    for p,r in zip(connector_pools,ls,strict=True):p.update(liquidity=unpack(r,'uint128'),liquidityRaw=asdict(r))
    result={'block':asdict(block),'codes':codes,'pools':pools,'uniswapConnectorPools':connector_pools,'quoteBlock':asdict(spike),'quotes':quotes,'networkRequests':client.network_requests}
    (OUT/'historical-leads.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'deployed':{k:bool(int(v['code'],16)) if v['code']!='0x' else False for k,v in codes.items()},'pancakePools':len(pools),'quotes':[dict(p,raw=None) for p in quotes]}))

if __name__=='__main__':main()
