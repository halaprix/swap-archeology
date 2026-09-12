"""Offline checks for the bounded Curve StableSwap-NG plain-pool adapter."""

from __future__ import annotations

import dataclasses

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.curve_ng import (
    A_PRECISION,
    FACTORY,
    FEE_DENOMINATOR,
    PRECISION,
    TAGS,
    UINT256_MAX,
    CurveNGAdapter,
    CurveNGState,
    get_D,
)
from swaparch.core.protocols import PoolState, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

POOL = "0x02950460e2b9529d0e00284a5fa2d7bdf3fa4d72"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
HASH = "0xabc"


class FakeSnapshot:
    def __init__(self, calls: list[CallResult]) -> None:
        self.block = BlockRef(1, 25896003, HASH, 0)
        self.calls = {(call.spec.to, call.spec.data): call for call in calls}

    def get(self, spec: CallSpec) -> CallResult:
        return self.calls[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self.calls


def token(address: str, symbol: str, decimals: int = 18) -> Token:
    return Token(1, address, symbol, decimals)


def record(*, semantics: bool = True, steth: bool = False) -> PoolRecord:
    tokens = (token(USDC, "USDC"), token(USDT, "USDT"), token(DAI, "DAI"))
    if steth:
        tokens = (token("0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "stETH"), *tokens[1:])
    addresses = [value.address for value in tokens]
    return PoolRecord(
        family="curve", chain=1, pool_id=f"curve:{FACTORY}:{POOL}", deployment=FACTORY, pool=POOL,
        tokens=tokens,
        config={
            "registry_observations": {HASH: {
                "coins": addresses, "decimals": [value.decimals for value in tokens],
                "base_pool": "0x0000000000000000000000000000000000000000",
                "base_registries": [FACTORY],
            }},
            "transfer_semantics": {address: "standard" for address in addresses} if semantics else {},
        },
        created_block=None, discovered_by={"method": "fixture"}, status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def snapshot(rec: PoolRecord, *, amp: int = 100_000, a: int = 1_000,
             balances: tuple[int, ...] = (1_000_000 * 10**18, 1_100_000 * 10**18, 900_000 * 10**18),
             asset_types: tuple[int, ...] = (0, 0, 0), success: bool = True) -> FakeSnapshot:
    adapter = CurveNGAdapter()
    raw_by_tag = {
        TAGS[0]: ("uint256", len(rec.tokens)), TAGS[1]: ("uint256", a), TAGS[2]: ("uint256", amp),
        TAGS[3]: ("uint256[]", balances), TAGS[4]: ("uint256[]", (PRECISION,) * len(rec.tokens)),
        TAGS[5]: ("uint256", 4_000_000), TAGS[6]: ("uint256", 45_000_000_000),
        TAGS[7]: ("uint256", 5_000_000_000), TAGS[8]: ("uint8[]", asset_types),
        TAGS[9]: ("address", "0xdcc91f930b42619377c200ba05b7513f2958b202"),
    }
    calls = []
    for spec in adapter.read_requests(rec, BlockRef(1, 25896003, HASH, 0)):
        abi, value = raw_by_tag[spec.tag]
        raw = "0x" + abi_encode([abi], [value]).hex() if success else "0x"
        calls.append(CallResult(spec, success, raw, "fixture"))
    return FakeSnapshot(calls)


def reference_quote(xp: tuple[int, ...], amp: int, i: int, j: int, dx: int,
                    fee: int, multiplier: int) -> int:
    """Separate literal translation of the pinned Vyper view formula."""
    n = len(xp)
    d = sum(xp)
    ann = amp * n
    for _ in range(255):
        d_p = d
        for value in xp:
            d_p = d_p * d // value
        d_p //= n**n
        previous = d
        d = ((ann * sum(xp) // A_PRECISION + d_p * n) * d //
             ((ann - A_PRECISION) * d // A_PRECISION + (n + 1) * d_p))
        if abs(d - previous) <= 1:
            break
    x = xp[i] + dx
    c, total = d, 0
    for index in range(n):
        if index == j:
            continue
        value = x if index == i else xp[index]
        total += value
        c = c * d // (value * n)
    c = c * d * A_PRECISION // (ann * n)
    b, y = total + d * A_PRECISION // ann, d
    for _ in range(255):
        previous = y
        y = (y * y + c) // (2 * y + b - d)
        if abs(y - previous) <= 1:
            break
    raw_out = xp[j] - y - 1
    xpi, xpj = (xp[i] + x) // 2, (xp[j] + y) // 2
    dynamic = fee if multiplier <= FEE_DENOMINATOR else multiplier * fee // (
        (multiplier - FEE_DENOMINATOR) * 4 * xpi * xpj // (xpi + xpj) ** 2 + FEE_DENOMINATOR
    )
    return raw_out - raw_out * dynamic // FEE_DENOMINATOR


def test_load_read_plan_and_plain_ng_identity() -> None:
    rec = record()
    adapter = CurveNGAdapter()
    state = adapter.load_state(rec, snapshot(rec))
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(state, PoolState)
    assert [spec.tag for spec in adapter.read_requests(rec, BlockRef(1, 1, HASH, 0))] == list(TAGS)
    assert state.capacity_ids() == (rec.pool_id,)
    assert state.amp == 100_000


def test_multi_coin_quote_matches_independent_vyper_formula_and_consumes_shared_state() -> None:
    rec = record()
    state = CurveNGAdapter().load_state(rec, snapshot(rec))
    amount = 123_456 * 10**18
    expected = reference_quote(state.balances, state.amp, 0, 2, amount, state.fee, state.offpeg_fee_multiplier)
    out, after = state.swap(USDC, DAI, amount)
    assert out == expected == 123_374_195_900_362_372_007_137
    assert after is not state
    assert after.balances[0] == state.balances[0] + amount
    assert after.balances[2] < state.balances[2] - out  # includes the separately floored admin claim
    next_out, _ = after.swap(USDT, DAI, amount)
    original_out, _ = state.swap(USDT, DAI, amount)
    assert next_out < original_out
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.balances = ()


def test_empty_dust_overflow_and_view_rounding_are_not_silently_priced() -> None:
    rec = record()
    with pytest.raises(Unsupported, match="empty balance"):
        CurveNGAdapter().load_state(rec, snapshot(rec, balances=(1, 0, 1)))
    with pytest.raises(Unsupported, match="rounds to zero"):
        CurveNGAdapter().load_state(rec, snapshot(rec)).swap(USDC, USDT, 1)
    with pytest.raises(Unsupported, match="outside uint256"):
        CurveNGState(rec, (UINT256_MAX, 10**18, 10**18), (PRECISION,) * 3, 100_000, 0, 0, 0).swap(
            USDC, USDT, 1
        )
    with pytest.raises(Unsupported, match=r"A\(\) view loses"):
        CurveNGAdapter().load_state(rec, snapshot(rec, amp=100_001))


def test_unqualified_tokens_rebasing_and_non_plain_assets_are_explicitly_unsupported() -> None:
    with pytest.raises(Unsupported, match="transfer semantics"):
        CurveNGAdapter().load_state(record(semantics=False), snapshot(record(semantics=False)))
    steth = record(steth=True)
    with pytest.raises(Unsupported, match="raw stETH"):
        CurveNGAdapter().load_state(steth, snapshot(steth))
    rec = record()
    with pytest.raises(Unsupported, match="asset_types 0"):
        CurveNGAdapter().load_state(rec, snapshot(rec, asset_types=(0, 1, 0)))


def test_get_d_rejects_zero_and_unchecked_intermediate_domain() -> None:
    with pytest.raises(Unsupported, match="empty balance"):
        get_D((1, 0), 100)
    with pytest.raises(Unsupported, match="outside uint256"):
        get_D((UINT256_MAX, UINT256_MAX), 100)


def test_non_mainnet_record_snapshot_and_token_are_rejected():
    rec = record()
    snap = snapshot(rec)
    adapter = CurveNGAdapter()
    for bad in (dataclasses.replace(rec, chain=2),
                dataclasses.replace(rec, tokens=(dataclasses.replace(rec.tokens[0], chain=2), *rec.tokens[1:]))):
        with pytest.raises(Unsupported, match='Ethereum pool, tokens, and snapshot'):
            adapter.load_state(bad, snap)
    snap.block = dataclasses.replace(snap.block, chain=2)
    with pytest.raises(Unsupported, match='Ethereum pool, tokens, and snapshot'):
        adapter.load_state(rec, snap)
