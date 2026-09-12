from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
SPEC = importlib.util.spec_from_file_location(
    "crash_slices", Path(__file__).parents[1] / "scripts/crash_slices.py"
)
crash = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(crash)


def _word(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def _round(answer: int, updated: int) -> str:
    return "0x" + "".join(_word(value) for value in (1, answer, updated - 1, updated, 1))


def _description(text: str) -> str:
    encoded = text.encode()
    padding = (32 - len(encoded) % 32) % 32
    return "0x" + _word(32) + _word(len(encoded)) + (encoded + b"\0" * padding).hex()


def test_selected_pins_include_exact_controls_and_known_divergence():
    pins = crash._window_blocks()
    assert len(pins) == 16
    assert {21895692, 21895693, 23550044} <= {pin["block"] for pin in pins}


def test_decode_round_supports_aggregator_v3_words():
    decoded = crash.decode_round(_round(115424136, 1740106391), 8)
    assert decoded["price"] == 1.15424136
    assert decoded["updatedAt"] == 1740106391


def test_chainlink_cross_rate_divides_usdc_for_susde_and_weth():
    class Client:
        def eth_call(self, address, data, block):
            answer = {crash.SUSDE_USD: 100_000_000, crash.ETH_USD: 200_000_000,
                      crash.USDC_USD: 50_000_000}[address]
            raw = (_word(8) if data == crash.DECIMALS else _round(answer, 90) if data == crash.LATEST_ROUND_DATA
                   else _description({crash.SUSDE_USD: "sUSDe / USD", crash.ETH_USD: "ETH / USD",
                                      crash.USDC_USD: "USDC / USD"}[address]))
            return SimpleNamespace(success=True, raw=raw)
    block = SimpleNamespace(timestamp=100)
    assert crash.chainlink_quote(Client(), block, "sUSDe")["oraclePrice"] == 2.0
    assert crash.chainlink_quote(Client(), block, "WETH")["oraclePrice"] == 4.0


def test_row_exposes_display_report_and_single_pool_price():
    report = {
        "chain": 1, "block": 7, "block_hash": "0xabc", "timestamp": 100,
        "request": {"token_in": "in", "token_out": "out", "symbol_in": "sUSDe", "symbol_out": "USDC",
                    "decimals_in": 18, "decimals_out": 6, "amount_in": 1_000 * 10**18},
        "best_split": {"amount_out": 1_001 * 10**6, "steps": [], "search_info": {}, "gas_estimate": 0},
        "single_pool_baseline": {"amount_out": 999 * 10**6}, "sources": [], "unsupported": [],
        "selected_families": [], "offline": True, "network_requests": 0, "solver_diagnostics": {},
        "requested_solver": "search", "limitations": [],
    }
    context = SimpleNamespace(block=SimpleNamespace(number=7, hash="0xabc", timestamp=100))
    row = crash.row_from_report({"window": "w", "label": "l"}, context,
                                {"qualification_mode": "collection-model-only"},
                                ("sUSDe", "USDC", "1000"), report, 0,
                                {"oraclePrice": 1.0, "oracleAgeSeconds": 5, "oracleStatus": "ok", "feed": {},
                                 "_decimal": __import__("decimal").Decimal("1")},
                                "outputs/raw.json", "standard")
    assert row["singlePoolBaselinePrice"] == 0.999
    assert row["aggregatedPrice"] == 1.001
    assert row["displayReport"]["best_split"]["amount_out"] == str(1_001 * 10**6)
    assert row["sliceId"] == "w"
