"""Discovery resume must retain historical filters and reject changed anchors."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import uniswap_v2_run as runner

from swaparch.core.types import BlockRef, Token


def test_discovery_warm_new_token_backfill_and_changed_anchor(tmp_path, monkeypatch):
    block = BlockRef(1, 25_896_003, '0xabc', 0)
    tokens = [Token(1, '0x' + f'{i:040x}', str(i), 18) for i in (1, 2)]
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps({'pools': [], 'coverage': []}))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'INVENTORY', inventory)
    monkeypatch.setattr(runner, 'load_inventory', lambda family: [SimpleNamespace(tokens=tokens)] if family == 'uniswap_v3' else [])
    calls = []

    class Client:
        def get_logs(self, address, topics, start, end, chunk):
            calls.append((start, end))
            return []

        def get_block(self, number):
            raise AssertionError('warm same-block discovery must not request headers')

    client = Client()
    runner.discover(client, block)
    assert calls == [(runner.FACTORY_CREATION_BLOCK, block.number)] * 2
    calls.clear()
    runner.discover(client, block)
    assert calls == []
    tokens.append(Token(1, '0x' + f'{3:040x}', '3', 18))
    runner.discover(client, block)
    assert calls == [(runner.FACTORY_CREATION_BLOCK, block.number)] * 4
    calls.clear()
    with pytest.raises(ValueError, match='coverage hash changed'):
        runner.discover(client, BlockRef(1, block.number, '0xdef', 0))
    assert calls == []
