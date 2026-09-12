"""Validate the expanded October sources dataset, candidate floors, and served public artifacts."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_quote_performance import check_report


def main() -> int:
    out = ROOT / "outputs/october-sources-expanded"
    old = json.loads((ROOT / "outputs/october-sources-connectors/prices.json").read_text())
    counts: Counter[str] = Counter()
    routes: Counter[str] = Counter()
    maximum = None
    cases = 0
    per_case: Counter[str] = Counter()

    for row in old["rows"]:
        h = row["blockHash"]
        with gzip.open(out / "aggregate-raw" / f"{h}.json.gz", "rt") as f:
            raw = json.load(f)
        with gzip.open(ROOT / "outputs/october-sources-connectors/aggregate-raw" / f"{h}.json.gz", "rt") as f:
            before = json.load(f)

        assert raw["block"] == row["block"] and raw["blockHash"] == h
        assert set(raw["reports"]) == {f"{a}:{s}" for a in ("ETH", "WETH") for s in (1, 10, 100)}

        expansion = json.loads((ROOT / "outputs/october-expansion/records" / f"{h}.json").read_text())
        added = {r["pool_id"]: r for r in expansion["records"]}

        for key, report in raw["reports"].items():
            check_report(report)
            b = report["best_split"]
            req = report["request"]
            _asset, size = key.split(":")
            assert report["block_hash"] == h and req["amount_in"] == int(size) * 10**18
            assert req["allow_psm_dai_refund"] and b["feasible"] and b["residual_in"] == 0 and b["amount_in_spent"] == req["amount_in"]

            prior = before["reports"][key]["best_split"]["amount_out"]
            actual = b["amount_out"]
            assert actual >= prior, (row["block"], key)
            gain = Decimal(actual - prior) / Decimal(prior) * 10000
            if actual > prior:
                counts["improved"] += 1
                per_case[key] += 1
            else:
                counts["unchanged"] += 1

            kinds = set()
            for step in b["steps"]:
                # Exclude duplicate wrapper pool
                assert "0xa188eec8f81263234da3622a406892f3d630f98c" not in step["pool_id"]
                if step["pool_id"] in added:
                    r = added[step["pool_id"]]
                    symbols = {t["symbol"] for t in r["tokens"]}
                    kinds.add(
                        "Pancake V3" if r["family"] == "pancake_v3"
                        else "DAI-USDS converter" if r["family"] == "maker_sky_psm"
                        else "WBTC" if "WBTC" in symbols
                        else "USDS AMM"
                    )
            routes.update(kinds)

            if maximum is None or gain > Decimal(str(maximum["gainBps"])):
                price = Decimal(actual) / 10**6 / int(size)
                previous = Decimal(prior) / 10**6 / int(size)
                maximum = {
                    "block": row["block"],
                    "blockHash": h,
                    "timestamp": row["timestamp"],
                    "case": key,
                    "gainBps": float(gain),
                    "beforePrice": str(previous),
                    "afterPrice": str(price),
                    "chainlink": row["chainlink"],
                    "aave": row["aave"],
                    "newSources": sorted(kinds),
                }
                if row["chainlink"]:
                    oracle = Decimal(str(row["chainlink"]))
                    maximum.update({
                        "beforeBelowChainlinkBps": float((1 - previous / oracle) * 10000),
                        "afterBelowChainlinkBps": float((1 - price / oracle) * 10000),
                    })
            cases += 1

    assert cases == 1524
    result = {
        "blocks": 254,
        "cases": cases,
        **dict(counts),
        "regressions": 0,
        "improvementsByCase": dict(per_case),
        "winningRoutesUsingNewSources": dict(routes),
        "largestGain": maximum,
    }
    (out / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))

    # Served data and public hash coverage check
    served_json_path = ROOT / "frontend/public/october-sources.json"
    served_csv_path = ROOT / "frontend/public/october-sources.csv"
    canonical_json_path = out / "prices.json"
    canonical_csv_path = out / "prices.csv"

    if canonical_json_path.is_file() and served_json_path.is_file():
        canonical_json_bytes = canonical_json_path.read_bytes()
        served_json_bytes = served_json_path.read_bytes()
        assert canonical_json_bytes == served_json_bytes, "Served october-sources.json differs from canonical prices.json"

        canonical_csv_bytes = canonical_csv_path.read_bytes()
        served_csv_bytes = served_csv_path.read_bytes()
        assert canonical_csv_bytes == served_csv_bytes, "Served october-sources.csv differs from canonical prices.csv"

        served_data = json.loads(served_json_bytes)
        assert len(served_data["rows"]) == 254
        assert served_data["rows"][0]["block"] == 23549939
        assert served_data["rows"][-1]["block"] == 23550192

        # Oracle coverage verification
        for s_row, o_row in zip(served_data["rows"], old["rows"], strict=True):
            assert s_row["block"] == o_row["block"]
            assert s_row["blockHash"] == o_row["blockHash"]
            assert s_row["chainlink"] == o_row["chainlink"]
            assert s_row["aave"] == o_row["aave"]

        print(
            f"Public frontend artifacts verified: {len(served_data['rows'])} blocks match canonical prices.json "
            f"(SHA256: {hashlib.sha256(served_json_bytes).hexdigest()[:16]}...)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
