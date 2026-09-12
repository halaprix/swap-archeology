"""The sampler preserves raw routes and refuses rate-limit bursts."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_sampling_and_rate_limit(tmp_path):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    with patch.object(sys, 'path', [str(scripts), *sys.path]):
        spec = importlib.util.spec_from_file_location('zeroex_sample', scripts / 'zeroex_sample.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    key = tmp_path / 'key'
    key.write_text('test-secret')
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {'route': {'fills': []}, 'echo': 'test-secret'}
    out = tmp_path / 'samples'
    argv = ['sample', '--key-file', str(key), '--output', str(out), '--rounds', '2']
    with patch.object(sys, 'argv', argv), patch.object(module.requests, 'Session', return_value=session), \
            patch.object(module.time, 'sleep'):
        module.main()
    assert session.get.call_count == 8
    records = [json.loads(p.read_text()) for p in sorted(out.glob('*.json'))]
    assert [r['sizeWeth'] for r in records] == [1, 10, 100, 1000] * 2
    assert all(r['params']['sellAmount'] == str(r['sizeWeth'] * 10**18) for r in records)
    assert all(r['historical'] is False and r['response']['echo'] == '[REDACTED]' for r in records)
    session.reset_mock()
    session.get.return_value.status_code = 429
    argv[4] = str(tmp_path / 'limited')
    with patch.object(sys, 'argv', argv), patch.object(module.requests, 'Session', return_value=session), \
            patch.object(module.time, 'sleep'), pytest.raises(SystemExit, match='429'):
        module.main()
    assert session.get.call_count == 1
