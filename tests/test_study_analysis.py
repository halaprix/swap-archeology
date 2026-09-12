"""Exact, offline saved-quote analysis checks."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "analyze_study", Path(__file__).resolve().parents[1] / "scripts/analyze_study.py"
)
analyze_study = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(analyze_study)


HASH = "0x" + "ab" * 32
WETH = "0x" + "01" * 20
USDC = "0x" + "02" * 20


def quote(amount_in: int, amount_out: int | None, baseline: int | None,
          *, pair: tuple[str, str] = (WETH, USDC), block: int = 10) -> dict:
    token_in, token_out = pair
    row = {
        "chain": 1,
        "block": block,
        "block_hash": HASH,
        "request": {
            "token_in": token_in,
            "symbol_in": "WETH" if token_in == WETH else "USDT",
            "decimals_in": 18 if token_in == WETH else 6,
            "token_out": token_out,
            "symbol_out": "USDC",
            "decimals_out": 18 if token_out == WETH else 6,
            "amount_in": amount_in,
        },
        "requested_solver": "search",
        "selected_families": ["uniswap_v2", "uniswap_v3"],
        "single_pool_baseline": None,
        "best_split": None,
    }
    if baseline is not None:
        row["single_pool_baseline"] = {"amount_out": baseline, "amount_in_spent": amount_in,
                                       "feasible": True, "residual_in": 0}
    if amount_out is not None:
        row["best_split"] = {"amount_out": amount_out, "amount_in_spent": amount_in,
                             "feasible": True, "residual_in": 0}
    return row


def oracle() -> dict:
    return {
        "block": {"chain": 1, "number": 10, "hash": HASH},
        "prices": [
            {"token": {"address": WETH, "symbol": "WETH", "decimals": 18},
             "price_base": 200, "available": True},
            {"token": {"address": USDC, "symbol": "USDC", "decimals": 6},
             "price_base": 95, "available": True},
        ],
    }


def test_mixed_decimals_depeg_gain_negative_gain_and_no_route(tmp_path):
    quote_dir = tmp_path / "quotes"
    oracle_dir = tmp_path / "oracle"
    quote_dir.mkdir()
    oracle_dir.mkdir()
    (oracle_dir / f"{HASH}.json").write_text(json.dumps(oracle()))
    (quote_dir / "small.json").write_text(json.dumps(quote(10**18, 1_100_000, 1_000_000)))
    (quote_dir / "large.json").write_text(json.dumps(quote(2 * 10**18, 1_800_000, 2_000_000)))
    (quote_dir / "none.json").write_text(json.dumps(quote(10**6, None, None,
                                                            pair=(USDC, WETH))))
    zero = quote(10**17, 0, 1_000_000)
    zero["selected_families"] = ["zero-reference"]
    later = quote(2 * 10**18, 1_800_000, 2_000_000)
    later["selected_families"] = ["zero-reference"]
    (quote_dir / "zero.json").write_text(json.dumps(zero))
    (quote_dir / "later.json").write_text(json.dumps(later))

    result = analyze_study.analyze_directory(quote_dir, oracle_dir)
    rows = {row["quote_path"]: row["analysis"] for row in result["rows"]}
    small = rows["small.json"]
    large = rows["large.json"]
    none = rows["none.json"]
    later = rows["later.json"]

    assert small["execution_price"] == {"numerator": 11, "denominator": 10,
                                         "decimal": "1.1"}
    assert small["routing_gain_bps"] == {"numerator": 1000, "denominator": 1,
                                          "decimal": "1000"}
    assert small["oracle_price"] == {"numerator": 40, "denominator": 19,
                                      "decimal": "2.105263157894736842105263"}
    assert small["oracle_discount_bps"] == {"numerator": 4775, "denominator": 1,
                                             "decimal": "4775"}
    assert large["routing_gain_bps"] == {"numerator": -1000, "denominator": 1,
                                          "decimal": "-1000"}
    assert large["size_deterioration_bps"]["numerator"] == 20000
    assert large["size_deterioration_bps"]["denominator"] == 11
    assert none["route_status"] == "no_tested_route"
    assert none["execution_price"] is None
    assert none["routing_gain_bps"] is None
    assert none["oracle_price"] is None
    assert later["size_deterioration_bps"] is None


def test_reference_hash_and_quote_request_identity_are_strict(tmp_path):
    wrong = tmp_path / "wrong.json"
    value = oracle()
    value["block"]["hash"] = "0x" + "cd" * 32
    wrong.write_text(json.dumps(value))
    try:
        analyze_study.load_oracle_reference(wrong, chain=1, block=10, block_hash=HASH)
    except ValueError as error:
        assert "block hash" in str(error)
    else:
        raise AssertionError("wrong oracle hash was accepted")

    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    invalid = quote(10**18, 1_100_000, 1_000_000)
    invalid["best_split"]["amount_in_spent"] = 2 * 10**18
    (quote_dir / "invalid.json").write_text(json.dumps(invalid))
    try:
        analyze_study.analyze_directory(quote_dir)
    except ValueError as error:
        assert "amount_in_spent" in str(error)
    else:
        raise AssertionError("mismatched quote request identity was accepted")


def test_analysis_rejects_decimal_drift_and_malformed_reports(tmp_path):
    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    (quote_dir / "summary.json").write_text(json.dumps({"scope": "summary"}))
    (quote_dir / "bad.json").write_text(json.dumps({"request": {}}))
    try:
        analyze_study.analyze_directory(quote_dir, None)
    except TypeError as error:
        assert "malformed quote report" in str(error)
    else:
        raise AssertionError("malformed non-summary report was silently skipped")

    quote_dir.joinpath("bad.json").unlink()
    first = quote(10**18, 1_100_000, 1_000_000)
    second = quote(2 * 10**18, 2_000_000, 2_000_000)
    second["request"]["decimals_in"] = 17
    (quote_dir / "first.json").write_text(json.dumps(first))
    (quote_dir / "second.json").write_text(json.dumps(second))
    try:
        analyze_study.analyze_directory(quote_dir, None)
    except ValueError as error:
        assert "inconsistent decimals" in str(error)
    else:
        raise AssertionError("decimal drift was silently mixed")


def test_candidate_completeness_and_oracle_integer_identity(tmp_path):
    for field in ("best_split", "single_pool_baseline"):
        for key, bad in (("feasible", False), ("feasible", None),
                         ("residual_in", 1), ("residual_in", None),
                         ("amount_in_spent", None)):
            value = quote(10**18, 1_100_000, 1_000_000)
            value[field][key] = bad
            with pytest.raises((TypeError, ValueError)):
                analyze_study._validate_quote(value, Path("invalid.json"))
    path = tmp_path / "oracle.json"
    for key in ("chain", "number"):
        value = oracle()
        value["block"][key] = True
        path.write_text(json.dumps(value))
        with pytest.raises(TypeError):
            analyze_study.load_oracle_reference(path, chain=1, block=10, block_hash=HASH)


def test_size_cohorts_follow_configured_budget_despite_winner_change(tmp_path):
    small = quote(10**18, 1_100_000, 1_000_000)
    large = quote(2 * 10**18, 1_800_000, 2_000_000)
    for row in (small, large):
        row["search_limits"] = {"max_steps": 6, "grid_parts": 4}
    large["best_split"]["search_info"] = {"search_limits": {"max_steps": 6}}
    for name, row in (("small", small), ("large", large)):
        (tmp_path / f"{name}.json").write_text(json.dumps(row))
    result = analyze_study.analyze_directory(tmp_path, None)
    analysis = next(row["analysis"] for row in result["rows"] if row["quote_path"] == "large.json")
    assert analysis["size_reference_amount_in_raw"] == 10**18
    assert analysis["size_deterioration_bps"]["numerator"] == 20000
    assert analysis["size_deterioration_bps"]["denominator"] == 11


def test_analysis_reads_gzip_quote_reports(tmp_path):
    value = quote(10**18, 1_100_000, 1_000_000)
    with gzip.open(tmp_path / "quote.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(value, handle)

    result = analyze_study.analyze_directory(tmp_path, None)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["quote_path"] == "quote.json.gz"
