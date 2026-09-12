"""Resume, identity invalidation, and failure classification for study orchestration."""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import historical_study as study


def test_state_only_batches_once_and_never_runs_qualification_or_quotes(tmp_path, monkeypatch):
    from swaparch import collection

    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    calls = []

    def collect(number, **options):
        calls.append((number, options['batch_size']))
        return {'network_requests': 3, 'evidence': 'state.json.gz', 'snapshot': 'snapshot',
                'waves': [], 'elapsed_seconds': 1, 'qualification_performed': False}

    monkeypatch.setattr(collection, 'collect_block', collect)
    monkeypatch.setattr(study, '_stage_functions', lambda: (_ for _ in ()).throw(AssertionError('qualification invoked')))
    kwargs = {'state_only': True, 'batch_size': 123, 'inventory_root': inventory,
              'header_hash': str, 'min_free_gb': 0,
              'quote_function': lambda *_: (_ for _ in ()).throw(AssertionError('quote invoked'))}
    config = {'scenarios': [{'token_in': 'WETH', 'token_out': 'USDC',
                            'amount': {'human': '1', 'raw': str(10**18)}}]}
    for _ in range(2):
        manifest = study.run_study([1, 2], config, tmp_path / 'run', **kwargs)
    assert calls == [(1, 123), (2, 123)]
    assert manifest['acquisition_mode'] == 'state_collection'
    assert manifest['quote_qualification_included'] is False
    assert manifest['summary']['completed'] == 0
    assert manifest['acquisition_network_requests']['known'] == 6


def test_acquisition_shards_advance_and_gzip_quotes_resume(tmp_path):
    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    config = {'scenarios': [{'token_in': 'WETH', 'token_out': 'USDC',
                            'amount': {'human': '1', 'raw': str(10**18)}}]}
    acquired, quoted = [], []

    def stage(number):
        acquired.append(number)
        print('{"network_requests": 1}')

    def quote(job, path):
        quoted.append(job['block'])
        path.write_text(json.dumps({'network_requests': 0, 'offline': True,
            'block': job['block'], 'block_hash': str(job['block']),
            'requested_solver': job['solver'], 'request': {
                'symbol_in': job['token_in'], 'symbol_out': job['token_out'],
                'amount_in': job['raw']}}))
        return 0

    kwargs = {'stage_functions': [('fake', stage)], 'quote_function': quote,
              'inventory_root': inventory, 'header_hash': str, 'min_free_gb': 0}
    output = tmp_path / 'run'
    for _ in range(2):
        manifest = study.run_study([1, 2], config, output, acquire=True,
                                  acquisition_only=True, max_blocks_this_run=1, **kwargs)
    assert acquired == [1, 2] and quoted == []
    assert manifest['summary']['all_stage_coverage_complete']
    assert all((output / row['shard']).is_file() for row in manifest['blocks'].values())
    manifest = study.run_study([1, 2], config, output, compress_reports=True, **kwargs)
    assert quoted == [1, 2] and manifest['summary']['completed'] == 2
    reports = list((output / 'quotes').glob('*.json.gz'))
    assert len(reports) == 2
    assert all(json.loads(gzip.decompress(path.read_bytes()))['offline'] for path in reports)
    study.run_study([1, 2], config, output, compress_reports=True, **kwargs)
    assert quoted == [1, 2]
    # The prior inline manifest format remains readable with the same semantics.
    manifest['version'] = 1
    manifest['blocks'] = {key: json.loads((output / row['shard']).read_text())
                          for key, row in manifest['blocks'].items()}
    (output / 'manifest.json').write_text(json.dumps(manifest))
    study.run_study([1, 2], config, output, compress_reports=True, **kwargs)
    assert quoted == [1, 2]


def test_new_adapter_stages_qualify_and_persist(monkeypatch, capsys):
    import fluid_dex_run
    import uniswap_v4_run

    calls = []

    def fluid(number, **kwargs):
        calls.append(("fluid", number, kwargs))
        return {"total_network_requests": 7}

    def v4(number, **kwargs):
        calls.append(("v4", number, kwargs))
        return {"network_requests": {"total": 9}}

    monkeypatch.setattr(fluid_dex_run, "run_block", fluid)
    monkeypatch.setattr(uniswap_v4_run, "qualify_block", v4)
    stages = dict(study._stage_functions())
    assert stages["fluid_dex"](23549991) == 0
    assert stages["uniswap_v4"](23549991) == 0
    assert all(number == 23549991 and options["online"] and options["write_inventory"]
               for _, number, options in calls)
    assert [json.loads(line)["network_requests"] for line in capsys.readouterr().out.splitlines()] == [7, 9]


def test_resume_invalidates_inventory_and_keeps_cache_misses_out_of_no_route(tmp_path):
    assert [name for name, _ in study._stage_functions()] == [name for name, _ in study.STAGE_MODULES]
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    (inventory / "one.json").write_text('{"family":"fixture"}\n')
    config = {"endpoints": study.DECIMALS, "scenarios": [{
        "token_in": "WETH", "token_out": "USDC",
        "amount": {"human": "1", "raw": "1000000000000000000"},
    }]}
    stages, quotes = [], []

    def stage(number):
        stages.append(number)
        print(json.dumps({"network_requests": 2, "evidence": f"raw/{number}.json"}))
        return 0

    def quote(job, output):
        quotes.append(job["block"])
        if job["block"] == 2 and quotes.count(2) == 1:
            raise RuntimeError("offline cache miss: eth_call")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "block": job["block"], "block_hash": f"0x{job['block']:064x}",
            "requested_solver": job["solver"], "offline": True, "network_requests": 0,
            "request": {"symbol_in": job["token_in"], "symbol_out": job["token_out"],
                        "amount_in": int(job["raw"])},
        }))
        return 0

    run = tmp_path / "run"
    header = lambda number: f"0x{number:064x}"
    first = study.run_study([1, 2], config, run, acquire=True, stage_functions=[("fake", stage)],
                            quote_function=quote, inventory_root=inventory, header_hash=header)
    assert first["summary"] == {"planned": 0, "completed": 1, "failed": 1, "total": 2,
                                 "remaining": 1, "all_stage_coverage_complete": True}
    block2 = json.loads((run / first["blocks"]["2"]["shard"]).read_text())
    assert block2["scenarios"]["WETH-USDC-1000000000000000000-search"]["failure_category"] == "offline_cache_missing"
    study.run_study([1, 2], config, run, acquire=True, stage_functions=[("fake", stage)],
                    quote_function=quote, inventory_root=inventory, header_hash=header)
    assert stages == [1, 2]
    assert quotes == [1, 2, 2]
    (inventory / "one.json").write_text('{"family":"changed"}\n')
    last = study.run_study([1, 2], config, run, acquire=True, stage_functions=[("fake", stage)],
                           quote_function=quote, inventory_root=inventory, header_hash=header)
    assert stages == [1, 2, 1, 2]
    assert quotes == [1, 2, 2, 1, 2]
    assert last["summary"] == {"planned": 0, "completed": 2, "failed": 0, "total": 2,
                                "remaining": 0, "all_stage_coverage_complete": True}
