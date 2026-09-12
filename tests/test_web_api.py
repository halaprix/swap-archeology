import json
import runpy
from pathlib import Path

import pytest

from swaparch.web_api import QuoteBridge, QuoteRequest, RequestError, main


@pytest.mark.parametrize("conflict", ["block_hash", "decimals_in"])
def test_catalog_rejects_conflicting_pinned_identities(tmp_path, conflict):
    export_catalog = runpy.run_path(str(Path(__file__).resolve().parents[1]
                                       / "scripts/export_frontend_data.py"))["export_catalog"]
    first, second = _report(), _report()
    if conflict == "block_hash":
        second["block_hash"] = "0x" + "b" * 64
    else:
        second["request"]["decimals_in"] = 6
    (tmp_path / "first.json").write_text(json.dumps(first))
    (tmp_path / "second.json").write_text(json.dumps(second))
    with pytest.raises(ValueError, match="conflicting saved"):
        export_catalog((tmp_path,))


def _report(amount=900719925474099312345):
    return {
        "chain": 1, "block": 25896003, "block_hash": "0x" + "a" * 64, "timestamp": 1,
        "request": {"token_in": "0xin", "token_out": "0xout", "symbol_in": "IN", "symbol_out": "OUT",
                    "decimals_in": 18, "decimals_out": 18, "amount_in": amount},
        "single_pool_baseline": {"amount_out": amount},
        "best_split": {"amount_out": amount + 1, "steps": [{"pool_id": "pool", "token_in": "0xin",
                       "token_out": "0xout", "amount_in": amount, "amount_out": amount + 1}],
                       "search_info": {"kind": "direct"}, "gas_estimate": None},
        "requested_solver": "baseline", "selected_families": ["uniswap_v3"],
        "sources": [{"family": "uniswap_v3", "selected": True, "usable_pools": 1}],
        "unsupported": [], "limitations": ["offline"],
    }


def test_quote_preserves_large_integers_and_reuses_exact_identity(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(bridge, "_cache_key", lambda request: "identity")
    calls = []

    def fake_quote(*args, **kwargs):
        calls.append((args, kwargs))
        print(json.dumps(_report()))
        return 0

    monkeypatch.setattr("swaparch.web_api.quote_command", fake_quote)
    request = {"block": "25896003", "tokenIn": "IN", "tokenOut": "OUT", "amount": "1", "solver": "baseline"}
    first = bridge.quote(request)
    second = bridge.quote(request)
    assert first["cached"] is False and second["cached"] is True and len(calls) == 1
    assert first["report"]["request"]["amount_in"] == "900719925474099312345"
    assert first["report"]["best_split"]["steps"][0]["amount_out"] == "900719925474099312346"
    assert bridge.reports()["reports"][0]["id"] == "identity"


@pytest.mark.parametrize("payload,code", [
    ({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1", "solver": "dual"}, "invalid_solver"),
    ({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1", "solver": {}}, "invalid_solver"),
    ({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1", "sources": []}, "invalid_sources"),
    ({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1e100000"}, "invalid_request"),
])
def test_quote_rejects_invalid_fields_before_solving(tmp_path, monkeypatch, payload, code):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    with pytest.raises(RequestError) as error:
        bridge.parse_request(payload)
    assert error.value.code == code


def test_quote_surfaces_missing_state_without_a_fake_zero_quote(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(bridge, "_cache_key", lambda request: "missing")

    def unavailable(*args, **kwargs):
        raise ValueError("offline cache miss")

    monkeypatch.setattr("swaparch.web_api.quote_command", unavailable)
    with pytest.raises(RequestError) as error:
        bridge.quote({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1"})
    assert error.value.code == "missing_state" and error.value.status == 409


def test_cache_identity_changes_with_the_exact_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr("swaparch.web_api._inventory_inputs", lambda: ([], "inventory-a", ()))
    monkeypatch.setattr("swaparch.web_api._snapshot_identity", lambda chain, block_hash: block_hash)
    bridge = QuoteBridge(tmp_path)
    first = QuoteRequest(1, "0x" + "a" * 64, "A", "B", "1", "baseline", None)
    second = QuoteRequest(1, "0x" + "b" * 64, "A", "B", "1", "baseline", None)
    assert bridge._cache_key(first) != bridge._cache_key(second)


def test_cache_identity_covers_adapter_and_evaluator_source(monkeypatch, tmp_path):
    monkeypatch.setattr("swaparch.web_api._inventory_inputs", lambda: ([], "inventory-a", ()))
    monkeypatch.setattr("swaparch.web_api._snapshot_identity", lambda chain, block_hash: "snapshot-a")
    seen, identities = [], iter(("source-a", "source-b"))

    def identity(paths):
        seen.extend(paths)
        return next(identities)

    monkeypatch.setattr("swaparch.web_api._identity", identity)
    bridge = QuoteBridge(tmp_path)
    request = QuoteRequest(1, "0x" + "a" * 64, "A", "B", "1", "baseline", None)
    assert bridge._cache_key(request) != bridge._cache_key(request)
    names = {path.as_posix() for path in seen}
    assert any(name.endswith("/adapters/uniswap_v3/adapter.py") for name in names)
    assert any(name.endswith("/evaluator/evaluate.py") for name in names)


def test_no_qualified_state_is_not_a_legitimate_no_route(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(bridge, "_cache_key", lambda request: "unqualified")
    report = _report()
    report["best_split"] = None
    report["sources"][0]["usable_pools"] = 0
    monkeypatch.setattr("swaparch.web_api.quote_command", lambda *args, **kwargs: print(json.dumps(report)))
    with pytest.raises(RequestError) as error:
        bridge.quote({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1"})
    assert error.value.code == "qualification_required" and error.value.status == 409


def test_qualified_no_route_remains_a_report(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(bridge, "_cache_key", lambda request: "no-route")
    report = _report()
    report["best_split"] = None
    monkeypatch.setattr("swaparch.web_api.quote_command", lambda *args, **kwargs: print(json.dumps(report)))
    result = bridge.quote({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1"})
    assert result["report"]["best_split"] is None


def test_busy_solver_is_rejected_without_waiting(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(bridge, "_cache_key", lambda request: "busy")
    assert bridge._solver_lock.acquire(blocking=False)
    try:
        with pytest.raises(RequestError) as error:
            bridge.quote({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1"})
    finally:
        bridge._solver_lock.release()
    assert error.value.code == "busy" and error.value.status == 429


def test_web_cli_passes_explicit_bind_and_data_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("swaparch.web_api.serve", lambda *args: calls.append(args))
    assert main(["--host", "localhost", "--port", "9876", "--data-root", str(tmp_path)]) == 0
    assert calls == [("localhost", 9876, tmp_path)]


@pytest.mark.parametrize("port", ["0", "65536"])
def test_web_cli_rejects_invalid_port(port):
    with pytest.raises(SystemExit):
        main(["--port", port])


def test_missing_inventory_is_structured_missing_state(tmp_path, monkeypatch):
    bridge = QuoteBridge(tmp_path)
    monkeypatch.setattr(bridge, "_resolve_block", lambda value: (25896003, "0x" + "a" * 64))
    monkeypatch.setattr(
        bridge,
        "_cache_key",
        lambda request: (_ for _ in ()).throw(
            FileNotFoundError(
                "no discovery inventories found under SWAPARCH_ROOT/data/discovery/1; "
                "set SWAPARCH_ROOT to a prepared Swap Archeology data root"
            )
        ),
    )
    with pytest.raises(RequestError) as error:
        bridge.quote({"block": "25896003", "tokenIn": "A", "tokenOut": "B", "amount": "1"})
    assert error.value.code == "missing_state" and "SWAPARCH_ROOT" in str(error.value)
