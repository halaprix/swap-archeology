from __future__ import annotations

import dataclasses

import pytest
from eth_abi import encode

from swaparch.adapters.curve_legacy_3pool import (
    DAI,
    POOL,
    REGISTRY,
    TAGS,
    USDC,
    USDT,
    CurveAdapter,
    CurveLegacy3PoolAdapter,
    CurveLegacy3PoolState,
)
from swaparch.core.protocols import PoolState, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, PoolRecord, SupportStatus, Token

HASH = "0xabc"


class Snapshot:
    block = BlockRef(1, 23550060, HASH, 0)

    def __init__(self, calls): self.calls = {(call.spec.to, call.spec.data): call for call in calls}
    def has(self, spec): return (spec.to, spec.data) in self.calls
    def get(self, spec): return self.calls[(spec.to, spec.data)]


def record():
    tokens = (Token(1, DAI, "DAI", 18), Token(1, USDC, "USDC", 6), Token(1, USDT, "USDT", 6))
    return PoolRecord("curve", 1, f"curve:{REGISTRY}:{POOL}", REGISTRY, POOL, tokens,
                      {"registry_observations": {HASH: {"coins": [DAI, USDC, USDT], "base_pool": "0x0000000000000000000000000000000000000000", "base_registries": [REGISTRY]}},
                       "transfer_semantics": {DAI: "standard", USDC: "standard", USDT: "standard"}},
                      None, {"method": "fixture"}, SupportStatus.DISCOVERED_UNSUPPORTED)


def snapshot(rec):
    adapter = CurveLegacy3PoolAdapter()
    values = {TAGS[0]: 200, TAGS[1]: 4_000_000, TAGS[2]: 5_000_000_000,
              TAGS[3]: 1_000_000 * 10**18, TAGS[4]: 1_100_000 * 10**6, TAGS[5]: 900_000 * 10**6}
    return Snapshot([CallResult(spec, True, "0x" + encode(["uint256"], [values[spec.tag]]).hex(), "fixture")
                     for spec in adapter.read_requests(rec, Snapshot.block)])


def reference(xp, amp, i, j, dx, fee):
    d = sum(xp)
    for _ in range(255):
        dp = d
        for value in xp: dp = dp * d // (value * 3)
        prior = d
        d = (amp * 3 * sum(xp) + dp * 3) * d // ((amp * 3 - 1) * d + 4 * dp)
        if abs(d - prior) <= 1: break
    x, c, total = xp[i] + dx, d, 0
    for index in range(3):
        if index == j: continue
        value = x if index == i else xp[index]
        total += value
        c = c * d // (value * 3)
    c = c * d // (amp * 3 * 3)
    b, y = total + d // (amp * 3), d
    for _ in range(255):
        prior, y = y, (y * y + c) // (2 * y + b - d)
        if abs(y - prior) <= 1: break
    raw = xp[j] - y - 1
    return (raw - raw * fee // 10**10) // 10**12


def test_read_plan_quote_and_shared_state_match_separate_source_order_translation():
    rec, adapter = record(), CurveLegacy3PoolAdapter()
    state = adapter.load_state(rec, snapshot(rec))
    assert isinstance(adapter, SourceAdapter) and isinstance(state, PoolState)
    assert [spec.tag for spec in adapter.read_requests(rec, Snapshot.block)] == list(TAGS)
    dx = 123_456 * 10**6
    expected = reference((10**24, 1_100_000 * 10**18, 900_000 * 10**18), 200, 2, 1, dx * 10**12, state.fee)
    out, after = state.swap(USDT, USDC, dx)
    assert out == expected
    assert state.quote_exact_in(USDT, USDC, dx) >= out
    assert after.balances[2] == state.balances[2] + dx
    assert after.swap(DAI, USDT, 10**18)[0] != state.swap(DAI, USDT, 10**18)[0]
    assert isinstance(CurveAdapter()._adapter(rec), CurveLegacy3PoolAdapter)


def test_rejects_noncanonical_identity_empty_state_and_overflow():
    rec = record()
    with pytest.raises(Unsupported, match="canonical 3pool"):
        CurveLegacy3PoolAdapter().load_state(dataclasses.replace(rec, pool=DAI), snapshot(rec))
    state = CurveLegacy3PoolAdapter().load_state(rec, snapshot(rec))
    with pytest.raises(Unsupported, match="outside uint256"):
        CurveLegacy3PoolState(rec, state.balances, state.amp, state.fee, state.admin_fee).swap(USDT, USDC, 1 << 256)
