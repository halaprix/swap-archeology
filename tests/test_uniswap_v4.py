"""Offline V4 identity, fee, and bounded-state checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from eth_abi import encode

from swaparch.adapters.uniswap_v3 import math as v3
from swaparch.adapters.uniswap_v4 import UniswapV4Adapter, UniV4State
from swaparch.adapters.uniswap_v4 import math as v4
from swaparch.adapters.uniswap_v4.adapter import POOL_MANAGER, STATE_VIEW, ZERO_HOOK, pool_key_id
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallResult, PoolRecord, SupportStatus, Token

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SPEC = importlib.util.spec_from_file_location("uniswap_v4_run", ROOT / "scripts/uniswap_v4_run.py")
assert SCRIPT_SPEC and SCRIPT_SPEC.loader
runner = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(runner)

ETH = "0x0000000000000000000000000000000000000000"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def record(*, hooks: str = ZERO_HOOK, fee: int = 3000) -> PoolRecord:
    pool = pool_key_id(ETH, USDC, fee, 60, hooks)
    return PoolRecord(
        family="uniswap_v4", chain=1, pool_id=f"uniswap_v4:{POOL_MANAGER}:{pool}",
        deployment=POOL_MANAGER, pool=pool,
        tokens=(Token(1, ETH, "ETH", 18), Token(1, USDC, "USDC", 6)),
        config={"currency0": ETH, "currency1": USDC, "fee": fee, "tick_spacing": 60, "hooks": hooks},
        created_block=21_688_545, discovered_by={"method": "logs:Initialize"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def state(protocol_fee: int = 0) -> UniV4State:
    rec = record()
    return UniV4State(
        rec, v3.get_sqrt_ratio_at_tick(0), 0, 10**18, protocol_fee, 3000, 60,
        rec.tokens[0], rec.tokens[1], {-1: 1 << 255, 0: 1, 1: 1},
        {-60: 10**17, 0: 0, 60: -10**17}, -1, 1,
    )


def topic(value: str) -> str:
    return "0x" + value[2:].rjust(64, "0")


def test_initialize_decodes_final_three_indexed_parameter_layout_and_pool_id():
    rec = record()
    log = {
        "topics": [runner.TOPIC_INITIALIZE, rec.pool, topic(ETH), topic(USDC)],
        "data": "0x" + encode(["uint24", "int24", "address", "uint160", "int24"], [3000, 60, ZERO_HOOK, v3.get_sqrt_ratio_at_tick(0), 0]).hex(),
        "blockNumber": hex(21_688_545), "blockHash": "0xabc", "transactionHash": "0xdef", "logIndex": "0x7",
    }
    decoded = runner.decode_initialize(log)
    assert decoded["pool"] == rec.pool
    assert (decoded["currency0"], decoded["currency1"], decoded["hooks"]) == (ETH, USDC, ZERO_HOOK)
    assert decoded["created_block"] == 21_688_545


def test_endpoint_filters_target_currency_topics_not_pool_id_topic():
    specs = runner.filters([ETH], 21_688_329, 21_688_545)
    assert [topics for _, topics in specs] == [
        [runner.TOPIC_INITIALIZE, None, topic(ETH), None],
        [runner.TOPIC_INITIALIZE, None, None, topic(ETH)],
    ]


def test_quoter_uses_the_canonical_nested_pool_key_selector():
    assert runner.SEL_QUOTE_EXACT_INPUT_SINGLE == "0xaa9d21cb"
    assert runner._quoter_spec(record(), True, 1).data.startswith("0xaa9d21cb")


def test_pool_key_id_and_static_hook_gate():
    adapter = UniswapV4Adapter()
    block = BlockRef(1, 25_896_003, "0x00", 0)
    reads = adapter.read_requests(record(), block)
    assert [call.to for call in reads] == [STATE_VIEW, STATE_VIEW]
    assert reads[0].tag == "univ4:slot0"
    with pytest.raises(Unsupported, match="nonzero hook"):
        adapter.read_requests(record(hooks="0x0000000000000000000000000000000000000001"), block)
    with pytest.raises(Unsupported, match="dynamic LP fee"):
        adapter.read_requests(record(fee=v4.DYNAMIC_FEE_FLAG), block)
    too_wide = record()
    object.__setattr__(too_wide, "config", {**too_wide.config, "tick_spacing": 32_768})
    with pytest.raises(Unsupported, match="tick spacing"):
        adapter.read_requests(too_wide, block)


def test_directional_protocol_fee_and_static_lp_fee_compose_as_v4():
    packed = 100 | (200 << 12)
    assert v4.directional_protocol_fee(packed, True) == 100
    assert v4.directional_protocol_fee(packed, False) == 200
    assert v4.swap_fee(packed, 3000, True) == 3100
    assert v4.swap_fee(packed, 3000, False) == 3200


def test_native_eth_remains_a_distinct_token_and_consumes_shared_state():
    before = state(100 | (200 << 12))
    first, after = before.swap(ETH, USDC, 10**14)
    second, _ = after.swap(ETH, USDC, 10**14)
    once, _ = before.swap(ETH, USDC, 2 * 10**14)
    assert first > second > 0
    assert abs(first + second - once) <= 2
    assert before.tokens()[0].symbol == "ETH"
    with pytest.raises(Unsupported):
        before.quote_exact_in("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", USDC, 1)
    with pytest.raises(Unsupported, match="uint128"):
        before.swap(ETH, USDC, v3.UINT128_MAX + 1)


def test_malformed_bitmap_bytes_are_unsupported_not_an_abi_exception():
    adapter, rec = UniswapV4Adapter(word_radius=0), record()
    block = BlockRef(1, 25_896_003, "0x00", 0)
    slot = adapter.slot0_spec(rec)
    bitmap = adapter.bitmap_spec(rec, 0)

    class Snapshot:
        def __init__(self):
            self.block = block
            self.calls = {
                (slot.to, slot.data): CallResult(slot, True, "0x" + encode(
                    ["uint160", "int24", "uint24", "uint24"], [v3.get_sqrt_ratio_at_tick(0), 0, 0, 3000]
                ).hex(), "fixture"),
                (bitmap.to, bitmap.data): CallResult(bitmap, True, "0x00", "fixture"),
            }

        def get(self, spec):
            return self.calls[(spec.to, spec.data)]

        def has(self, spec):
            return (spec.to, spec.data) in self.calls

    with pytest.raises(Unsupported, match="malformed V4 tick bitmap"):
        adapter.dependent_requests(rec, block, Snapshot())
