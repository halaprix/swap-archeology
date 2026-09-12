"""Export the five pinned saved reports into the static frontend catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from swaparch.explorer import _as_strings, _display_report, _read_json, _report_paths, _validate

ROOT = Path(__file__).resolve().parents[1]
PINS = {23549991, 23550060, 23728292, 24356381, 25896003}
PIN_LABELS = {23549991: "Crash 1 · early", 23550060: "Crash 1 · late",
              23728292: "Crash 3", 24356381: "Crash 2", 25896003: "Calm control"}
INPUTS = tuple(ROOT / "data/results" / name for name in
               ("six-family-full-intermediates", "source-subsets", "pre-phase8"))


def _identifier(report: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_display_report(report), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def export_catalog(paths: tuple[Path, ...] = INPUTS) -> dict[str, Any]:
    reports: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[str, Any]] = {}
    pins: dict[int, dict[str, str]] = {}
    for path in _report_paths(paths):
        report = _validate(_read_json(path), path)
        if report is None or report["block"] not in PINS:
            continue
        displayed = _as_strings(_display_report(report))
        identity = _identifier(report)
        previous = reports.setdefault(identity, {"id": identity, "origin": "saved", "report": displayed})
        if previous["report"] != displayed:
            raise ValueError(f"conflicting saved report identity: {path}")
        pin = {"number": str(report["block"]), "hash": str(report.get("block_hash", "")),
               "timestamp": str(report.get("timestamp", "")), "label": PIN_LABELS[report["block"]]}
        if pins.setdefault(report["block"], pin) != pin:
            raise ValueError(f"conflicting saved block identity: {path}")
        request = report["request"]
        for direction in ("in", "out"):
            address = str(request[f"token_{direction}"]).lower()
            token = {"symbol": request[f"symbol_{direction}"], "address": address,
                     "decimals": int(request[f"decimals_{direction}"])}
            if tokens.setdefault(address, token) != token:
                raise ValueError(f"conflicting saved token identity: {path}")
    return {"schemaVersion": 1, "pins": [pins[number] for number in sorted(pins)],
            "tokens": sorted(tokens.values(), key=lambda item: (item["symbol"], item["address"])),
            "reports": [reports[key] for key in sorted(reports)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "frontend/public/data/catalog.json")
    args = parser.parse_args(argv)
    catalog = export_catalog()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, separators=(",", ":")) + "\n")
    print(f"wrote {len(catalog['reports'])} reports to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
