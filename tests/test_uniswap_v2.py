"""Offline Uniswap V2 state and discovery checks; no RPC."""

from __future__ import annotations

import dataclasses

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.uniswap_v2 import (
    FACTORY,
    FEE_DENOMINATOR,
    FEE_NUMERATOR,
    SEL_BALANCE_OF,
    SEL_GET_RESERVES,
    SEL_TOKEN0,
    SEL_TOKEN1,
    STETH,
    UINT112_MAX,
    UniswapV2Adapter,
    UniV2State,
    get_amount_out,
)
from swaparch.core.protocols import PoolState, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token
from swaparch.discovery.uniswap_v2 import (
    FACTORY_CREATION_BLOCK,
    TOPIC_PAIR_CREATED,
    build_pair_created_filters,
    decode_pair_created,
    pool_record_from_log,
)

PAIR = "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class FakeSnapshot:
    def __init__(self, calls: list[CallResult]) -> None:
        self.block = BlockRef(1, 25896003, "0xabc", 0)
        self.calls = {(call.spec.to, call.spec.data): call for call in calls}

    def get(self, spec: CallSpec) -> CallResult:
        return self.calls[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self.calls


def token(address: str, symbol: str, decimals: int) -> Token:
    return Token(1, address, symbol, decimals)


def record(
    *,
    token0: Token | None = None,
    token1: Token | None = None,
    semantics: bool = True,
) -> PoolRecord:
    token0 = token0 or token(USDC, "USDC", 6)
    token1 = token1 or token(WETH, "WETH", 18)
    return PoolRecord(
        family="uniswap_v2",
        chain=1,
        pool_id=f"uniswap_v2:{FACTORY}:{PAIR}",
        deployment=FACTORY,
        pool=PAIR,
        tokens=(token0, token1),
        config={
            "transfer_semantics": (
                {token0.address: "standard", token1.address: "standard"} if semantics else {}
            )
        },
        created_block=10042267,
        discovered_by={"method": "logs:PairCreated"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def snapshot(rec: PoolRecord, reserve0: int = 50_000_000_000, reserve1: int = 20_000 * 10**18,
             balance0: int | None = None, balance1: int | None = None, success: bool = True) -> FakeSnapshot:
    adapter = UniswapV2Adapter()
    calls: list[CallResult] = []
    for spec in adapter.read_requests(rec, BlockRef(1, 25896003, "0xabc", 0)):
        if spec.data == SEL_GET_RESERVES:
            raw = "0x" + abi_encode(["uint112", "uint112", "uint32"], [reserve0, reserve1, 0]).hex()
        elif spec.data == SEL_TOKEN0:
            raw = "0x" + abi_encode(["address"], [rec.tokens[0].address]).hex()
        elif spec.data == SEL_TOKEN1:
            raw = "0x" + abi_encode(["address"], [rec.tokens[1].address]).hex()
        elif spec.data.startswith(SEL_BALANCE_OF):
            value = balance0 if spec.tag == "univ2:balance0" else balance1
            value = reserve0 if value is None and spec.tag == "univ2:balance0" else value
            value = reserve1 if value is None else value
            raw = "0x" + abi_encode(["uint256"], [value]).hex()
        else:  # pragma: no cover - fixture follows adapter selectors
            raise AssertionError(spec)
        calls.append(CallResult(spec, success, raw if success else "0x", "fixture"))
    return FakeSnapshot(calls)


def test_canonical_integer_quote_matches_independent_rational_expression():
    amount, reserve_in, reserve_out = 997_123, 17_000_003, 42_000_007
    expected = (amount * 997 * reserve_out) // (reserve_in * 1000 + amount * 997)
    assert get_amount_out(amount, reserve_in, reserve_out) == expected == 2_320_396
    assert (FEE_NUMERATOR, FEE_DENOMINATOR) == (997, 1000)


def test_load_quote_and_immutable_shared_state_transition():
    rec = record()
    state = UniswapV2Adapter().load_state(rec, snapshot(rec))
    assert isinstance(UniswapV2Adapter(), SourceAdapter)
    assert isinstance(state, PoolState)
    assert state.capacity_ids() == (rec.pool_id,)
    amount = 1_000 * 10**6
    once, after_once = state.swap(USDC, WETH, amount * 2)
    first, after_first = state.swap(USDC, WETH, amount)
    second, _ = after_first.swap(USDC, WETH, amount)
    assert after_once is not state
    assert after_once.reserve0 == state.reserve0 + amount * 2
    assert after_once.reserve1 == state.reserve1 - once
    # V2 stores the full input (including the fee) after each call, so two
    # separately settled swaps are slightly worse than one combined call.
    assert second < first
    assert first + second < once
    assert state.quote_exact_in(USDC, WETH, amount) == first
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.reserve0 = 1


def test_read_plan_includes_stored_reserves_identity_and_actual_pair_balances():
    rec = record()
    specs = UniswapV2Adapter().read_requests(rec, BlockRef(1, 1, "0x0", 0))
    assert [spec.tag for spec in specs] == [
        "univ2:getReserves", "univ2:token0", "univ2:token1", "univ2:balance0", "univ2:balance1"
    ]
    assert [spec.data[:10] for spec in specs] == [
        SEL_GET_RESERVES, SEL_TOKEN0, SEL_TOKEN1, SEL_BALANCE_OF, SEL_BALANCE_OF
    ]
    assert specs[3].to == USDC and specs[4].to == WETH


def test_reserve_balance_mismatch_is_not_priced_as_a_normal_v2_pool():
    rec = record()
    with pytest.raises(Unsupported, match="reserve/balance mismatch"):
        UniswapV2Adapter().load_state(rec, snapshot(rec, balance0=50_000_000_001))


def test_empty_malformed_failed_and_unqualified_reads_are_explicitly_unsupported():
    rec = record()
    adapter = UniswapV2Adapter()
    with pytest.raises(Unsupported, match="empty reserves"):
        adapter.load_state(rec, snapshot(rec, reserve0=0))
    with pytest.raises(Unsupported, match="failed getReserves"):
        adapter.load_state(rec, snapshot(rec, success=False))
    calls = list(snapshot(rec).calls.values())
    bad_reserves = calls[0]
    calls[0] = CallResult(bad_reserves.spec, True, "0x12", "fixture")
    with pytest.raises(Unsupported, match="malformed getReserves"):
        adapter.load_state(rec, FakeSnapshot(calls))
    with pytest.raises(Unsupported, match="transfer semantics are unqualified"):
        adapter.load_state(record(semantics=False), snapshot(record(semantics=False)))


def test_raw_steth_and_post_swap_uint112_overflow_are_rejected():
    steth_record = record(token0=token(STETH, "stETH", 18), token1=token(WETH, "WETH", 18))
    with pytest.raises(Unsupported, match="raw stETH"):
        UniswapV2Adapter().load_state(steth_record, snapshot(steth_record))
    state = UniV2State(
        record(), UINT112_MAX, UINT112_MAX, token(USDC, "USDC", 6), token(WETH, "WETH", 18)
    )
    with pytest.raises(Unsupported, match="post-swap input reserve overflows uint112"):
        state.swap(USDC, WETH, 10**18)


def test_negative_or_dust_input_and_wrong_pair_are_not_silently_quoted():
    state = UniswapV2Adapter().load_state(record(), snapshot(record()))
    assert state.swap(USDC, WETH, 0) == (0, state)
    with pytest.raises(Unsupported, match="positive exact input"):
        state.quote_exact_in(USDC, WETH, -1)
    dust = UniV2State(record(), 10**18, 1, token(USDC, "USDC", 6), token(WETH, "WETH", 18))
    with pytest.raises(Unsupported, match="rounds to zero"):
        dust.swap(USDC, WETH, 1)
    with pytest.raises(Unsupported, match="not this pool"):
        state.quote_exact_in(USDC, STETH, 100)


def test_pair_created_filters_record_coverage_and_decoder_captures_creation_hash():
    filters = build_pair_created_filters({"USDC": USDC, "WETH": WETH}, to_block=25896003)
    assert len(filters) == 2
    assert all(spec.address == FACTORY and spec.topics[0] == TOPIC_PAIR_CREATED for spec in filters)
    assert {spec.meta["canonical"] for spec in filters} == {True, False}
    assert all(spec.from_block == FACTORY_CREATION_BLOCK for spec in filters)
    log = {
        "topics": [
            TOPIC_PAIR_CREATED,
            "0x" + "00" * 12 + USDC[2:],
            "0x" + "00" * 12 + WETH[2:],
        ],
        "data": "0x" + abi_encode(["address", "uint256"], [PAIR, 1]).hex(),
        "blockNumber": "0x991d8b",
        "blockHash": "0xdeadbeef",
        "transactionHash": "0xcafe",
        "logIndex": "0x2",
    }
    decoded = decode_pair_created(log)
    assert decoded == {
        "token0": USDC, "token1": WETH, "pair": PAIR, "pair_index": 1,
        "created_block": 10034571, "block_hash": "0xdeadbeef", "tx_hash": "0xcafe", "log_index": 2,
    }
    tokens = {USDC: token(USDC, "USDC", 6), WETH: token(WETH, "WETH", 18)}
    discovered = pool_record_from_log(log, tokens, filter_meta=filters[0].meta)
    assert discovered.created_block == 10034571
    assert discovered.discovered_by["block_hash"] == "0xdeadbeef"
    assert discovered.status == SupportStatus.DISCOVERED_UNSUPPORTED


def test_bad_pair_created_log_is_rejected():
    with pytest.raises(ValueError, match="not a PairCreated"):
        decode_pair_created({"topics": ["0x00"], "data": "0x"})
