from dataclasses import replace

import pytest
from eth_abi import encode as abi_encode

from swaparch.adapters.fluid_dex import (
    EARLIEST_RESOLVER,
    EARLIEST_RESOLVER_CREATED_BLOCK,
    NEW_RESOLVER,
    NEW_RESOLVER_CREATED_BLOCK,
    OLD_RESOLVER,
    OLD_RESOLVER_CREATED_BLOCK,
    POOL_RESERVES_TYPES,
    FluidDexAdapter,
    FluidDexState,
    protocol_token,
    resolver_for_block,
)
from swaparch.core.protocols import Unsupported
from swaparch.core.types import NATIVE_ETH, BlockRef, CallResult, PoolRecord, SupportStatus, Token


def _pack_price(value: int) -> int:
    exponent = max(value.bit_length() - 32, 0)
    return ((value >> exponent) << 8) | exponent


def _state(*, fee: int = 0, variables2: int = 1, center: int = 10**27,
           timestamp: int = 1, last_timestamp: int = 0) -> FluidDexState:
    token0 = Token(1, "0x0000000000000000000000000000000000000001", "T0", 12)
    token1 = Token(1, "0x0000000000000000000000000000000000000002", "T1", 12)
    record = PoolRecord("fluid_dex", 1, "fluid:test", "0x0000000000000000000000000000000000000003",
                        "0x0000000000000000000000000000000000000004", (token0, token1),
                        {"liquidity": "0x0000000000000000000000000000000000000005"}, None, {},
                        SupportStatus.DISCOVERED_UNSUPPORTED)
    variables = (_pack_price(10**27) << 1) | (_pack_price(10**27) << 41) | (last_timestamp << 121)
    return FluidDexState(
        record, token0, token1, 1, 1, 1, 1, fee, center, variables, variables2 | (fee << 2), timestamp,
        (10**18, 10**18, 10**18, 10**18),
        (0, 0, 10**18, 10**18, 10**18, 10**18),
        (10**18, 10**18), (10**18, 10**18), True, bool(variables2 & 2), False,
    )


def test_two_curve_route_preserves_per_leg_flooring_and_is_single_use():
    state = _state(variables2=3)
    # Equal curves split 2e6 adjusted input into two 1e6 legs. Each output is
    # floored independently by the T1 source before the two raw outputs sum.
    out, after = state.swap(state.token0.address, state.token1.address, 2_000_000)
    assert out == 1_999_998
    with pytest.raises(Unsupported, match="post-trade"):
        after.quote_exact_in(after.token0.address, after.token1.address, 2_000_000)


def test_same_block_packed_center_guard_and_native_identity():
    state = _state(timestamp=0, last_timestamp=0, center=10**27 + 2 * 10**19)
    with pytest.raises(Unsupported, match="center price"):
        state.quote_exact_in(state.token0.address, state.token1.address, 1_000_000)
    assert protocol_token("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee") == NATIVE_ETH
    assert protocol_token(state.token0.address) == state.token0.address


def test_resolver_history_is_bounded_and_preserves_later_eras():
    with pytest.raises(Unsupported, match="unproven"):
        resolver_for_block(EARLIEST_RESOLVER_CREATED_BLOCK - 1)
    assert resolver_for_block(EARLIEST_RESOLVER_CREATED_BLOCK) == EARLIEST_RESOLVER
    assert resolver_for_block(OLD_RESOLVER_CREATED_BLOCK - 1) == EARLIEST_RESOLVER
    assert resolver_for_block(OLD_RESOLVER_CREATED_BLOCK) == OLD_RESOLVER
    assert resolver_for_block(NEW_RESOLVER_CREATED_BLOCK - 1) == OLD_RESOLVER
    assert resolver_for_block(NEW_RESOLVER_CREATED_BLOCK) == NEW_RESOLVER


def test_adapter_decodes_resolver_normalized_state():
    original = _state()
    original = replace(
        original,
        record=replace(original.record, config={**original.record.config,
                                                "resolver": "0x0000000000000000000000000000000000000006"}),
    )
    adapter = FluidDexAdapter()
    block = BlockRef(1, EARLIEST_RESOLVER_CREATED_BLOCK, "0x" + "01" * 32, 1)
    specs = adapter.read_requests(original.record, block)
    reserves = (
        original.record.pool, original.token0.address, original.token1.address, 0, 10**27,
        original.collateral, original.debt,
        ((10**18, 0, 0), (10**18, 0, 0), (10**18, 0, 0), (10**18, 0, 0)),
    )
    raw = {
        specs[0]: "0x" + abi_encode(POOL_RESERVES_TYPES, [reserves]).hex(),
        specs[1]: "0x" + abi_encode(["uint256"] * 4, [1, 1, 1, 1]).hex(),
        specs[2]: "0x" + abi_encode(["uint256"], [original.dex_variables]).hex(),
        specs[3]: "0x" + abi_encode(["uint256"], [1]).hex(),
    }

    class Snapshot:
        def has(self, spec):
            return spec in raw

        def get(self, spec):
            return CallResult(spec, True, raw[spec], "fixture")

    snapshot = Snapshot()
    snapshot.block = block
    decoded = adapter.load_state(original.record, snapshot)
    assert decoded.collateral == original.collateral
    assert decoded.withdrawable == (10**18, 10**18)
