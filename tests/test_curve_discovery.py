"""Registry index duplicates and unknown activation must survive discovery."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from eth_abi import decode, encode

from swaparch.core.types import BlockRef, CallResult, Token

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import curve_discovery_run as runner


def test_registry_enumeration_deduplicates_and_records_activation_upper_bound(tmp_path, monkeypatch):
    a, b, pool, irrelevant, handler, registry = ['0x' + f'{i:040x}' for i in range(1, 7)]
    block = BlockRef(1, 100, '0xabc', 0)
    path = tmp_path / 'data/discovery/1/curve.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'pools': [], 'coverage': [], 'unresolved': []}))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'RpcClient', lambda: SimpleNamespace(get_block=lambda _: block, network_requests=0))
    monkeypatch.setattr(runner, 'load_inventory', lambda _: [SimpleNamespace(tokens=(
        Token(1, a, 'A', 18), Token(1, b, 'B', 6)))])

    class Batch:
        def call(self, specs, block):
            results = []
            for spec in specs:
                signature = spec.tag
                if signature in ('pool_count()', 'registry_length()'):
                    abi, value = 'uint256', 3 if signature == 'pool_count()' else 1
                elif signature == 'get_registry(uint256)':
                    abi, value = 'address', handler
                elif signature == 'get_base_registry(address)':
                    abi, value = 'address', registry
                elif signature == 'pool_list(uint256)':
                    i = decode(['uint256'], bytes.fromhex(spec.data[10:]))[0]
                    abi, value = 'address', [pool, irrelevant, pool][i]
                elif signature in ('get_coins(address)', 'get_underlying_coins(address)'):
                    target = decode(['address'], bytes.fromhex(spec.data[10:]))[0]
                    abi, value = 'address[8]', ([a, b] if target == pool else [a, registry]) + [runner.ZERO] * 6
                elif signature == 'get_decimals(address)':
                    abi, value = 'uint256[8]', [18, 6] + [0] * 6
                elif signature == 'get_base_pool(address)':
                    abi, value = 'address', runner.ZERO
                else:
                    assert signature == 'get_registry_handlers_from_pool(address)'
                    abi, value = 'address[10]', [handler] + [runner.ZERO] * 9
                results.append(CallResult(spec, True, '0x' + encode([abi], [value]).hex(), 'fixture'))
            return results

    monkeypatch.setattr(runner, 'Multicall3', lambda *args, **kwargs: Batch())
    assert runner.main(100) == 0
    data = json.loads(path.read_text())
    assert len(data['pools']) == 1
    assert data['pools'][0]['pool'] == pool
    assert data['pools'][0]['created_block'] is None
    assert data['pools'][0]['discovered_by']['deployed_by_block'] == 100
    assert data['pools'][0]['status'] == 'discovered_unsupported'
    assert data['coverage'][0]['enumerated_indexes'] == 3
    assert data['coverage'][0]['unique_addresses'] == 2
