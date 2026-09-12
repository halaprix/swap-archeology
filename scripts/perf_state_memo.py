"""Scoped immutable-state swap memo experiment for offline quote benchmarks.

The production state classes remain immutable.  This module only patches them
inside ``state_swap_memo`` and restores every patched function on exit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from statistics import median
from typing import Any

from swaparch.adapters.curve_ng import CurveNGState
from swaparch.adapters.fluid_dex import FluidDexState
from swaparch.adapters.lido import LidoWstEthState
from swaparch.adapters.litepsm import LitePsmState
from swaparch.adapters.origin_arm import OriginLidoArmState
from swaparch.adapters.uniswap_v2 import UniV2State
from swaparch.adapters.uniswap_v3 import state as v3_state
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.adapters.uniswap_v4 import state as v4_state
from swaparch.adapters.uniswap_v4.state import UniV4State
from swaparch.core.protocols import Unsupported
from swaparch.core.types import Token

_PERF_SPEC = importlib.util.spec_from_file_location(
    "eval_quote_performance", Path(__file__).with_name("eval_quote_performance.py")
)
assert _PERF_SPEC and _PERF_SPEC.loader
_PERF = importlib.util.module_from_spec(_PERF_SPEC)
_PERF_SPEC.loader.exec_module(_PERF)
CACHE_SIZE = _PERF.CACHE_SIZE
SwapMemo = _PERF.SwapMemo
VARIANTS = ("reference", "tick_lru_state_memo")
STATE_CACHE_SIZE = 16_384
IMMUTABLE_STATES = (
    UniV2State, UniV3State, UniV4State, CurveNGState, FluidDexState, LitePsmState,
    LidoWstEthState, OriginLidoArmState,
)


class _Identities:
    """Assign scoped ids while retaining objects, so ``id`` cannot be reused."""

    def __init__(self, state_key_limit: int) -> None:
        self._objects: list[object] = []
        self._by_object_id: dict[int, int] = {}
        self._map_signatures: dict[tuple[tuple[int, int], ...], int] = {}
        self._state_keys: dict[int, tuple[object, _StateKey]] = {}
        self._state_key_limit = state_key_limit

    def object(self, value: object) -> int:
        value_id = id(value)
        known = self._by_object_id.get(value_id)
        if known is not None:
            assert self._objects[known] is value
            return known
        known = len(self._objects)
        self._objects.append(value)
        self._by_object_id[value_id] = known
        return known

    def tick_map(self, value: Mapping[int, int]) -> int:
        # Map ordering is not semantics.  The signature is computed only for
        # previously unseen static maps; internal successor states reuse the
        # canonical proxy below and take the O(1) object path.
        known = self._by_object_id.get(id(value))
        if known is not None and self._objects[known] is value:
            return known
        signature = tuple(sorted(value.items()))
        known = self._map_signatures.get(signature)
        if known is not None:
            return known
        known = self.object(value)
        self._map_signatures[signature] = known
        return known

    def canonical_map(self, value: Mapping[int, int]) -> Mapping[int, int]:
        key = self.tick_map(value)
        canonical = self._objects[key]
        assert isinstance(canonical, Mapping)
        return canonical

    def state_key(self, state: object) -> _StateKey:
        known = self._state_keys.get(id(state))
        if known is not None and known[0] is state:
            return known[1]
        if len(self._state_keys) == self._state_key_limit:
            self._state_keys.clear()
        key = _StateKey(_state_key(state, self))
        self._state_keys[id(state)] = (state, key)
        return key


class _StateKey:
    """Tuple equality with a precomputed hash for repeated memo lookups."""

    __slots__ = ("_hash", "values")

    def __init__(self, values: tuple[Any, ...]) -> None:
        self.values = values
        self._hash = hash(values)

    def __hash__(self) -> int:
        return self._hash

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _StateKey) and self.values == other.values


def _state_key(state: Any, identities: _Identities) -> tuple[Any, ...]:
    """Key every field that can affect a cached immutable state transition."""
    if isinstance(state, (UniV3State, UniV4State)):
        common = (
            type(state), identities.object(state.record), state.sqrt_price_x96, state.tick,
            state.liquidity, state.tick_spacing, identities.object(state.token0), identities.object(state.token1),
            identities.tick_map(state.tick_bitmap), identities.tick_map(state.tick_liquidity_net),
            state.word_lo, state.word_hi, state.gas,
        )
        if isinstance(state, UniV3State):
            return common + (state.fee,)
        return common + (state.protocol_fee, state.lp_fee)
    if not hasattr(state, "__dataclass_fields__"):
        raise TypeError(f"state memo requires an immutable dataclass, got {type(state)!r}")
    return (type(state), identities.object(state.record), *(
        identities.object(value) if isinstance(value, Token) else value
        for name in state.__dataclass_fields__ if name != "record"
        for value in (getattr(state, name),)
    ))


@contextmanager
def state_swap_memo(
    initial_states: tuple[Any, ...], maxsize: int = STATE_CACHE_SIZE,
    classes: tuple[type, ...] = IMMUTABLE_STATES,
) -> Iterator[SwapMemo]:
    """Memoize immutable swaps across equal successor states within one run.

    Successful results and deterministic ``Unsupported`` failures are cached.
    The state constructors still perform their normal coverage validation.  A
    raw mapping is copied normally; only a mapping proxy made inside this scope
    is reused by a successor state.
    """
    identities = _Identities(maxsize)
    for state in initial_states:
        if isinstance(state, (UniV3State, UniV4State)):
            identities.object(state.record)
            identities.tick_map(state.tick_bitmap)
            identities.tick_map(state.tick_liquidity_net)
        elif isinstance(state, IMMUTABLE_STATES):
            identities.object(state.record)

    memo = SwapMemo(maxsize)
    original_swaps = {cls: cls.swap for cls in classes}
    original_freezes = {v3_state: v3_state._freeze, v4_state: v4_state._freeze}

    def freeze(original):
        def wrapped(values: Mapping[int, int]) -> Mapping[int, int]:
            # Do not trust externally supplied MappingProxyType objects: first
            # construction still copies them through the original freezer.
            known = identities._by_object_id.get(id(values))
            if known is not None and identities._objects[known] is values:
                return identities.canonical_map(values)
            return identities.canonical_map(original(values))
        return wrapped

    def wrapped_swap(original):
        def swap(self, token_in, token_out, amount_in):
            key = (identities.state_key(self), token_in, token_out, amount_in)
            cached = memo.get(key)
            if cached is not None:
                kind, value = cached
                if kind == "error":
                    raise Unsupported(*value)
                return value
            try:
                result = original(self, token_in, token_out, amount_in)
            except Unsupported as exc:
                memo.put(key, ("error", exc.args))
                raise
            memo.put(key, ("result", result))
            return result
        return swap

    try:
        for module, original in original_freezes.items():
            module._freeze = freeze(original)
        for cls, original in original_swaps.items():
            cls.swap = wrapped_swap(original)
        yield memo
    finally:
        for cls, original in original_swaps.items():
            cls.swap = original
        for module, original in original_freezes.items():
            module._freeze = original


def execute_variant(name: str, context: Any, annotation: dict[str, Any],
                    case: tuple[str, str, str]) -> dict[str, Any]:
    if name == "reference":
        return _PERF.execute_variant(name, context, annotation, case)
    if name != "tick_lru_state_memo":
        raise ValueError(f"unknown variant {name}")
    with _PERF.tick_lru_cache(True) as ticks, state_swap_memo(context.states) as swaps:
        code, encoded, wall, cpu = _PERF.run_quote(context, annotation, case)
        stats = {"tick_lru": ticks.cache_info()._asdict(), "state_swap_memo": swaps.stats()}
    report = json.loads(encoded)
    assert report["network_requests"] == 0
    _PERF.check_report(report)
    return {"variant": name, "exit_code": code, "wall_seconds": wall, "cpu_seconds": cpu,
            "report": report, "cache": stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline V3/V4 successor-state memo comparison")
    parser.add_argument("--blocks", type=_PERF.parse_blocks, default=_PERF.DEFAULT_BLOCKS)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.repeats <= 0:
        parser.error("--repeats must be positive")

    _PERF.cli._inventory_inputs()
    prepared = {block: _PERF.prepared_collection_context(block) for block in args.blocks}
    rows: list[dict[str, Any]] = []
    references: dict[tuple[int, tuple[str, str, str]], dict[str, Any]] = {}
    for repeat in range(args.repeats):
        for block in args.blocks:
            context, annotation, client = prepared[block]
            assert client.network_requests == 0
            for case in _PERF.CASES:
                for name in VARIANTS:
                    row = execute_variant(name, context, annotation, case)
                    key = block, case
                    if name == "reference":
                        references.setdefault(key, row["report"])
                        assert row["report"] == references[key], f"reference report changed at {key}"
                    else:
                        assert row["report"] == references[key], f"full report parity failed at {key}"
                    row.pop("report")
                    row.update({"repeat": repeat, "block": block,
                                "case": dict(zip(("token_in", "token_out", "amount"), case))})
                    rows.append(row)
                print(f"completed repeat={repeat + 1}/{args.repeats} block={block} {case}",
                      file=sys.stderr, flush=True)
    totals = {
        name: sum(median(row["wall_seconds"] for row in rows if row["variant"] == name
                         and row["block"] == block and tuple(row["case"].values()) == case)
                  for block in args.blocks for case in _PERF.CASES)
        for name in VARIANTS
    }
    result = {
        "offline": True, "network_requests": 0,
        "settings": {"blocks": list(args.blocks), "cases": [dict(zip(("token_in", "token_out", "amount"), c)) for c in _PERF.CASES], "repeats": args.repeats},
        "parity": "all full CLI JSON reports, including diagnostics and unsupported lists, matched reference",
        "runs": rows,
        "total_median_work": {name: {"wall_seconds": value,
                                       "speedup_vs_reference": totals["reference"] / value}
                              for name, value in totals.items()},
    }
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
