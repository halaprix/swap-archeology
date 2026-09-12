"""Qualify ARM source-rate quotes and finite inventory against pinned raw reads.

ARM has no public quoter. These are independent Fraction/source-model checks,
not observed token transfers or atomic settlement.
"""
import json
import sys
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from swaparch.adapters.origin_arm import ARM, STETH, WETH, OriginArmAdapter
from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory

ROOT = Path(__file__).resolve().parents[1]


def main(number):
    client, adapter = RpcClient(), OriginArmAdapter()
    block = client.get_block(number)
    record = next(row for row in load_inventory(adapter.family) if row.pool == ARM)
    snapshot, _ = acquire({adapter.family: adapter}, [record], block, SnapshotStore(), client)
    state = adapter.load_state(record, snapshot)
    checks = []
    for source, target, rate, capacity in ((WETH, STETH, state.traderate0, state.reserve1),
                                         (STETH, WETH, state.traderate1, state.reserve0)):
        # Largest input whose floored source output is within the output reserve.
        limit = ((capacity + 1) * 10**36 - 1) // rate
        for amount in sorted({1, max(1, limit // 2), limit, limit + 1}):
            expected = int(Fraction(amount * rate, 10**36))
            allowed = (expected <= capacity and
                       (source != STETH or state.outstanding_withdrawals <= state.weth_balance))
            try:
                output, after = state.swap(source, target, amount)
                if source == WETH:
                    shares = state.steth_shares - int(Fraction(expected * state.total_shares,
                                                              state.total_pooled_ether))
                    weth = state.weth_balance + amount
                else:
                    shares = state.steth_shares + int(Fraction(amount * state.total_shares,
                                                              state.total_pooled_ether))
                    weth = state.weth_balance - expected
                matched = (allowed and output == expected and after.steth_shares == shares
                           and after.weth_balance == weth
                           and after.outstanding_withdrawals == state.outstanding_withdrawals)
                row = {'local_out': output, 'after_weth': weth, 'after_steth_shares': shares}
            except Unsupported as exc:
                matched = not allowed
                row = {'reason': str(exc)}
            checks.append({'token_in': source, 'token_out': target, 'amount_in': amount,
                           'reference_out': expected, 'capacity': capacity,
                           'expected_supported': allowed, 'matched': matched, **row})
    supported = all(row['matched'] for row in checks)
    path = ROOT / f'data/validation/origin-arm/{block.hash}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'block': asdict(block), 'pool': ARM,
        'calls': [asdict(snapshot.get(spec)) for spec in adapter.read_requests(record, block)],
        'checks': checks, 'reserve0': state.reserve0, 'reserve1': state.reserve1,
        'network_requests': client.network_requests,
        'scope': __doc__}, indent=2) + '\n')
    inventory_path = ROOT / 'data/discovery/1/origin_arm.json'
    inventory = json.loads(inventory_path.read_text())
    row = next(row for row in inventory['pools'] if row['pool'] == ARM)
    hashes = set(row['config'].get('validated_block_hashes', []))
    hashes.discard(block.hash)
    if supported:
        hashes.add(block.hash)
    row['config']['validated_block_hashes'] = sorted(hashes)
    row['status'] = 'supported' if hashes else 'discovered_unsupported'
    row['notes'] = 'Hash-qualified source-rate quote model and queue-subtracted finite inventory; no public quoter or settlement validation.'
    row['discovered_by']['evidence'] = sorted(set(row['discovered_by']['evidence'] + [str(path.relative_to(ROOT))]))
    inventory['status'] = 'supported' if hashes else 'discovered_unsupported'
    inventory_path.write_text(json.dumps(inventory, indent=1) + '\n')
    print(json.dumps({'block': number, 'checks': len(checks),
        'matched': sum(row['matched'] for row in checks), 'reserve0': state.reserve0,
        'reserve1': state.reserve1, 'network_requests': client.network_requests}), flush=True)
    return int(not supported)


if __name__ == '__main__':
    raise SystemExit(max(main(int(number)) for number in sys.argv[1:] or ['25896003']))
