"""Offline checks for the scoped immutable successor-state memo."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from swaparch.adapters.curve_ng import PRECISION, CurveNGState
from swaparch.adapters.uniswap_v3 import math
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.adapters.uniswap_v4.state import UniV4State
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, Token

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("perf_state_memo", ROOT / "scripts/perf_state_memo.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
state_swap_memo = MODULE.state_swap_memo

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def state(*, fee: int = 500, liquidity: int = 10**18, tick_one: int = 0,
          sqrt_price: int | None = None) -> UniV3State:
    tokens = Token(1, USDC, "USDC", 6), Token(1, WETH, "WETH", 18)
    record = PoolRecord("uniswap_v3", 1, f"test:{fee}:{liquidity}:{tick_one}", WETH, WETH,
                        tokens, {}, 1, {}, SupportStatus.SUPPORTED)
    return UniV3State(record, sqrt_price or math.get_sqrt_ratio_at_tick(0), 0, liquidity, fee,
                      60, *tokens, {-1: 0, 0: 0, 1: tick_one}, {}, -1, 1)


def test_equal_successor_states_share_result_and_reuse_static_maps():
    initial = state()
    original = UniV3State.swap
    _, left = original(initial, USDC, WETH, 10**8)
    _, right = original(initial, USDC, WETH, 10**8)
    with state_swap_memo((initial,), maxsize=8) as memo:
        first = left.swap(USDC, WETH, 10**8)
        second = right.swap(USDC, WETH, 10**8)
        assert second is first
        assert second[1].tick_bitmap is initial.tick_bitmap
        assert memo.stats() == {"hits": 1, "misses": 1, "size": 1, "maxsize": 8}


def test_state_fields_that_change_behavior_do_not_collide():
    initial = state()
    states = (initial, replace(initial, fee=3_000), replace(initial, liquidity=2 * 10**18),
              replace(initial, tick_bitmap={-1: 0, 0: 0, 1: 1}))
    with state_swap_memo(states, maxsize=8) as memo:
        for item in states:
            item.swap(USDC, WETH, 10**8)
        assert memo.stats()["hits"] == 0
        assert memo.stats()["misses"] == len(states)


def test_v4_protocol_and_lp_fees_do_not_collide():
    tokens = Token(1, USDC, "USDC", 6), Token(1, WETH, "WETH", 18)

    def v4(protocol_fee: int, lp_fee: int) -> UniV4State:
        record = PoolRecord("uniswap_v4", 1, f"v4:{protocol_fee}:{lp_fee}", WETH, WETH,
                            tokens, {}, 1, {}, SupportStatus.SUPPORTED)
        return UniV4State(record, math.get_sqrt_ratio_at_tick(0), 0, 10**18, protocol_fee,
                          lp_fee, 60, *tokens, {-1: 0, 0: 0, 1: 0}, {}, -1, 1)

    initial = v4(0, 500)
    states = initial, replace(initial, protocol_fee=100), replace(initial, lp_fee=3_000)
    with state_swap_memo(states, maxsize=8) as memo:
        for item in states:
            item.swap(USDC, WETH, 10**8)
        assert memo.stats()["hits"] == 0
        assert memo.stats()["misses"] == len(states)


def test_equal_curve_successors_share_result_but_fee_and_balance_do_not_collide():
    tokens = Token(1, USDC, "USDC", 6), Token(1, WETH, "WETH", 18)
    record = PoolRecord("curve", 1, "curve:test", WETH, WETH, tokens, {}, 1, {}, SupportStatus.SUPPORTED)

    def curve(*, fee: int = 4_000_000, balance: int = 10**24) -> CurveNGState:
        return CurveNGState(record, (balance, balance), (PRECISION, PRECISION), 100_000, fee,
                            45_000_000_000, 5_000_000_000)

    original = CurveNGState.swap
    initial = curve()
    _, left = original(initial, USDC, WETH, 10**18)
    _, right = original(initial, USDC, WETH, 10**18)
    with state_swap_memo((initial, curve(fee=3_000_000), curve(balance=2 * 10**24)), maxsize=8) as memo:
        first = left.swap(USDC, WETH, 10**18)
        assert right.swap(USDC, WETH, 10**18) is first
        for item in (curve(fee=3_000_000), curve(balance=2 * 10**24)):
            item.swap(USDC, WETH, 10**18)
        assert memo.stats()["hits"] == 1


def test_unsupported_failures_are_cached_and_all_patches_restore():
    initial = state(sqrt_price=math.MIN_SQRT_RATIO + 1)
    original_swap = UniV3State.swap
    from swaparch.adapters.uniswap_v3 import state as state_module
    original_freeze = state_module._freeze
    with pytest.raises(RuntimeError), state_swap_memo((initial,), maxsize=8) as memo:
        for _ in range(2):
            with pytest.raises(Unsupported, match="minimum sqrt"):
                initial.swap(USDC, WETH, 1)
        assert memo.stats()["hits"] == 1
        assert memo.stats()["size"] == 1
        raise RuntimeError("exercise finally")
    assert UniV3State.swap is original_swap
    assert state_module._freeze is original_freeze
