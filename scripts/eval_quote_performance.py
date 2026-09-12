"""Offline, reproducible quote-performance comparison; the historical sweep stays stopped."""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, redirect_stdout
from functools import lru_cache
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from typing import Any

from swaparch import cli
from swaparch.adapters.uniswap_v3 import math as v3_math
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.adapters.uniswap_v4.state import UniV4State
from swaparch.collection_quotes import prepared_collection_context

DEFAULT_BLOCKS = (21895170, 25760917)
CASES = (
    ("WETH", "USDC", "100"),
    ("USDC", "USDT", "1000"),
    ("sUSDe", "USDC", "1000"),
    ("USDC", "WETH", "100000"),
)
VARIANTS = ("reference", "tick_lru", "initial_swap_memo", "combined")
GRID_PARTS = 10
MAX_STEPS = 8
BEAM_WIDTH = 128
MAX_EXPANSIONS = 5000
CACHE_SIZE = 8192


class SwapMemo:
    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.values: dict[tuple[int, str, str, int], tuple[int, Any]] = {}
        self.order: list[tuple[int, str, str, int]] = []
        self.hits = self.misses = 0

    def clear(self) -> None:
        self.values.clear()
        self.order.clear()
        self.hits = self.misses = 0

    def get(self, key: tuple[int, str, str, int]) -> tuple[int, Any] | None:
        value = self.values.get(key)
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: tuple[int, str, str, int], value: tuple[int, Any]) -> None:
        if key in self.values:
            return
        if len(self.order) == self.maxsize:
            del self.values[self.order.pop(0)]
        self.order.append(key)
        self.values[key] = value

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self.values), "maxsize": self.maxsize}


@contextmanager
def initial_swap_memo(initial_states: tuple[Any, ...], maxsize: int = CACHE_SIZE,
                       classes: tuple[type, ...] = (UniV3State, UniV4State)) -> Iterator[SwapMemo]:
    """Memoize successful swaps only for the original prepared V3/V4 states."""
    allowed = {id(state) for state in initial_states}
    memo = SwapMemo(maxsize)
    originals = {cls: cls.swap for cls in classes}

    def wrapped(original):
        def swap(self, token_in, token_out, amount_in):
            if id(self) not in allowed:
                return original(self, token_in, token_out, amount_in)
            key = (id(self), token_in, token_out, amount_in)
            cached = memo.get(key)
            if cached is not None:
                return cached
            result = original(self, token_in, token_out, amount_in)
            memo.put(key, result)
            return result
        return swap

    try:
        for cls, original in originals.items():
            cls.swap = wrapped(original)
        yield memo
    finally:
        for cls, original in originals.items():
            cls.swap = original


@contextmanager
def tick_lru_cache(enabled: bool, maxsize: int = CACHE_SIZE) -> Iterator[Any | None]:
    if not enabled:
        yield None
        return
    original = v3_math.get_sqrt_ratio_at_tick
    cached = lru_cache(maxsize=maxsize)(original)
    try:
        v3_math.get_sqrt_ratio_at_tick = cached
        yield cached
    finally:
        v3_math.get_sqrt_ratio_at_tick = original


def replay_balances(result: dict[str, Any] | None, request: dict[str, Any]) -> None:
    """Independently check the selected result's ordered token balances."""
    if result is None:
        return
    balances: dict[str, int] = defaultdict(int)
    balances[request["token_in"]] = request["amount_in"]
    for step in result["steps"]:
        assert balances[step["token_in"]] >= step["amount_in"]
        balances[step["token_in"]] -= step["amount_in"]
        balances[step["token_out"]] += step["amount_out"]
    assert balances[request["token_out"]] == result["amount_out"]
    assert request["amount_in"] - balances[request["token_in"]] == result["amount_in_spent"]
    assert balances[request["token_in"]] == result["residual_in"]
    refunds = result.get("terminal_refund", {})
    assert isinstance(refunds, dict)
    for token, amount in refunds.items():
        assert type(amount) is int and amount > 0
        assert balances[token] == amount


def check_report(report: dict[str, Any]) -> None:
    for name in ("single_pool_baseline", "best_single_path", "saved_reference_pool", "best_split"):
        replay_balances(report[name], report["request"])


def run_quote(context: Any, annotation: dict[str, Any], case: tuple[str, str, str]) -> tuple[int, str, float, float]:
    output = io.StringIO()
    wall_start, cpu_start = perf_counter(), process_time()
    with redirect_stdout(output):
        code = cli.quote_command(
            context.block.number, *case, GRID_PARTS, output=None, solver_name="search",
            max_steps=MAX_STEPS, beam_width=BEAM_WIDTH, max_expansions=MAX_EXPANSIONS,
            offline=True, prepared_context=context, report_annotations=annotation,
        )
    return code, output.getvalue(), perf_counter() - wall_start, process_time() - cpu_start


def cache_stats(ticks: Any | None, swaps: SwapMemo | None) -> dict[str, Any]:
    return {
        "tick_lru": None if ticks is None else ticks.cache_info()._asdict(),
        "initial_swap_memo": None if swaps is None else swaps.stats(),
    }


def execute_variant(name: str, context: Any, annotation: dict[str, Any], case: tuple[str, str, str]) -> dict[str, Any]:
    use_ticks, use_swaps = name in ("tick_lru", "combined"), name in ("initial_swap_memo", "combined")
    with tick_lru_cache(use_ticks) as ticks, (initial_swap_memo(context.states) if use_swaps else nullcontext()) as swaps:
        code, encoded, wall, cpu = run_quote(context, annotation, case)
        stats = cache_stats(ticks, swaps)
    report = json.loads(encoded)
    assert report["network_requests"] == 0
    check_report(report)
    return {"variant": name, "exit_code": code, "wall_seconds": wall, "cpu_seconds": cpu,
            "report": report, "cache": stats}


def parse_blocks(value: str) -> tuple[int, ...]:
    try:
        blocks = tuple(int(item) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--blocks must be comma-separated integers") from exc
    if not blocks or any(block <= 0 for block in blocks):
        raise argparse.ArgumentTypeError("--blocks must contain positive block numbers")
    return blocks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded offline quote cache comparison")
    parser.add_argument("--blocks", type=parse_blocks, default=DEFAULT_BLOCKS)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.repeats <= 0:
        parser.error("--repeats must be positive")

    # Context and normal CLI inventory parsing are deliberately outside timings.
    cli._inventory_inputs()
    prepared = {block: prepared_collection_context(block) for block in args.blocks}
    runs: list[dict[str, Any]] = []
    references: dict[tuple[int, tuple[str, str, str]], dict[str, Any]] = {}
    for repeat in range(args.repeats):
        for block in args.blocks:
            context, annotations, client = prepared[block]
            assert client.network_requests == 0
            for case in CASES:
                order = VARIANTS[repeat % len(VARIANTS):] + VARIANTS[:repeat % len(VARIANTS)]
                for variant in order:
                    result = execute_variant(variant, context, annotations, case)
                    key = (block, case)
                    if variant == "reference":
                        if key in references:
                            assert result["report"] == references[key], f"reference report changed at {key}"
                        else:
                            references[key] = result["report"]
                    else:
                        assert result["report"] == references[key], f"report parity failed for {variant} at {key}"
                    result.pop("report")
                    result.update({"repeat": repeat, "block": block, "case": dict(zip(("token_in", "token_out", "amount"), case))})
                    runs.append(result)
                print(f"completed repeat={repeat + 1}/{args.repeats} block={block} case={case[0]}->{case[1]} {case[2]}",
                      file=sys.stderr, flush=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        grouped[row["variant"]].append(row)
    case_medians = {
        name: {
            f"{block}:{case[0]}->{case[1]}:{case[2]}": {
                "wall_seconds": median(row["wall_seconds"] for row in rows),
                "cpu_seconds": median(row["cpu_seconds"] for row in rows),
            }
            for (block, case), rows in {
                (block, case): [row for row in grouped[name] if row["block"] == block and tuple(row["case"].values()) == case]
                for block in args.blocks for case in CASES
            }.items()
        }
        for name in VARIANTS
    }
    totals = {name: {"wall_seconds": sum(row["wall_seconds"] for row in rows.values()),
                     "cpu_seconds": sum(row["cpu_seconds"] for row in rows.values())}
              for name, rows in case_medians.items()}
    reference_total = totals["reference"]["wall_seconds"]
    result = {
        "python": sys.version,
        "offline": True,
        "network_requests": 0,
        "settings": {"grid_parts": GRID_PARTS, "max_steps": MAX_STEPS, "beam_width": BEAM_WIDTH,
                     "max_expansions": MAX_EXPANSIONS, "cache_size": CACHE_SIZE, "repeats": args.repeats,
                     "blocks": list(args.blocks), "cases": [dict(zip(("token_in", "token_out", "amount"), c)) for c in CASES]},
        "parity": "all full CLI JSON reports, including diagnostics and unsupported lists, matched reference",
        "runs": runs,
        "case_medians": case_medians,
        "total_median_work": {name: {**rows, "speedup_vs_reference": reference_total / rows["wall_seconds"]}
                              for name, rows in totals.items()},
    }
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
