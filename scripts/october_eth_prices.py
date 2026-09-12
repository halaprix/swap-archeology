"""Offline native-ETH versus WETH October price comparison.

This deliberately measures native ETH against the collected native-asset
adapters.  It does not insert a WETH bridge, so a difference is an inventory
and route-semantics result rather than a claim about a wrapped execution.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

import crash_slices
import perf_native
from eval_quote_performance import check_report

from swaparch.collection_quotes import prepared_collection_context

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs/october-price-gap/prices.json"
OUT = ROOT / "outputs/october-eth-prices"
SIZES = (1, 10, 100)
CASE_OUT = "USDC"


def _fingerprint(annotation: dict[str, Any], build: dict[str, Any]) -> str:
    """Bind cached reports to exact collection evidence, code and budgets."""
    files = ("scripts/october_eth_prices.py", "scripts/crash_slices.py",
             "scripts/perf_native.py", "scripts/eval_quote_performance.py")
    payload = {
        "case": ["ETH", CASE_OUT], "sizes": SIZES, "settings": crash_slices.SETTINGS,
        "collection": annotation["collection_evidence_identity"],
        "build": build["manifest"],
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt") as handle:
        return json.load(handle)


def _weth_report(row: dict[str, Any], size: int) -> dict[str, Any]:
    report_path = ROOT / row[f"report{size}"]
    value = _load_gzip(report_path)
    return value.get("report", value)


def _cached(path: Path, block_hash: str, fingerprint: str, amount: int) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = _load_gzip(path)
    report = value.get("report")
    if (value.get("fingerprint") == fingerprint and value.get("blockHash") == block_hash
            and value.get("amountRaw") == str(amount) and isinstance(report, dict)):
        return report
    return None


def _write_gzip(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with gzip.open(temporary, "wt") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _price(report: dict[str, Any], size: int) -> float | None:
    result = report.get("best_split")
    if not result or not result.get("feasible") or result.get("residual_in") != 0:
        return None
    return float(Decimal(result["amount_out"]) / Decimal(10**6) / Decimal(size))


def _route(report: dict[str, Any]) -> list[dict[str, Any]]:
    result = report.get("best_split") or {}
    return [{key: step[key] for key in ("pool_id", "token_in", "token_out", "amount_in", "amount_out")}
            for step in result.get("steps", [])]


def _missing(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep source-level absences visible; raw reports retain each refused leg."""
    return [*report.get("unsupported", []), *report.get("model_exclusions", [])]


def _run_native(context: Any, annotation: dict[str, Any], build: dict[str, Any], size: int) -> dict[str, Any]:
    with perf_native.optimized(context, build):
        _code, report = crash_slices._run_quote(context, annotation, ("ETH", CASE_OUT, str(size)))
    check_report(report)
    return report


def _native_report(saved: dict[str, Any], size: int, context: Any, annotation: dict[str, Any], build: dict[str, Any]) -> tuple[dict[str, Any], Path, str]:
    amount = size * 10**18
    fingerprint = _fingerprint(annotation, build)
    raw = OUT / "raw" / f"{context.block.number}-{context.block.hash[2:]}-ETH-USDC-{size}.json.gz"
    report = _cached(raw, context.block.hash, fingerprint, amount)
    if report is None:
        report = _run_native(context, annotation, build, size)
        _write_gzip(raw, {"fingerprint": fingerprint, "blockHash": context.block.hash,
                          "amountRaw": str(amount), "report": report})
    check_report(report)
    if report["block_hash"] != saved["blockHash"] or report["request"]["amount_in"] != amount:
        raise ValueError(f"native report identity mismatch at block {context.block.number}, size {size}")
    return report, raw, fingerprint


def _row(saved: dict[str, Any], context: Any, annotation: dict[str, Any], build: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {key: saved[key] for key in ("block", "blockHash", "timestamp", "chainlink", "aave")}
    row.update({"nativeETH": "0x0000000000000000000000000000000000000000",
                "qualificationMode": annotation["qualification_mode"], "networkRequests": 0,
                "routes": {}, "missing": {}, "sources": {}, "rawReports": {}})
    for size in SIZES:
        weth = _weth_report(saved, size)
        check_report(weth)
        native, raw, fingerprint = _native_report(saved, size, context, annotation, build)
        row[f"eth{size}"] = _price(native, size)
        row[f"weth{size}"] = _price(weth, size)
        row["routes"][str(size)] = {"nativeETH": _route(native), "WETH": _route(weth)}
        row["missing"][str(size)] = {"nativeETH": _missing(native), "WETH": _missing(weth)}
        row["sources"][str(size)] = {"nativeETH": native["sources"], "WETH": weth["sources"]}
        row["rawReports"][str(size)] = {"nativeETH": str(raw.relative_to(ROOT)), "WETH": saved[f"report{size}"],
                                       "fingerprint": fingerprint}
    return row


def _csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {**{key: row[key] for key in ("block", "blockHash", "timestamp", "chainlink", "aave",
                                         "eth1", "eth10", "eth100", "weth1", "weth10", "weth100")},
            "routes": json.dumps(row["routes"], separators=(",", ":")),
            "missing": json.dumps(row["missing"], separators=(",", ":")),
            "sources": json.dumps(row["sources"], separators=(",", ":")),
            "rawReports": json.dumps(row["rawReports"], separators=(",", ":"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", help="comma-separated saved blocks; defaults to all 86")
    args = parser.parse_args(argv)
    source = json.loads(INPUT.read_text())
    wanted = None if args.blocks is None else {int(item) for item in args.blocks.split(",")}
    selected = [row for row in source["rows"] if wanted is None or row["block"] in wanted]
    if not selected or (wanted is not None and {row["block"] for row in selected} != wanted):
        raise ValueError("requested blocks are absent from saved October price data")
    OUT.mkdir(parents=True, exist_ok=True)
    build = perf_native.load_build(perf_native.DEFAULT_BUILD)
    rows = []
    for saved in selected:
        context, annotation, offline = prepared_collection_context(saved["block"])
        if offline.network_requests != 0 or context.block.hash != saved["blockHash"]:
            raise ValueError(f"offline collection identity mismatch at block {saved['block']}")
        rows.append(_row(saved, context, annotation, build))
        print(json.dumps({"block": saved["block"], "completed": len(rows), "total": len(selected)}), flush=True)
    if wanted is not None:
        return 0
    payload = {"schemaVersion": 1, "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
               "nativeETH": "0x0000000000000000000000000000000000000000", "case": "ETH -> USDC",
               "sizes": list(SIZES), "units": "USDC per ETH; execution prices net of modelled pool fees, excluding gas",
               "comparison": "native ETH uses only collected native-asset routes; WETH is the saved aggregate. No WETH bridge is inserted.",
               "qualification": "collection-model-only; bounded optimizer, incomplete public pool inventory, no historical RFQ",
               "rows": rows}
    flat = [_csv_row(row) for row in rows]
    csv_path = OUT / "prices.csv"
    temporary_csv = csv_path.with_suffix(csv_path.suffix + f".tmp-{os.getpid()}")
    with temporary_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    for size in SIZES:
        gaps = [((Decimal(str(row[f"eth{size}"])) / Decimal(str(row[f"weth{size}"]))) - 1) * 10_000
                for row in rows if row[f"eth{size}"] is not None and row[f"weth{size}"] is not None]
        payload[f"nativeMinusWethMedianBps{size}"] = float(median(gaps)) if gaps else None
    _write_json(OUT / "prices.json", payload)
    temporary_csv.replace(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
