"""Bounded state-only scheduling keeps one manifest writer and resumes failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import historical_study as study


def _config() -> dict:
    return {'scenarios': [{'token_in': 'WETH', 'token_out': 'USDC',
                           'amount': {'human': '1', 'raw': str(10**18)}}]}


def test_parallel_state_failure_resumes_without_recollecting_completed_blocks(tmp_path, monkeypatch) -> None:
    from swaparch import collection

    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    calls: list[int] = []
    failures = {2}

    def collect(number: int, **_options) -> dict:
        calls.append(number)
        if number in failures:
            failures.remove(number)
            raise RuntimeError('temporary RPC failure')
        evidence, snapshot = tmp_path / f'{number}.json.gz', tmp_path / f'snapshot-{number}'
        evidence.write_text('{}')
        snapshot.mkdir()
        return {'block': {'number': number, 'hash': str(number), 'chain': 1},
                'network_requests': 1, 'evidence': str(evidence), 'snapshot': str(snapshot), 'waves': [],
                'elapsed_seconds': 0, 'qualification_performed': False}

    monkeypatch.setattr(collection, 'collect_block', collect)
    kwargs = {'state_only': True, 'workers': 2, 'inventory_root': inventory,
              'header_hash': str, 'min_free_gb': 0}
    first = study.run_study([1, 2, 3], _config(), tmp_path / 'run', **kwargs)
    assert first['summary']['all_stage_coverage_complete'] is False
    assert json.loads((tmp_path / 'run/blocks/2.json').read_text())['stages']['aggregate_state']['status'] == 'failed'
    assert sorted(calls) == [1, 2, 3]

    resumed = study.run_study([1, 2, 3], _config(), tmp_path / 'run', **kwargs)
    assert resumed['summary']['all_stage_coverage_complete'] is True
    assert calls.count(1) == calls.count(3) == 1
    assert calls.count(2) == 2
    failed_attempts = (tmp_path / 'run/blocks/2.json').read_text()
    assert failed_attempts.count('"status": "failed"') == 1


def test_workers_are_bounded_and_state_only_only(tmp_path) -> None:
    with pytest.raises(ValueError, match='between 1 and 8'):
        study.run_study([1], _config(), tmp_path / 'run', state_only=True, workers=9)
    with pytest.raises(ValueError, match='state_only'):
        study.run_study([1], _config(), tmp_path / 'run', workers=2)
