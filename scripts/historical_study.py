"""Serial, restartable orchestration for the historical routing study.

Quotes are always offline: ``--acquire`` only runs the existing qualification
commands once per block, then every scenario reads their pinned cache.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import importlib
import io
import json
import re
import shutil
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = (23549991, 23550060, 24356381, 23728292, 25896003)
WINDOWS = {
    "crash1": (23548634, 23550420),
    "crash2": (24355465, 24357132),
    "crash3": (23726960, 23728635),
    "calm24h": (25760917, 25768095),
    "sUSDe": (21895170, 21896367),
}
DECIMALS = {"WETH": 18, "wstETH": 18, "sUSDe": 18, "USDC": 6, "USDT": 6, "DAI": 18}
STAGE_MODULES = (
    ("uniswap_v3", "validate_v3_universe"),
    ("uniswap_v2", "uniswap_v2_run"),
    ("curve_registry", "curve_discovery_run"),
    ("curve_ng_probe", "curve_ng_probe"),
    ("curve_ng", "curve_ng_run"),
    ("litepsm", "litepsm_run"),
    ("lido", "lido_run"),
    ("origin_arm_probe", "origin_arm_probe"),
    ("origin_arm", "origin_arm_run"),
    ("uniswap_v4", "uniswap_v4_run"),
    ("fluid_dex", "fluid_dex_run"),
    ("aave_reference", "aave_reference_run"),
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path: Path, value: object) -> None:
    if isinstance(value, dict) and 'scenario_keys' in value:
        _summarize(value, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _summarize(manifest: dict, root: Path) -> None:
    if manifest.get('version') == 2:
        total = len(manifest['planned_blocks']) * len(manifest['scenario_keys'])
        rows = list(manifest['blocks'].values())
        completed = sum(row.get('completed', 0) for row in rows)
        failed = sum(row.get('failed', 0) for row in rows)
        manifest['planned_total'] = total
        manifest['summary'] = {
            'planned': total - completed - failed, 'completed': completed, 'failed': failed,
            'total': total, 'remaining': total - completed,
            'all_stage_coverage_complete': len(rows) == len(manifest['planned_blocks'])
            and all(row.get('coverage_complete', False) for row in rows),
        }
        manifest['acquisition_network_requests'] = {
            'known': sum(row.get('requests_known', 0) for row in rows),
            'unknown_stages': sum(row.get('requests_unknown', 0) for row in rows),
            'counting': 'all stage attempts, summarized per block',
        }
        return
    def block(number: int) -> dict:
        value = manifest['blocks'].get(str(number), {})
        if manifest.get('version') == 2 and value.get('shard'):
            try:
                return json.loads((root / value['shard']).read_text())
            except (OSError, json.JSONDecodeError):
                return {}
        return value
    entries = [block(number).get('scenarios', {}).get(key, {})
               for number in manifest['planned_blocks'] for key in manifest['scenario_keys']]
    statuses = [entry.get('status', 'planned') for entry in entries]
    manifest['planned_total'] = len(entries)
    manifest['summary'] = {status: statuses.count(status)
                           for status in ('planned', 'completed', 'failed')}
    manifest['summary'].update(total=len(entries), remaining=len(entries) - statuses.count('completed'),
        all_stage_coverage_complete=bool(entries) and all(
            entry.get('coverage', {}).get('complete', False) for entry in entries))
    counts = [attempt.get('network_requests') for block in (block(number) for number in manifest['planned_blocks'])
              for row in block.get('stages', {}).values() for attempt in row.get('attempts', [row])]
    manifest['acquisition_network_requests'] = {
        'known': sum(value for value in counts if isinstance(value, int)),
        'unknown_stages': sum(value is None for value in counts),
        'counting': 'all stage attempts; unknown_stages counts attempts with unknown requests',
    }


def _checkpoint_block(output: Path, manifest: dict, number: int, block: dict) -> None:
    if manifest.get('version') != 2:
        manifest['blocks'][str(number)] = block
        _write(output / 'manifest.json', manifest)
        return
    shard = Path('blocks') / f'{number}.json'
    _write(output / shard, block)
    entries = list(block.get('scenarios', {}).values())
    counts = [attempt.get('network_requests') for row in block.get('stages', {}).values()
              for attempt in row.get('attempts', [row])]
    manifest['blocks'][str(number)] = {
        'shard': str(shard), 'completed': sum(row.get('status') == 'completed' for row in entries),
        'failed': sum(row.get('status') == 'failed' for row in entries),
        'coverage_complete': block.get('coverage', {}).get('complete', False),
        'requests_known': sum(value for value in counts if type(value) is int),
        'requests_unknown': sum(value is None for value in counts),
    }


def _report_bytes(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()


def _effective_inventory_identity(block_hash: str | None,
                                  root: Path = ROOT / "data/discovery/1") -> str:
    """Hash only the inventory facts that can affect this particular block's quote."""
    def record(row: dict) -> dict:
        config = dict(row.get("config", {}))
        validated = set(config.pop("validated_block_hashes", []))
        for key in ("registry_observations", "historical_observations", "resolver_observations"):
            observations = config.get(key, {})
            config[key] = ({block_hash: observations[block_hash]}
                           if block_hash and block_hash in observations else {})
        return {"family": row.get("family"), "chain": row.get("chain"),
                "pool_id": row.get("pool_id"), "deployment": row.get("deployment"),
                "pool": row.get("pool"), "tokens": row.get("tokens"),
                "status": row.get("status"), "notes": row.get("notes"),
                "created_block": row.get("created_block"),
                "discovered_by": {key: value for key, value in row.get("discovered_by", {}).items()
                                  if key != "evidence"},
                "config": config, "qualified_here": bool(block_hash in validated)}

    inventories = {}
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text())
        inventories[path.name] = {"family": data.get("family"),
                                  "status": data.get("status"),
                                  "unresolved": data.get("unresolved"),
                                  "pools": [record(row) for row in data.get("pools", [])]}
    return _digest(inventories)


def _code_identity() -> str:
    paths = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "scripts/historical_study.py"]
    paths += sorted((ROOT / "src/swaparch").rglob("*.py"))
    paths += sorted((ROOT / "scripts").glob("*.py"))
    return _digest({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in paths if path.is_file()})


def _cached_header_hash(number: int) -> str | None:
    path = ROOT / "data/rpc-cache/headers/1" / f"{number}.json"
    try:
        return str(json.loads(path.read_text())["response"]["result"]["hash"]).lower()
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _raw_amount(human: str, decimals: int) -> str:
    try:
        value = Decimal(human)
        if not value.is_finite() or value <= 0 or not 0 <= value.adjusted() + decimals <= 76:
            raise ValueError
        numerator, denominator = value.as_integer_ratio()
    except (InvalidOperation, OverflowError, ValueError):
        raise ValueError(f"invalid exact amount: {human}") from None
    raw, remainder = divmod(numerator * 10 ** decimals, denominator)
    if raw >= 2 ** 255 or remainder:
        raise ValueError(f"amount is not a positive {decimals}-decimal value: {human}")
    return str(raw)


def default_config() -> dict:
    ladders = {
        "WETH": ("0.1", "1", "10", "100"),
        "wstETH": ("0.1", "1", "10", "100"),
        "sUSDe": ("100", "1000", "10000", "100000"),
        "USDC": ("100", "1000", "10000", "100000"),
        "USDT": ("100", "1000", "10000", "100000"),
        "DAI": ("100", "1000", "10000", "100000"),
    }
    forward = (("WETH", "USDC"), ("WETH", "USDT"), ("WETH", "DAI"),
               ("wstETH", "WETH"), ("wstETH", "USDC"),
               ("sUSDe", "USDC"), ("sUSDe", "USDT"), ("sUSDe", "DAI"),
               ("USDC", "USDT"), ("USDC", "DAI"), ("USDT", "DAI"))
    pairs = [*forward, *((out, incoming) for incoming, out in forward)]
    return {
        "endpoints": DECIMALS,
        "pairs": [[incoming, out] for incoming, out in pairs],
        "size_ladders": {token: [{"human": amount, "raw": _raw_amount(amount, DECIMALS[token])}
                                   for amount in amounts] for token, amounts in ladders.items()},
    }


def scenarios(config: dict) -> list[dict]:
    if not isinstance(config, dict):
        raise TypeError("scenario config must be a JSON object")
    endpoints = config.get("endpoints", DECIMALS)
    if endpoints != DECIMALS:
        raise ValueError("scenario endpoints must be the six canonical symbols and decimals")
    rows = config.get("scenarios")
    if rows is None:
        rows = [{"token_in": incoming, "token_out": out, "amount": amount}
                for incoming, out in config["pairs"]
                for amount in config["size_ladders"][incoming]]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("each scenario must be an object")
        incoming, out = row["token_in"], row["token_out"]
        amount = row["amount"]
        if incoming not in DECIMALS or out not in DECIMALS or incoming == out:
            raise ValueError(f"invalid endpoint pair: {incoming}->{out}")
        if (not isinstance(amount, dict) or set(amount) != {"human", "raw"}
                or str(amount["raw"]) != _raw_amount(str(amount["human"]), DECIMALS[incoming])):
            raise ValueError(f"raw amount does not exactly match {incoming}: {amount}")
        result.append({"token_in": incoming, "token_out": out,
                       "human": str(amount["human"]), "raw": str(amount["raw"])})
    if not result:
        raise ValueError("scenario config has no scenarios")
    if len({(row['token_in'], row['token_out'], row['raw']) for row in result}) != len(result):
        raise ValueError('scenario config contains duplicate semantic jobs')
    return result


def select_blocks(blocks: str | None, windows: Iterable[str], start: int | None, end: int | None) -> list[int]:
    selected: set[int] = set()
    if blocks:
        selected.update(int(value.strip()) for value in blocks.split(",") if value.strip())
    for window in windows:
        names = WINDOWS if window == "all" else {window: WINDOWS[window]}
        for first, last in names.values():
            selected.update(range(first, last + 1))
    if (start is None) != (end is None):
        raise ValueError("--start and --end must be supplied together")
    if start is not None:
        if start > end:
            raise ValueError("--start must not exceed --end")
        selected.update(range(start, end + 1))
    selected = selected or set(PINS)
    if min(selected) < 0:
        raise ValueError("block numbers must be non-negative")
    return sorted(selected)


def _stage_functions() -> list[tuple[str, Callable[[int], int | None]]]:
    stages = []
    for name, module_name in STAGE_MODULES:
        module = importlib.import_module(module_name)
        if name == "uniswap_v2":
            def run_v2(number: int, module=module) -> int:
                client = module.RpcClient()
                block = client.get_block(number)
                module.qualify(client, block, module.discover(client, block))
                return 0
            stages.append((name, run_v2))
        elif name in ("fluid_dex", "uniswap_v4"):
            def run_new_adapter(number: int, module=module, family=name) -> int:
                if family == "fluid_dex":
                    report = module.run_block(number, online=True, write_inventory=True)
                    report["network_requests"] = report.get("total_network_requests", 0)
                else:
                    cached = (ROOT / "data/discovery-evidence/uniswap-v4"
                              / f"{_cached_header_hash(25896003)}.json")
                    report = module.qualify_block(
                        number, online=True, write_inventory=True,
                        discovery_report=cached if cached.is_file() and number <= 25896003 else None,
                    )
                    report["network_requests"] = report["network_requests"]["total"]
                print(json.dumps(report))
                return 0
            stages.append((name, run_new_adapter))
        else:
            stages.append((name, module.main))
    return stages


def _safe_error(error: BaseException) -> str:
    return re.sub(r"https?://[^\s'\"]+", "<redacted-url>", f"{type(error).__name__}: {error}")


def _json_rows(text: str) -> list[dict]:
    decoder, rows, index = json.JSONDecoder(), [], 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return rows
        try:
            value, index = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            rows.append(value)


def _quote(job: dict, output: Path) -> int:
    from swaparch.cli import quote_command

    return quote_command(job["block"], job["token_in"], job["token_out"], job["human"],
                         job["grid_parts"], output, solver_name=job["solver"],
                         max_steps=job["max_steps"], beam_width=job["beam_width"],
                         max_expansions=job["max_expansions"], offline=True)


def _collection_model_quote(job: dict, output: Path) -> int:
    from swaparch.cli import quote_command
    from swaparch.collection_quotes import prepared_collection_context

    context, annotations, _client = prepared_collection_context(job["block"])
    families = {row["family"] for row in context.inventories}
    annotations["collection_quote_code_identity"] = job["code_identity"]
    return quote_command(job["block"], job["token_in"], job["token_out"], job["human"],
                         job["grid_parts"], output, solver_name=job["solver"],
                         max_steps=job["max_steps"], beam_width=job["beam_width"],
                         max_expansions=job["max_expansions"], families=tuple(families), offline=True,
                         prepared_context=context, report_annotations=annotations)


def _collection_report(path: str | None) -> dict | None:
    """Load a prior state report only as a fresh-read request-plan seed."""
    if not path:
        return None
    try:
        raw = Path(path).read_bytes()
        return json.loads(gzip.decompress(raw) if path.endswith('.gz') else raw)
    except (OSError, EOFError, json.JSONDecodeError, gzip.BadGzipFile):
        return None


def _parallel_state_collection(manifest: dict, output: Path, *, workers: int, batch_size: int,
                               inventory_root: Path, code_id: str,
                               min_free_gb: int, max_blocks_this_run: int | None,
                               header_hash: Callable[[int], str | None]) -> dict:
    """Collect independent blocks concurrently; this thread is the sole manifest writer."""
    from swaparch.collection import collect_block

    def stored_block(number: int) -> dict:
        stored = manifest['blocks'].get(str(number), {})
        if manifest.get('version') == 2 and stored.get('shard'):
            return json.loads((output / stored['shard']).read_text())
        return manifest['blocks'].setdefault(str(number), {'stages': {}, 'scenarios': {}})

    def ready(block: dict, before: str, block_hash: str | None) -> bool:
        row = block.get('stages', {}).get('aggregate_state', {})
        artifacts = row.get('raw_paths', [])
        return (block.get('stage_inventory_identity') == before and row.get('status') == 'completed'
                and row.get('code_identity') == code_id and row.get('block_hash') == block_hash
                and bool(artifacts) and all(Path(path).exists() for path in artifacts))

    def record(block: dict, number: int, before: str, report: dict | None, error: BaseException | None) -> None:
        prior = block['stages'].get('aggregate_state', {})
        attempts = prior.get('attempts', [dict(prior)] if prior else [])
        if attempts and attempts[-1].get('status') == 'running':
            attempts = attempts[:-1]
        log = output / 'logs' / f'{number}-aggregate_state-{len(attempts) + 1}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        if error is None and report is not None:
            log.write_text(json.dumps({key: report.get(key) for key in
                                       ('network_requests', 'evidence', 'snapshot', 'waves', 'elapsed_seconds',
                                        'qualification_performed')}) + '\n')
            row = {'status': 'completed', 'return_code': 0, 'log': str(log), 'code_identity': code_id,
                   'block_hash': header_hash(number), 'network_requests': report.get('network_requests'),
                   'raw_paths': [report[key] for key in ('evidence', 'snapshot') if report.get(key)]}
            block.setdefault('acquisition_raw_paths', []).extend(row['raw_paths'])
            block['stage_inventory_identity'] = before
        else:
            log.write_text(_safe_error(error) + '\n')
            row = {'status': 'failed', 'log': str(log), 'code_identity': code_id,
                   'block_hash': header_hash(number), 'error': _safe_error(error),
                   'network_requests': None, 'raw_paths': []}
        row['attempts'] = attempts + [dict(row)]
        block['stages']['aggregate_state'] = row
        inventory_id = _effective_inventory_identity(header_hash(number), inventory_root)
        block['inventory_identity'] = inventory_id
        block['coverage'] = {
            'complete': row['status'] == 'completed' and block.get('stage_inventory_identity') == inventory_id,
            'failed_stages': [] if row['status'] == 'completed' else ['aggregate_state'],
            'missing_stages': [],
            'stale_stages': ([] if row['status'] == 'completed'
                             and block.get('stage_inventory_identity') == inventory_id else ['aggregate_state']),
        }
        _checkpoint_block(output, manifest, number, block)
        _write(output / 'manifest.json', manifest)

    def begin(block: dict, number: int) -> None:
        prior = block['stages'].get('aggregate_state', {})
        attempts = prior.get('attempts', [dict(prior)] if prior else [])
        running = {'status': 'running', 'log': '', 'code_identity': code_id,
                   'block_hash': header_hash(number), 'network_requests': None, 'raw_paths': []}
        block['stages']['aggregate_state'] = {**running, 'attempts': attempts + [running]}
        _checkpoint_block(output, manifest, number, block)
        _write(output / 'manifest.json', manifest)

    seed: dict | None = None
    processed = 0
    scheduling_stopped = False
    planned = iter(manifest['planned_blocks'])

    def next_pending() -> tuple[int, dict, str, dict | None] | None:
        """Return one unsatisfied block, checking disk space at actual submission time."""
        nonlocal processed, seed, scheduling_stopped
        if scheduling_stopped or (max_blocks_this_run is not None and processed >= max_blocks_this_run):
            return None
        for number in planned:
            block = stored_block(number)
            block_hash = header_hash(number)
            before = _effective_inventory_identity(block_hash, inventory_root)
            if ready(block, before, block_hash):
                report = _collection_report(block['stages']['aggregate_state']['raw_paths'][0])
                if report is not None:
                    seed = report
                _checkpoint_block(output, manifest, number, block)
                continue
            if max_blocks_this_run is not None and processed >= max_blocks_this_run:
                return None
            disk_path = output if output.exists() else output.parent
            while not disk_path.exists():
                disk_path = disk_path.parent
            if shutil.disk_usage(disk_path.resolve()).free < min_free_gb * 1024**3:
                scheduling_stopped = True
                manifest['stopped'] = {'reason': 'free_space_below_threshold', 'next_block': number,
                                       'min_free_gb': min_free_gb}
                _write(output / 'manifest.json', manifest)
                return None
            processed += 1
            return number, block, before, seed
        return None

    # Bootstrap one block so all concurrent workers share only immutable request identities.
    first = next_pending()
    if first is None:
        _write(output / 'manifest.json', manifest)
        return manifest
    number, block, before, bootstrap_seed = first
    begin(block, number)
    try:
        seed = collect_block(number, batch_size=batch_size, inventory_root=inventory_root,
                             previous_report=bootstrap_seed)
        record(block, number, before, seed, None)
    except BaseException as error:
        record(block, number, before, None, error)
        if isinstance(error, KeyboardInterrupt):
            raise

    def collect(number: int, task_seed: dict | None) -> dict:
        return collect_block(number, batch_size=batch_size, inventory_root=inventory_root,
                             previous_report=task_seed)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='state-collection') as executor:
        active: dict[Future[dict], tuple[int, dict, str]] = {}
        while True:
            while len(active) < workers:
                pending = next_pending()
                if pending is None:
                    break
                number, block, before, task_seed = pending
                begin(block, number)
                active[executor.submit(collect, number, task_seed)] = (number, block, before)
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                number, block, before = active.pop(future)
                try:
                    report = future.result()
                    record(block, number, before, report, None)
                    if seed is None or report['block']['number'] > seed['block']['number']:
                        seed = report
                except BaseException as error:
                    record(block, number, before, None, error)
                    if isinstance(error, KeyboardInterrupt):
                        raise
    _write(output / 'manifest.json', manifest)
    return manifest


def _complete(entry: dict | None, job: dict, *, config_id: str, code_id: str,
              inventory_id: str, header_hash: str | None) -> bool:
    if not entry or entry.get("status") != "completed":
        return False
    if (entry.get("config_identity") != config_id or entry.get("code_identity") != code_id
            or entry.get("inventory_identity") != inventory_id):
        return False
    report = Path(entry.get("report", ""))
    if not report.is_file():
        return False
    try:
        raw = _report_bytes(report)
        value = json.loads(raw)
        request = value.get("request", {})
        return (entry.get("report_digest") == hashlib.sha256(raw).hexdigest()
                and value.get("block_hash") == entry.get("block_hash") == header_hash
                and value.get("block") == job["block"] and value.get("requested_solver") == job["solver"]
                and value.get("offline") is True and request.get("symbol_in") == job["token_in"]
                and request.get("symbol_out") == job["token_out"]
                and str(request.get("amount_in")) == job["raw"])
    except (OSError, EOFError, json.JSONDecodeError):
        return False


def run_study(block_numbers: Iterable[int], config: dict, output: Path, *, acquire: bool = False,
              acquisition_only: bool = False, max_blocks_this_run: int | None = None,
              state_only: bool = False, batch_size: int = 4000,
              collection_model_only: bool = False,
              workers: int = 1,
              min_free_gb: int = 2, compress_reports: bool = False,
              solvers: Iterable[str] = ("search",), grid_parts: int = 10, max_steps: int = 8,
              beam_width: int = 128, max_expansions: int = 5000,
              stage_functions: list[tuple[str, Callable[[int], int | None]]] | None = None,
              quote_function: Callable[[dict, Path], int] = _quote,
              inventory_root: Path = ROOT / "data/discovery/1",
              header_hash: Callable[[int], str | None] = _cached_header_hash) -> dict:
    block_numbers = list(block_numbers)
    if state_only and collection_model_only:
        raise ValueError("collection_model_only cannot be combined with state_only")
    if state_only:
        acquire = acquisition_only = True
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if workers != 1 and not state_only:
        raise ValueError("workers is supported only with state_only")
    if acquisition_only and not acquire:
        raise ValueError("acquisition_only requires acquire")
    if max_blocks_this_run is not None and max_blocks_this_run < 1:
        raise ValueError("max_blocks_this_run must be positive")
    if min_free_gb < 0:
        raise ValueError("min_free_gb must be non-negative")
    solvers = tuple(solvers)
    if not solvers or set(solvers) - {"baseline", "search", "dual"}:
        raise ValueError("solvers must be drawn from baseline, search, dual")
    if len(set(solvers)) != len(solvers):
        raise ValueError('duplicate solver jobs')
    if min(grid_parts, max_steps, beam_width, max_expansions) < 1:
        raise ValueError("solver budgets must be positive")
    scenario_rows = scenarios(config)
    code_id = _code_identity()
    config_id = _digest({"scenarios": scenario_rows, "solvers": solvers, "grid_parts": grid_parts,
                         "max_steps": max_steps, "beam_width": beam_width,
                         "max_expansions": max_expansions, "state_only": state_only,
                         "batch_size": batch_size, "collection_model_only": collection_model_only})
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "version": 2, "code_identity": code_id, "config_identity": config_id,
        "planned_blocks": block_numbers, "blocks": {},
    }
    if manifest["code_identity"] != code_id or manifest["config_identity"] != config_id:
        raise ValueError("existing run has different code or scenario/solver configuration; choose a new --run-id")
    if manifest["planned_blocks"] != block_numbers:
        raise ValueError("existing run has different planned blocks; choose a new --run-id")
    manifest['scenario_keys'] = [f"{row['token_in']}-{row['token_out']}-{row['raw']}-{solver}"
                                 for row in scenario_rows for solver in solvers]
    manifest['acquisition_mode'] = ('state_collection' if state_only else
                                    'collection_model_only' if collection_model_only else 'qualification')
    manifest['quote_qualification_included'] = not state_only and not collection_model_only
    _write(manifest_path, manifest)
    stages = stage_functions if stage_functions is not None else (_stage_functions() if acquire and not state_only else [])
    if collection_model_only:
        if acquire or acquisition_only:
            raise ValueError("collection_model_only is offline and cannot acquire")
        if quote_function is _quote:
            quote_function = _collection_model_quote
    if state_only and stage_functions is None:
        from swaparch.collection import collect_block
        previous_report = None

        def collect(number):
            nonlocal previous_report
            report = collect_block(number, batch_size=batch_size, inventory_root=inventory_root,
                                   previous_report=previous_report)
            previous_report = report
            print(json.dumps({key: report[key] for key in
                              ('network_requests', 'evidence', 'snapshot', 'waves', 'elapsed_seconds',
                               'qualification_performed')}))
            return 0

        stages = [('aggregate_state', collect)]
    if state_only and workers > 1:
        return _parallel_state_collection(
            manifest, output, workers=workers, batch_size=batch_size, inventory_root=inventory_root,
            code_id=code_id, min_free_gb=min_free_gb, max_blocks_this_run=max_blocks_this_run,
            header_hash=header_hash,
        )
    processed = 0
    for number in manifest["planned_blocks"]:
        disk_path = output if output.exists() else output.parent
        while not disk_path.exists():
            disk_path = disk_path.parent
        if shutil.disk_usage(disk_path.resolve()).free < min_free_gb * 1024**3:
            manifest["stopped"] = {"reason": "free_space_below_threshold", "next_block": number,
                                   "min_free_gb": min_free_gb}
            _write(manifest_path, manifest)
            break
        stored = manifest["blocks"].get(str(number), {})
        if manifest.get("version") == 2 and stored.get("shard"):
            block = json.loads((output / stored["shard"]).read_text())
        else:
            block = manifest["blocks"].setdefault(str(number), {"stages": {}, "scenarios": {}})
        cached_hash = header_hash(number)
        before = _effective_inventory_identity(cached_hash, inventory_root)
        stage_rows = block["stages"]
        stages_ready = (block.get("stage_inventory_identity") == before and all(
            stage_rows.get(name, {}).get("status") == "completed"
            and stage_rows[name].get("code_identity") == code_id
            and stage_rows[name].get("block_hash") == cached_hash
            for name, _ in stages))
        if acquisition_only and stages_ready:
            _checkpoint_block(output, manifest, number, block)
            continue
        if max_blocks_this_run is not None and processed >= max_blocks_this_run:
            break
        processed += 1
        _checkpoint_block(output, manifest, number, block)
        _write(manifest_path, manifest)
        if acquire and not stages_ready:
            for name, stage in stages:
                prior = stage_rows.get(name, {})
                if (prior.get("status") == "completed" and prior.get("code_identity") == code_id
                        and prior.get("block_hash") == cached_hash
                        and block.get("stage_inventory_identity") == before):
                    continue
                attempts = prior.get("attempts", [dict(prior)] if prior else [])
                stage_log = output / "logs" / f"{number}-{name}-{len(attempts) + 1}.log"
                captured = io.StringIO()
                running = {'status': 'running', 'log': str(stage_log),
                           'code_identity': code_id, 'block_hash': header_hash(number),
                           'network_requests': None, 'raw_paths': []}
                stage_rows[name] = {**running, 'attempts': attempts + [running]}
                _checkpoint_block(output, manifest, number, block)
                try:
                    with contextlib.redirect_stdout(captured):
                        code = stage(number)
                    stage_log.parent.mkdir(parents=True, exist_ok=True)
                    stage_log.write_text(captured.getvalue())
                    rows = _json_rows(captured.getvalue())
                    counts = [int(row["network_requests"]) for row in rows if "network_requests" in row]
                    requests = sum(counts) if code in (None, 0) and counts else None
                    stage_rows[name] = {"status": "completed" if code in (None, 0) else "failed",
                                             "return_code": code, "log": str(stage_log), "code_identity": code_id,
                                             "block_hash": header_hash(number), "network_requests": requests,
                                             "raw_paths": [row[key] for row in rows for key in ("evidence", "snapshot") if key in row]}
                    block.setdefault("acquisition_raw_paths", []).extend(
                        stage_rows[name]["raw_paths"])
                except Exception as error:  # noqa: BLE001 - retain a stage boundary failure in the manifest
                    stage_log.parent.mkdir(parents=True, exist_ok=True)
                    stage_log.write_text(captured.getvalue())
                    stage_rows[name] = {"status": "failed", "log": str(stage_log),
                                        "code_identity": code_id, "block_hash": header_hash(number),
                                        "error": _safe_error(error), "network_requests": None, "raw_paths": []}
                except BaseException:
                    _checkpoint_block(output, manifest, number, block)
                    _write(manifest_path, manifest)
                    raise
                stage_rows[name]["attempts"] = attempts + [dict(stage_rows[name])]
                _checkpoint_block(output, manifest, number, block)
            cached_hash = header_hash(number)
            block["stage_inventory_identity"] = _effective_inventory_identity(cached_hash, inventory_root)
        inventory_id = _effective_inventory_identity(cached_hash, inventory_root)
        if collection_model_only:
            from swaparch.collection_quotes import collection_artifact_identity
            inventory_id = collection_artifact_identity(number)
        block["inventory_identity"] = inventory_id
        failed_stages = sorted(name for name, row in stage_rows.items() if row.get("status") != "completed")
        expected_stages = set() if collection_model_only else {name for name, _ in (stages or STAGE_MODULES)}
        missing_stages = sorted(expected_stages - set(stage_rows))
        stale_stages = sorted(name for name, row in stage_rows.items()
                              if row.get('code_identity') != code_id or
                              row.get('block_hash') != cached_hash or
                              block.get('stage_inventory_identity') != inventory_id)
        coverage = {'complete': not failed_stages and not missing_stages and not stale_stages,
                    'failed_stages': failed_stages, 'missing_stages': missing_stages,
                    'stale_stages': stale_stages}
        block['coverage'] = coverage
        if acquisition_only:
            _checkpoint_block(output, manifest, number, block)
            _write(manifest_path, manifest)
            continue
        for scenario in scenario_rows:
            for solver in solvers:
                job = {"block": number, **scenario, "solver": solver, "grid_parts": grid_parts,
                       "code_identity": code_id,
                       "max_steps": max_steps, "beam_width": beam_width, "max_expansions": max_expansions}
                key = f"{scenario['token_in']}-{scenario['token_out']}-{scenario['raw']}-{solver}"
                entry = block["scenarios"].get(key)
                if _complete(entry, job, config_id=config_id, code_id=code_id,
                             inventory_id=inventory_id, header_hash=cached_hash):
                    entry['coverage'] = dict(coverage)
                    continue
                report = output / "quotes" / f"{number}-{key}.json"
                if compress_reports:
                    report = report.with_suffix('.json.gz')
                pending = output / '.pending' / f'{number}-{key}.json'
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.unlink(missing_ok=True)
                log = output / "logs" / f"{number}-{key}.log"
                block["scenarios"][key] = {"status": "planned", "report": str(report),
                                            "config_identity": config_id, "code_identity": code_id,
                                            "inventory_identity": inventory_id,
                                            "coverage": dict(coverage)}
                captured = io.StringIO()
                try:
                    with contextlib.redirect_stdout(captured):
                        code = quote_function(job, pending)
                    raw_report = pending.read_bytes() if pending.is_file() else None
                    result = json.loads(raw_report) if raw_report is not None else None
                    if result is None:
                        raise RuntimeError(f"quote returned {code} without a report")
                    if result.get("network_requests") != 0:
                        raise RuntimeError("offline optimizer reported network requests")
                    report.parent.mkdir(parents=True, exist_ok=True)
                    if compress_reports:
                        temporary = report.with_suffix('.gz.tmp')
                        temporary.write_bytes(gzip.compress(raw_report, compresslevel=6, mtime=0))
                        temporary.replace(report)
                        pending.unlink()
                    else:
                        pending.replace(report)
                    log.parent.mkdir(parents=True, exist_ok=True)
                    log.write_text(json.dumps({'return_code': code, 'report': str(report)}) + '\n')
                    block["scenarios"][key].update(status="completed", return_code=code,
                        outcome="quoted" if code == 0 else "no_tested_route", log=str(log),
                        block_hash=result.get("block_hash"),
                        report_digest=hashlib.sha256(raw_report).hexdigest(),
                        raw_paths=[result["snapshot"]] if result.get("snapshot") else [])
                except Exception as error:  # noqa: BLE001 - cache/input failures must be restartable
                    log.parent.mkdir(parents=True, exist_ok=True)
                    log.write_text(captured.getvalue())
                    category = "offline_cache_missing" if "offline cache miss" in str(error).lower() else "quote_failure"
                    block["scenarios"][key].update(status="failed", log=str(log), error=_safe_error(error),
                                                       failure_category=category, raw_paths=[])
                _checkpoint_block(output, manifest, number, block)
        _checkpoint_block(output, manifest, number, block)
        _write(manifest_path, manifest)
    _write(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", help="comma-separated explicit block numbers")
    parser.add_argument("--window", action="append", choices=(*WINDOWS, "all"), default=[])
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--acquire", action="store_true", help="run serial qualification stages once per block")
    parser.add_argument("--acquire-only", action="store_true", help="resume acquisition stages without offline quotes")
    parser.add_argument("--state-only", action="store_true", help="globally batch known candidate state; no discovery, qualification or quotes")
    parser.add_argument("--collection-model-only", action="store_true", help="offline sweep of collected state without promoting quote qualification")
    parser.add_argument("--batch-size", type=int, default=4000, help="maximum subcalls per global state batch")
    parser.add_argument("--workers", type=int, default=1, help="state-only concurrent block collectors (1-8)")
    parser.add_argument("--max-blocks-this-run", type=int, help="bounded resumable prefix without changing run id")
    parser.add_argument("--min-free-gb", type=int, default=2, help="stop cleanly before a block below this free-space threshold")
    parser.add_argument("--compress-reports", action="store_true", help="save gzip JSON reports")
    parser.add_argument("--scenario-config", type=Path, help="JSON with endpoints/pairs/size_ladders or scenarios")
    parser.add_argument("--solvers", default="search", help="comma-separated baseline,search,dual; default search has its baseline incumbent")
    parser.add_argument("--grid-parts", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--beam-width", type=int, default=128)
    parser.add_argument("--max-expansions", type=int, default=5000)
    parser.add_argument("--run-id", default="study-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.run_id) or args.run_id in {".", ".."}:
            raise ValueError("--run-id must be a simple output directory name")
        config = json.loads(args.scenario_config.read_text()) if args.scenario_config else default_config()
        manifest = run_study(select_blocks(args.blocks, args.window, args.start, args.end), config,
                             ROOT / "data/results/study" / args.run_id, acquire=args.acquire or args.acquire_only,
                             acquisition_only=args.acquire_only, max_blocks_this_run=args.max_blocks_this_run,
                             state_only=args.state_only, batch_size=args.batch_size,
                             collection_model_only=args.collection_model_only,
                             workers=args.workers,
                             min_free_gb=args.min_free_gb,
                             compress_reports=args.compress_reports,
                             solvers=tuple(item for item in args.solvers.split(",") if item),
                             grid_parts=args.grid_parts, max_steps=args.max_steps,
                             beam_width=args.beam_width, max_expansions=args.max_expansions)
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(f"historical-study: {_safe_error(error)}", file=sys.stderr)
        return 1
    print(json.dumps({"run": str(ROOT / "data/results/study" / args.run_id), **manifest["summary"],
                      "acquisition_network_requests": manifest["acquisition_network_requests"]}, indent=2))
    return int(bool(manifest["summary"]["failed"] or
                    ((args.acquire or args.acquire_only or args.state_only)
                     and not manifest["summary"]["all_stage_coverage_complete"])))


if __name__ == "__main__":
    raise SystemExit(main())
