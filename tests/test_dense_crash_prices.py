from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location("dense", Path(__file__).parents[1] / "scripts/dense_crash_prices.py")
dense = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(dense)


def test_dense_row_uses_oracle_cross_rate_and_string_raw_amount():
    report = {"request": {"amount_in": 100 * 10**18, "decimals_in": 18, "decimals_out": 6},
              "best_split": {"amount_out": 200 * 10**6, "steps": [], "search_info": {}, "gas_estimate": 0},
              "single_pool_baseline": None, "chain": 1, "block": 1, "block_hash": "0x1", "timestamp": 1,
              "selected_families": [], "sources": [], "unsupported": [], "requested_solver": "search", "limitations": []}
    context = SimpleNamespace(block=SimpleNamespace(number=1, hash="0x1", timestamp=1))
    row = dense.row_from_report(context, {"qualification_mode": "collection-model-only"}, report, 0,
                                {"oraclePrice": 2.0, "_decimal": Decimal(2), "oracleAgeSeconds": 3,
                                 "oracleStatus": "ok", "feed": {}}, "outputs/x.json.gz", "f")
    assert row["amountRaw"] == str(100 * 10**18)
    assert row["gapBps"] == 0.0
