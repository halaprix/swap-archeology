"""Offline checks for ``UniV3State`` and ``UniswapV3Adapter``.

Everything runs from recorded raw ``eth_call`` responses under
``data/adapters-evidence/uniswap_v3/`` (block 25896003 and 23549991, pool
0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640) fed through a minimal fake
``Snapshot`` defined here.  No RPC.

The expected quote values are *independent* of this code:

* ``quoter_out`` comes from ``QuoterV2.quoteExactInputSingle`` at the same block;
* the funded-run amounts come from ``evidence/.../results-funded.json``, a forge
  fork test written by another session.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter, UniV3State
from swaparch.adapters.uniswap_v3.state import GAS_ESTIMATE
from swaparch.core.protocols import PoolState, Snapshot, SourceAdapter, Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "adapters-evidence" / "uniswap_v3"
FUNDED = ROOT / "evidence" / "crash-rescue-simulation" / "results-funded.json"

POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"


class FakeSnapshot:
    """Minimal ``core.protocols.Snapshot`` over recorded raw responses."""

    def __init__(self, fixture: dict) -> None:
        self.block = BlockRef(
            chain=fixture["chain"],
            number=fixture["block"],
            hash=fixture["block_hash"],
            timestamp=0,
        )
        self._by_call: dict[tuple[str, str], CallResult] = {}
        for call in fixture["calls"]:
            spec = CallSpec(to=call["to"], data=call["data"], tag=call["tag"])
            self._by_call[(spec.to, spec.data)] = CallResult(
                spec=spec, success=call["success"], raw=call["result"], via="fixture"
            )

    def get(self, spec: CallSpec) -> CallResult:
        return self._by_call[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._by_call


def make_record(config_extra: dict | None = None) -> PoolRecord:
    return PoolRecord(
        family="uniswap_v3",
        chain=1,
        pool_id=f"uniswap_v3:{FACTORY}:{POOL}",
        deployment=FACTORY,
        pool=POOL,
        tokens=(
            Token(chain=1, address=USDC, symbol="USDC", decimals=6),
            Token(chain=1, address=WETH, symbol="WETH", decimals=18),
        ),
        config={"fee": 500, "tick_spacing": 10, **(config_extra or {})},
        created_block=12376729,
        discovered_by={"method": "logs:PoolCreated"},
        status=SupportStatus.SUPPORTED,
    )


def load(block: int, radius: int = 8) -> tuple[dict, FakeSnapshot]:
    fixture = json.loads((EVID / f"snapshot-{block}-r{radius}.json").read_text())
    return fixture, FakeSnapshot(fixture)


@pytest.fixture(scope="module")
def calm():
    fixture, snap = load(25896003)
    state = UniswapV3Adapter(word_radius=8).load_state(make_record(), snap)
    return fixture, snap, state


@pytest.fixture(scope="module")
def crash():
    fixture, snap = load(23549991)
    state = UniswapV3Adapter(word_radius=8).load_state(make_record(), snap)
    return fixture, snap, state


# ------------------------------------------------------------------ protocols


def test_types_satisfy_the_shared_protocols(calm):
    _, _, state = calm
    assert isinstance(UniswapV3Adapter(), SourceAdapter)
    assert isinstance(state, PoolState)
    assert isinstance(FakeSnapshot({"chain": 1, "block": 1, "block_hash": "0x", "calls": []}),
                      Snapshot)


# --------------------------------------------------------------- state loading


def test_loaded_state_matches_the_recorded_slot0(calm):
    _, snap, state = calm
    assert snap.block.number == 25896003
    assert snap.block.hash == (
        "0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5"
    )
    # sqrtPriceX96 independently recorded in evidence/.../standing-prices.json
    assert state.sqrt_price_x96 == 1618868919676713855630301716459399
    assert state.tick == 198508
    assert state.fee == 500
    assert state.tick_spacing == 10
    assert state.token0.address == USDC
    assert state.token1.address == WETH
    assert (state.word_lo, state.word_hi) == (69, 85)
    assert len(state.tick_liquidity_net) == 1461
    assert state.capacity_ids() == (f"uniswap_v3:{FACTORY}:{POOL}",)
    assert state.gas_estimate(WETH, USDC) == GAS_ESTIMATE == 120_000


def test_crash_block_state(crash):
    _, snap, state = crash
    assert snap.block.number == 23549991
    # matches evidence/.../standing-prices.json and results-funded.json
    assert state.sqrt_price_x96 == 1367563279517029399426676730054431
    assert (state.word_lo, state.word_hi) == (68, 84)


def test_state_is_immutable(calm):
    _, _, state = calm
    before = (state.sqrt_price_x96, state.tick, state.liquidity)
    out, new = state.swap(WETH, USDC, 1000 * 10**18)
    assert out > 0
    assert (state.sqrt_price_x96, state.tick, state.liquidity) == before
    assert new is not state
    assert new.sqrt_price_x96 > state.sqrt_price_x96  # token1 in raises the price
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.sqrt_price_x96 = 1  # frozen dataclass
    with pytest.raises(TypeError):
        state.tick_bitmap[999] = 1  # mapping proxy


def test_state_rejects_holes_in_its_advertised_bitmap_window(calm):
    _, _, state = calm
    with pytest.raises(Unsupported, match=r"missing words \[76\]"):
        dataclasses.replace(
            state,
            tick_bitmap={word: value for word, value in state.tick_bitmap.items() if word != 76},
        )


def test_consecutive_swaps_consume_shared_depth(calm):
    """Two 500 WETH sales through the same state must beat one 1000 WETH sale."""
    _, _, state = calm
    once, _ = state.swap(WETH, USDC, 1000 * 10**18)
    first, after = state.swap(WETH, USDC, 500 * 10**18)
    second, _ = after.swap(WETH, USDC, 500 * 10**18)
    assert second < first  # the second half gets a worse price
    assert first + second == once or abs(first + second - once) <= 2


# ----------------------------------------------------------- QuoterV2 agreement


def test_quotes_match_quoter_v2_wei_exact(calm, crash):
    for fixture, _snap, state in (calm, crash):
        assert fixture["expected_quotes"], "fixture carries no recorded QuoterV2 answers"
        for q in fixture["expected_quotes"]:
            t_in, t_out = (WETH, USDC) if q["direction"] == "WETH->USDC" else (USDC, WETH)
            out, new = state.swap(t_in, t_out, q["amount_in"])
            assert out == q["quoter_out"], (
                f"block {fixture['block']} {q['direction']} {q['amount_in']}"
            )
            assert new.sqrt_price_x96 == q["quoter_sqrt_after"]


def test_quote_exact_in_matches_swap(calm):
    _, _, state = calm
    for amount in (10**17, 10**18, 100 * 10**18):
        assert state.quote_exact_in(WETH, USDC, amount) == state.swap(WETH, USDC, amount)[0]


# --------------------------------------------------- independent forge evidence


def test_reproduces_settled_forge_fork_swaps(calm, crash):
    """Nine settled WETH->USDC swaps from another session's funded fork run."""
    funded = json.loads(FUNDED.read_text())
    states = {25896003: calm[2], 23549991: crash[2]}
    checked = 0
    for row in funded:
        if row["status"] != "settled" or row["block"] not in states:
            continue
        state = states[row["block"]]
        # the fork's pre-swap price is the end-of-block state we loaded
        assert state.sqrt_price_x96 == row["sqrt_price_before"]
        assert state.quote_exact_in(WETH, USDC, row["weth_in"]) == row["usdc_out"]
        checked += 1
    assert checked == 9


# --------------------------------------------------------------- failure modes


def test_unsupported_outside_the_loaded_tick_window(calm):
    _, _, state = calm
    with pytest.raises(Unsupported) as exc:
        state.swap(WETH, USDC, 200_000 * 10**18)
    assert str(exc.value) == "insufficient tick coverage: needed word 86, loaded [69,85]"


def test_widening_the_window_prices_a_trade_the_default_cannot(calm):
    """40 000 WETH needs +/-16 words; the answer then matches QuoterV2 exactly."""
    _, _, narrow = calm
    with pytest.raises(Unsupported):
        narrow.swap(WETH, USDC, 40_000 * 10**18)

    _, wide_snap = load(25896003, radius=16)
    wide = UniswapV3Adapter(word_radius=16).load_state(make_record(), wide_snap)
    assert (wide.word_lo, wide.word_hi) == (61, 93)
    recorded = json.loads((EVID / "window-widening-40000weth-25896003.json").read_text())
    out, new = wide.swap(WETH, USDC, 40_000 * 10**18)
    assert out == recorded["quoter"]["amount_out"] == 67632886601193
    assert new.sqrt_price_x96 == recorded["quoter"]["sqrt_price_after"]


def test_unsupported_for_a_pair_this_pool_does_not_hold(calm):
    _, _, state = calm
    with pytest.raises(Unsupported) as exc:
        state.quote_exact_in(WETH, DAI, 10**18)
    assert "is not this pool's pair" in str(exc.value)


def test_zero_and_negative_input(calm):
    _, _, state = calm
    assert state.swap(WETH, USDC, 0) == (0, state)
    with pytest.raises(Unsupported):
        state.swap(WETH, USDC, -1)


# ------------------------------------------------------------- adapter reads


def test_read_plan_selectors_and_tags():
    adapter = UniswapV3Adapter()
    record = make_record()
    block = BlockRef(chain=1, number=25896003, hash="0x00", timestamp=0)
    specs = adapter.read_requests(record, block)
    assert [s.tag for s in specs] == [
        "univ3:slot0", "univ3:liquidity", "univ3:fee",
        "univ3:tickSpacing", "univ3:token0", "univ3:token1",
    ]
    assert [s.data for s in specs] == [
        "0x3850c7bd", "0x1a686502", "0xddca3f43", "0xd0c93a7c", "0x0dfe1681", "0xd21220a7",
    ]
    assert all(s.to == POOL for s in specs)
    # tickBitmap(int16) / ticks(int24) encode their argument ABI-style
    assert adapter.bitmap_spec(record, 77).data == "0x5339c296" + "00" * 31 + "4d"
    assert adapter.tick_spec(record, 198510).data == "0xf30dba93" + "00" * 29 + "03076e"
    assert adapter.bitmap_spec(record, -1).data == "0x5339c296" + "ff" * 32


def test_tick_hint_moves_the_bitmap_window_into_phase_one():
    block = BlockRef(chain=1, number=25896003, hash="0x00", timestamp=0)
    hinted = UniswapV3Adapter(word_radius=2).read_requests(
        make_record({"tick_hint": 198508}), block
    )
    assert [s.tag for s in hinted[6:]] == [f"univ3:tickBitmap:{w}" for w in range(75, 80)]


def test_dependent_requests_converge_to_empty(calm):
    _, snap, _ = calm
    adapter = UniswapV3Adapter(word_radius=8)
    record = make_record()
    assert adapter.dependent_requests(record, snap.block, snap) == []


def test_dependent_requests_rejects_failed_bitmap_read():
    fixture, _ = load(23549991)
    failed = {**fixture, "calls": [dict(call) for call in fixture["calls"]]}
    for call in failed["calls"]:
        if call["tag"] == "univ3:tickBitmap:76":
            call.update(success=False, result="0x")
            break
    else:  # pragma: no cover - fixture corruption guard
        raise AssertionError("fixture has no tickBitmap word 76")

    snap = FakeSnapshot(failed)
    with pytest.raises(Unsupported, match=r"failed tickBitmap word 76"):
        UniswapV3Adapter(word_radius=8).dependent_requests(make_record(), snap.block, snap)


def test_dependent_requests_asks_for_bitmap_then_ticks():
    """Replay the acquisition loop against a snapshot that only has phase 1."""
    fixture, _ = load(25896003)
    adapter = UniswapV3Adapter(word_radius=8)
    record = make_record()
    by_call = {(c["to"], c["data"]): c for c in fixture["calls"]}

    partial = {"chain": 1, "block": fixture["block"], "block_hash": fixture["block_hash"],
               "calls": [c for c in fixture["calls"] if not c["tag"].startswith(
                   ("univ3:tickBitmap", "univ3:ticks"))]}
    snap = FakeSnapshot(partial)
    rounds = []
    while True:
        wanted = adapter.dependent_requests(record, snap.block, snap)
        if not wanted:
            break
        rounds.append(len(wanted))
        for spec in wanted:
            partial["calls"].append(by_call[(spec.to, spec.data)])
        snap = FakeSnapshot(partial)
    assert rounds == [17, 1461]  # 17 bitmap words, then one read per initialized tick
    state = adapter.load_state(record, snap)
    assert state.sqrt_price_x96 == 1618868919676713855630301716459399


def test_provenance_records_the_window(calm):
    _, snap, _ = calm
    prov = UniswapV3Adapter(word_radius=8).provenance(make_record(), snap)
    assert prov["words_loaded"] == [69, 85]
    assert prov["word_radius_requested"] == 8
    assert prov["initialized_ticks_loaded"] == 1461
    assert prov["block_hash"] == snap.block.hash


def test_load_state_rejects_an_unrelated_token_map():
    _, snap = load(25896003)
    bad = PoolRecord(
        family="uniswap_v3", chain=1, pool_id="x", deployment=FACTORY, pool=POOL,
        tokens=(Token(1, DAI, "DAI", 18), Token(1, WETH, "WETH", 18)),
        config={}, created_block=None, discovered_by={},
    )
    with pytest.raises(Unsupported):
        UniswapV3Adapter().load_state(bad, snap)


def test_state_type_is_the_documented_one(calm):
    _, _, state = calm
    assert isinstance(state, UniV3State)
