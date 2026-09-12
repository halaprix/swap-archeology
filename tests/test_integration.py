"""Failures must stay visible at acquisition, admission, and evidence boundaries."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_uniswap_v3_state import load, make_record

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import SupportStatus
from swaparch.universe import acquire, load_states

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import uniswap_v3_online_check as online
import uniswap_v3_window_analysis as window


def test_failed_dependency_is_reported_and_excluded():
    _, snapshot = load(25896003)
    record = make_record()

    class FailingAdapter(UniswapV3Adapter):
        def dependent_requests(self, *args):
            raise Unsupported("bitmap subcall failed")

    class ExistingStore:
        def extend(self, *args):
            return snapshot

    adapters = {"uniswap_v3": FailingAdapter()}
    _, report = acquire(adapters, [record], snapshot.block, ExistingStore(), None)
    assert report.unsupported == {record.pool_id: "bitmap subcall failed"}
    states, excluded = load_states(adapters, [record], snapshot, report.unsupported)
    assert not states and excluded == [(record, "bitmap subcall failed")]


def test_discovery_or_other_block_validation_does_not_admit_pool():
    _, snapshot = load(25896003)
    adapters = {"uniswap_v3": UniswapV3Adapter()}
    record = make_record()
    assert not load_states(adapters, [record], snapshot)[0]
    validated = replace(record, config={**record.config,
                                        "validated_block_hashes": [snapshot.block.hash]})
    assert len(load_states(adapters, [validated], snapshot)[0]) == 1
    discovered = replace(validated, status=SupportStatus.DISCOVERED_UNSUPPORTED)
    assert not load_states(adapters, [discovered], snapshot)[0]


def test_online_acquisition_rejects_changed_hash(monkeypatch):
    _, snapshot = load(25896003)
    hashes = iter([snapshot.block.hash, "0x" + "00" * 32])
    monkeypatch.setattr(online.C, "cast_block_hash", lambda _: next(hashes))
    monkeypatch.setattr(online.C, "pinned_multicall",
                        lambda specs, block, chunk: [snapshot.get(spec) for spec in specs])
    with pytest.raises(RuntimeError, match="hash changed during acquisition"):
        online.acquire(snapshot.block.number, 8)


def test_online_check_fails_on_quote_mismatch(monkeypatch):
    evidence = {"state": {"word_lo": 1, "word_hi": 2, "initialized_ticks": 0},
                "comparison": [{"direction": "A->B", "amount_in": 1, "local_out": 1,
                                "quoter_out": 2, "diff": -1, "sqrt_match": True,
                                "error": None}]}
    monkeypatch.setattr(online, "check_block", lambda *_: evidence)
    assert online.main(["check", "25896003"]) == 1


def test_window_analysis_reports_failed_construction():
    fixture, _ = load(23549991)
    for call in fixture["calls"]:
        if call["tag"] == "univ3:tickBitmap:76":
            call["success"] = False
    result = window.minimal_radius(fixture, window.WETH, window.USDC, 10 ** 18, None)
    assert result["radius"] is None and "missing words [76]" in result["error"]


def test_activation_upper_bound_does_not_invent_creation_or_prior_absence():
    from swaparch.universe import activation_reason, pools_live_at

    unknown = replace(make_record(), created_block=None, discovered_by={})
    observed = replace(unknown, discovered_by={"deployed_by_block": 100,
                                               "evidence": ["pinned registry membership"]})
    assert not pools_live_at([unknown, observed], 99)
    assert activation_reason(observed, 99) == "activation not established at requested block"
    assert pools_live_at([unknown, observed], 100) == [observed]
    assert observed.created_block is None
    assert activation_reason(replace(unknown, created_block=100), 99) == "not_deployed_at_block"
    assert not pools_live_at([replace(unknown, discovered_by={"deployed_by_block": True})], 100)


def test_activation_respects_recorded_initialization_after_creation():
    from swaparch.universe import activation_reason, pools_live_at

    for created, initialized in ((23770895, 23770901), (21589896, 21589910)):
        record = replace(
            make_record(),
            created_block=created,
            config={**make_record().config, "initialized_block": initialized},
        )
        assert activation_reason(record, created - 1) == "not_deployed_at_block"
        assert activation_reason(record, created) == "not_initialized_at_block"
        assert pools_live_at([record], initialized - 1) == []
        assert activation_reason(record, initialized) is None
        assert record.created_block == created

    legacy = replace(make_record(), created_block=100)
    assert activation_reason(legacy, 100) is None


def test_acquire_blocks_inactive_records_before_adapter_reads():
    _, snapshot = load(25896003)
    block = snapshot.block
    records = [
        replace(make_record(), pool_id="uncreated", created_block=block.number + 1),
        replace(
            make_record(),
            pool_id="uninitialized",
            created_block=block.number - 1,
            config={**make_record().config, "initialized_block": block.number + 1},
        ),
        replace(make_record(), pool_id="unknown", created_block=None, discovered_by={}),
    ]

    class NoReadAdapter:
        def read_requests(self, *_args):
            raise AssertionError("inactive record reached read_requests")

        def dependent_requests(self, *_args):
            raise AssertionError("inactive record reached dependent_requests")

    class EmptyStore:
        def __init__(self):
            self.spec_lists = []

        def extend(self, _block, specs, _client):
            self.spec_lists.append(list(specs))
            return snapshot

    store = EmptyStore()
    _, report = acquire({"uniswap_v3": NoReadAdapter()}, records, block, store, None)
    assert report.unsupported == {
        "uncreated": "not_deployed_at_block",
        "uninitialized": "not_initialized_at_block",
        "unknown": "activation not established at requested block",
    }
    assert store.spec_lists == [[]]


def test_bad_read_plan_excludes_one_pool_without_aborting_other_acquisition():
    _, snapshot = load(25896003)
    record = make_record()

    class BadReadAdapter(UniswapV3Adapter):
        def read_requests(self, *args):
            raise Unsupported("deployment identity mismatch")

    class ExistingStore:
        def extend(self, *args):
            return snapshot

    adapters = {"uniswap_v3": BadReadAdapter()}
    _, report = acquire(adapters, [record], snapshot.block, ExistingStore(), None)
    assert report.unsupported == {record.pool_id: "deployment identity mismatch"}
    assert not load_states(adapters, [record], snapshot, report.unsupported)[0]

def test_singleton_activation_is_pinned_cached_and_does_not_guess_creation(tmp_path, monkeypatch):
    from swaparch import universe

    monkeypatch.setattr(universe, 'PROJECT_ROOT', tmp_path)
    _, snapshot = load(25896003)
    record = replace(make_record(), created_block=None, discovered_by={})
    calls = []

    class Client:
        def _rpc(self, method, params):
            calls.append((method, params))
            return '0x6000'

    observed = universe.observe_singleton_activation(record, snapshot.block, Client())
    assert observed.created_block is None
    assert observed.discovered_by['deployed_by_block'] == snapshot.block.number
    assert calls[0][1][1]['blockHash'] == snapshot.block.hash
    assert universe.observe_singleton_activation(record, snapshot.block, Client()) == observed
    assert len(calls) == 1
    future = replace(record, created_block=snapshot.block.number + 1)
    assert universe.observe_singleton_activation(future, snapshot.block, Client()) is None
    assert len(calls) == 1
