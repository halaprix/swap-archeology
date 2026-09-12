"""Read-only current0x liquidity leads. Never treats API prices as historical quotes."""
import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/0x-discovery'
TOKENS = {'WBTC': ('0x2260fac5e5542a773aa44fbcfedf7c193bc2c599',8),
          'WETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',18),
          'ETH': ('0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',18),
          'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',6),
          'USDT': ('0xdac17f958d2ee523a2206206994597c13d831ec7',6),
          'DAI': ('0x6b175474e89094c44da98b954eedeac495271d0f',18),
          'USDS': ('0xdc035d45d973e3ec169d2276ddab16f1e407384f',18)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--sources-only',action='store_true')
    parser.add_argument('--only-source',help='Probe only this exact source label from the live source list')
    args=parser.parse_args()
    key=args.key_file.read_text().strip() if args.key_file else os.environ['ZERO_EX_API_KEY']
    session=requests.Session()
    session.headers.update({'0x-api-key':key,'0x-version':'v2'})
    OUT.mkdir(exist_ok=True,parents=True)

    def get(path,params,name):
        for attempt in range(3):
            response=session.get('https://api.0x.org'+path,params=params,timeout=45)
            if response.status_code==429 and attempt<2:
                time.sleep(2**(attempt+1));continue
            break
        body=response.json()
        payload={'retrievedAt':datetime.now(UTC).isoformat(),'endpoint':path,'params':params,
                 'status':response.status_code,'historical':False,'response':body}
        text=json.dumps(payload,indent=2).replace(key,'[REDACTED]')
        (OUT/(name+'.json')).write_text(text+'\n')
        if response.status_code!=200:
            raise RuntimeError(f'0x {response.status_code}; inspect saved redacted response {name}.json')
        time.sleep(.3)
        return body

    sources=get('/sources',{'chainId':1},'sources')['sources']
    print(json.dumps({'sources':sources}),flush=True)
    if args.sources_only:return
    if args.only_source and args.only_source not in sources:
        raise ValueError('source not advertised on Ethereum')
    cases=[(a,'USDC',n) for a in ('ETH','WETH') for n in (1,10,100,1000)]
    cases += [(a,b,100000) for a,b in [('USDT','DAI'),('DAI','USDC'),('USDT','USDC'),('USDS','USDC')]]
    # Stable connectors at a meaningful trade size; no live execution.
    for a,b,n in cases:
        params={'chainId':1,'sellToken':TOKENS[a][0],'buyToken':TOKENS[b][0],
                'sellAmount':str(n*10**TOKENS[a][1])}
        if args.only_source:params['excludedSources']=','.join(s for s in sources if s!=args.only_source)
        name=f'{a}-{b}-{n}'+('-'+args.only_source if args.only_source else '')
        body=get('/swap/allowance-holder/price',params,name)
        print(json.dumps({'case':name,'block':body.get('blockNumber'),'liquid':body.get('liquidityAvailable'),
                          'fills':body.get('route',{}).get('fills',[])}),flush=True)

if __name__=='__main__':main()
