"""Reproducible offline native/memo/solver experiment; never starts sweep workers.

Build with scripts/build_perf_native.py first. Every timed quote uses the same
search budget and starts with empty quote caches. Full reports must match.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import inspect
import json
import platform
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from statistics import median
from types import MappingProxyType
from typing import Any

import eval_quote_performance as PERF

from swaparch.adapters.uniswap_v3 import math as v3
from swaparch.adapters.uniswap_v4 import math as v4

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = PROJECT / ".cache/perf-native"


def load_build(build: Path) -> dict[str, Any]:
    """Load only an explicitly selected local build, rejecting stale sources."""
    build = build.resolve()
    manifest_bytes = (build / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for name, expected in manifest["source_sha256"].items():
        actual = hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"stale native build: {name}; rebuild before benchmarking")
    sys.path.insert(0, str(build))
    names = ["perf_math_v3", "perf_math_v4", "perf_scope_solver", "perf_scope_state_memo"]
    index = json.loads((build / "method_index.json").read_text())
    for name in [*names, *index]:
        spec = importlib.util.find_spec(name)
        if (spec is None or spec.origin is None
                or not any(spec.origin.endswith(suffix) for suffix in EXTENSION_SUFFIXES)):
            raise ValueError(f"native extension missing: {name}; rebuild and use a fresh process")
        if Path(spec.origin).resolve().parent != build:
            raise ValueError("a different native build is already loaded; use a fresh process")
    modules = {name: importlib.import_module(name) for name in [*names, *index]}
    for module in modules.values():
        if not any(module.__file__.endswith(suffix) for suffix in EXTENSION_SUFFIXES):
            raise ValueError(f"native extension missing: {module.__name__}; rebuild")
        if Path(module.__file__).resolve().parent != build:
            raise ValueError("a different native build is already loaded; use a fresh process")
    try:
        unchanged = (build / "manifest.json").read_bytes() == manifest_bytes
    except OSError:
        unchanged = False
    if not unchanged:
        raise ValueError("native build changed during loading; use a fresh process after rebuilding")
    return {"manifest": manifest, "modules": modules, "index": index}


@contextlib.contextmanager
def native(loaded: dict[str, Any]):
    saved = []
    try:
        for original, name in ((v3, "perf_math_v3"), (v4, "perf_math_v4")):
            for attr, function in list(vars(original).items()):
                if inspect.isfunction(function) and function.__module__ == original.__name__:
                    saved.append((original, attr, function))
                    setattr(original, attr, getattr(loaded["modules"][name], attr))
        for name, entry in loaded["index"].items():
            compiled = loaded["modules"][name]
            module = importlib.import_module(entry["module"])
            cls = getattr(module, entry["class"])
            for attr, new, static in entry["methods"]:
                target = module if static == "module" else cls
                saved.append((target, attr, inspect.getattr_static(target, attr)))
                function = getattr(compiled, new)
                setattr(target, attr, staticmethod(function) if static is True else function)
        yield
    finally:
        for target, attr, original in reversed(saved):
            setattr(target, attr, original)


@contextlib.contextmanager
def successor_constructors(states: tuple[Any, ...], loaded: dict[str, Any]):
    """Reuse validated immutable bitmap coverage in internal CL successors only.

    Adapter constructors remain unchanged. Unknown maps/ranges still go through
    the original constructor; cached entries retain both immutable maps.
    """
    saved = []

    def factory(cls):
        fields = tuple(cls.__dataclass_fields__)
        known = {}

        def remember(state):
            if (len(known) >= 16_384 or type(state.tick_bitmap) is not MappingProxyType
                    or type(state.tick_liquidity_net) is not MappingProxyType):
                return
            known[id(state.tick_bitmap), id(state.tick_liquidity_net), state.word_lo, state.word_hi] = (
                state.tick_bitmap, state.tick_liquidity_net
            )

        for state in states:
            if type(state) is cls:
                remember(state)

        def create(*args, **kwargs):
            values = dict(zip(fields, args))
            values.update(kwargs)
            if (len(args) > len(fields) or values.keys() != cls.__dataclass_fields__.keys()
                    or set(fields[:len(args)]) & kwargs.keys()):
                return cls(*args, **kwargs)
            key = (id(values["tick_bitmap"]), id(values["tick_liquidity_net"]),
                   values["word_lo"], values["word_hi"])
            if key not in known:
                state = cls(*args, **kwargs)
                remember(state)
                return state
            state = object.__new__(cls)
            state.__dict__.update(values)
            return state
        return create

    try:
        for version in ("v3", "v4"):
            module = loaded["modules"][f"perf_methods_{version}"]
            name = f"Uni{version.upper()}State"
            cls = getattr(module, name)
            saved.append((module, name, cls))
            setattr(module, name, factory(cls))
        yield
    finally:
        for module, name, cls in reversed(saved):
            setattr(module, name, cls)


@contextlib.contextmanager
def optimized(context: Any, loaded: dict[str, Any]):
    solver = loaded["modules"]["perf_scope_solver"]
    states = loaded["modules"]["perf_scope_state_memo"]
    with contextlib.ExitStack() as stack:
        stack.enter_context(native(loaded))
        stack.enter_context(successor_constructors(context.states, loaded))
        ticks = stack.enter_context(PERF.tick_lru_cache(True))
        swaps = stack.enter_context(states.state_swap_memo(context.states))
        stack.enter_context(solver.baseline_indexed_split_quotes())
        stack.enter_context(solver.baseline_ordered_prefix_memo(context.states))
        stack.enter_context(solver.search_nonterminal_residual_skip())
        yield {"ticks": ticks, "swaps": swaps}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--blocks", type=PERF.parse_blocks, default=PERF.DEFAULT_BLOCKS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--study-scenarios", action="store_true",
                        help="use every pair/amount in historical_study.default_config")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    scenario_config = None
    cases_to_run = PERF.CASES
    if args.study_scenarios:
        from historical_study import default_config, scenarios
        scenario_config = default_config()
        cases_to_run = tuple((row["token_in"], row["token_out"], row["human"])
                             for row in scenarios(scenario_config))
    loaded = load_build(args.build_dir)
    PERF.cli._inventory_inputs()
    contexts = {block: PERF.prepared_collection_context(block) for block in args.blocks}
    rows, references = [], {}
    for repeat in range(args.repeats):
        for block in args.blocks:
            context, annotation, client = contexts[block]
            assert client.network_requests == 0
            for case in cases_to_run:
                key = (block, case)
                order = ("reference", "optimized") if repeat % 2 == 0 else ("optimized", "reference")
                reports, exit_codes = {}, {}
                for variant in order:
                    if variant == "reference":
                        result = PERF.execute_variant("reference", context, annotation, case)
                        report = result.pop("report")
                    else:
                        with optimized(context, loaded) as caches:
                            code, encoded, wall, cpu = PERF.run_quote(context, annotation, case)
                        report = json.loads(encoded)
                        PERF.check_report(report)
                        result = {"exit_code": code, "wall_seconds": wall, "cpu_seconds": cpu,
                                  "cache": {"ticks": caches["ticks"].cache_info()._asdict(),
                                            "swaps": caches["swaps"].stats()}}
                    assert result["exit_code"] in (0, 1) and report["network_requests"] == 0
                    references.setdefault(key, report)
                    assert report == references[key], (block, case, variant, "full report mismatch")
                    reports[variant] = report
                    exit_codes[variant] = result["exit_code"]
                    rows.append({**result, "variant": variant, "repeat": repeat, "block": block, "case": case})
                assert reports["reference"] == reports["optimized"]
                assert exit_codes["reference"] == exit_codes["optimized"]
                print(f"repeat={repeat + 1} block={block} case={'/'.join(case)} parity=ok", file=sys.stderr, flush=True)
    cases = []
    for block in args.blocks:
        for case in cases_to_run:
            timings = {variant: median(row["wall_seconds"] for row in rows
                       if row["block"] == block and tuple(row["case"]) == case and row["variant"] == variant)
                       for variant in ("reference", "optimized")}
            cases.append({"block": block, "case": case, **timings,
                          "speedup": timings["reference"] / timings["optimized"]})
    reference_sum = sum(row["reference"] for row in cases)
    optimized_sum = sum(row["optimized"] for row in cases)
    result = {"python": platform.python_version(), "build": loaded["manifest"], "offline": True,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "network_requests": 0, "repeats": args.repeats, "cases": cases, "runs": rows,
              "scenario_config": scenario_config,
              "inputs": {str(block): {"block_hash": ctx.block.hash, **annotation}
                         for block, (ctx, annotation, _) in contexts.items()},
              "settings": {"grid_parts": PERF.GRID_PARTS, "max_steps": PERF.MAX_STEPS,
                           "beam_width": PERF.BEAM_WIDTH, "max_expansions": PERF.MAX_EXPANSIONS},
              "full_report_parity": True, "sum_median_reference_seconds": reference_sum,
              "sum_median_optimized_seconds": optimized_sum, "aggregate_speedup": reference_sum / optimized_sum}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("full_report_parity", "aggregate_speedup")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
