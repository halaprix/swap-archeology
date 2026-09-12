"""Independent fresh block-hash reads for the two inventory claims required by signoff."""
import json
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3

ROOT = Path(__file__).resolve().parents[1]
ARM = '0x85b78aca6deae198fbf201c82daf6ca21942acc6'
LISTA = '0x35c9a4dae1ff05788f24b5b32721d89340cbb636'
WETH = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
STETH = '0xae7ab96520de3a18e5e111b5eaab095312d7fe84'


def spec(target, signature, types=(), args=()):
    return CallSpec(target, '0x' + keccak(text=signature)[:4].hex() + encode(types, args).hex(), signature)


def main():
    # A separate cache ensures this repeats reads independently of inventory acquisition.
    root = ROOT / 'data/validation/inventory-spotchecks'
    client = RpcClient(cache_root=root / 'rpc-cache')
    block = client.get_block(25896003)
    specs = [spec(ARM, 'getReserves()'), spec(ARM, 'withdrawsQueued()'),
             spec(ARM, 'withdrawsClaimed()'), spec(WETH, 'balanceOf(address)', ['address'], [ARM]),
             spec(STETH, 'balanceOf(address)', ['address'], [ARM]),
             spec(LISTA, 'coins(uint256)', ['uint256'], [0]),
             spec(LISTA, 'coins(uint256)', ['uint256'], [1]),
             spec(LISTA, 'get_dy(uint256,uint256,uint256)', ['uint256'] * 3, [0, 1, 10**6])]
    results = Multicall3(client).call(specs, block)
    for result in results:
        if not result.success or not result.raw[2:]:
            raise ValueError(f'failed or empty {result.spec.tag}')
    reserves = decode(['uint256', 'uint256'], bytes.fromhex(results[0].raw[2:]))
    queued, claimed, weth, steth = [decode(['uint256'], bytes.fromhex(r.raw[2:]))[0] for r in results[1:5]]
    coins = [decode(['address'], bytes.fromhex(r.raw[2:]))[0] for r in results[5:7]]
    lista_out = decode(['uint256'], bytes.fromhex(results[7].raw[2:]))[0]
    checks = {
        'arm_reserve0_claim': reserves[0] == 2_980_893_066_769_446,
        'arm_reserve1_claim': reserves[1] == 313_738_966_461_309,
        'arm_weth_balance_claim': weth == 19_293_422_946_852_522_412,
        'arm_queue_formula': reserves[0] == max(0, weth - (queued - claimed)),
        'arm_steth_formula': reserves[1] == steth,
        'lista_coins': coins == ['0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0xdac17f958d2ee523a2206206994597c13d831ec7'],
        'lista_get_dy_claim': lista_out == 1_000_302,
    }
    root.mkdir(parents=True, exist_ok=True)
    output = root / 'result.json'
    output.write_text(json.dumps({'chain': block.chain, 'block': block.number, 'block_hash': block.hash,
        'checks': checks, 'decoded': {'arm_reserves': reserves, 'withdrawsQueued': queued,
        'withdrawsClaimed': claimed, 'weth_balance': weth, 'steth_balance': steth,
        'lista_coins': coins, 'lista_out': lista_out},
        'calls': [{'to': r.spec.to, 'data': r.spec.data, 'tag': r.spec.tag,
                   'success': r.success, 'raw': r.raw, 'via': r.via} for r in results],
        'network_requests': client.network_requests,
        'scope': 'Independent inventory view checks; canonical historical swap semantics still require verification.'}, indent=2) + '\n')
    print(json.dumps({'checks': checks, 'network_requests': client.network_requests, 'evidence': str(output)}))
    return int(not all(checks.values()))


if __name__ == '__main__':
    raise SystemExit(main())
