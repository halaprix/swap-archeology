"""Re-read a qualification bundle's calls through separate batch/direct caches."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swaparch.core.types import BlockRef, CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3


def main(bundle, output):
    record = json.loads(bundle.read_text())
    block = BlockRef(**record['block'])
    specs = [CallSpec(**row['spec']) for row in record['calls']]
    batch_client = RpcClient(cache_root=output / 'batch-cache')
    direct_client = RpcClient(cache_root=output / 'direct-cache')
    for client in (batch_client, direct_client):
        if client.get_block(block.number).hash != block.hash:
            raise ValueError('qualification block hash changed')
    batch = Multicall3(batch_client).call(specs, block)
    direct = [direct_client.eth_call(s.to, s.data, block) for s in specs]
    checks = [{'spec': asdict(s), 'batch': asdict(b), 'direct': asdict(d),
               'matched': b.success and d.success and b.raw == d.raw == original['raw']}
              for s, b, d, original in zip(specs, batch, direct, record['calls'], strict=True)]
    output.mkdir(parents=True, exist_ok=True)
    (output / 'result.json').write_text(json.dumps({'block': asdict(block), 'bundle': str(bundle),
        'checks': checks, 'batch_network_requests': batch_client.network_requests,
        'direct_network_requests': direct_client.network_requests}, indent=2) + '\n')
    print(json.dumps({'checks': len(checks), 'matched': sum(c['matched'] for c in checks),
        'batch_network_requests': batch_client.network_requests,
        'direct_network_requests': direct_client.network_requests, 'output': str(output)}))
    return int(not checks or not all(c['matched'] for c in checks))


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
