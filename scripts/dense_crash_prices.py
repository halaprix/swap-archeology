"""Dense, bounded October crash market-price series; never starts sweep workers.

The default is cache-only Chainlink reads.  ``--acquire-oracle`` permits pinned
read-only calls for missing oracle entries; it does not perform Aave policy
reads, which remain a separate provenance pass.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import crash_slices

from swaparch import cli
from swaparch.collection_quotes import prepared_collection_context
from swaparch.explorer import _as_strings, _display_report
from swaparch.rpc.client import RpcClient

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "outputs/dense-crash/market-oracle-23549800-23550200.json"
DEFAULT_RAW = PROJECT / "outputs/dense-crash/raw"
DEFAULT_CACHE = PROJECT / "outputs/crash-slices/rpc-cache"
CASE = ("WETH", "USDC", "100")


def _fingerprint(annotation: dict[str, Any], mode: str, manifest: dict[str, Any] | None) -> str:
    payload = {"script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "crashSlices": hashlib.sha256((PROJECT / "scripts/crash_slices.py").read_bytes()).hexdigest(),
               "settings": crash_slices.SETTINGS, "case": CASE,
               "collection": annotation.get("collection_evidence_identity"), "executionMode": mode,
               "nativeBuild": manifest,
               "crashSlicesFingerprint": crash_slices._fingerprint(annotation, mode, manifest)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _run_quote(context: Any, annotation: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = cli.quote_command(context.block.number, *CASE, grid_parts=crash_slices.SETTINGS["grid_parts"],
                                 solver_name="search", max_steps=crash_slices.SETTINGS["max_steps"],
                                 beam_width=crash_slices.SETTINGS["beam_width"],
                                 max_expansions=crash_slices.SETTINGS["max_expansions"], offline=True,
                                 prepared_context=context, report_annotations=annotation)
    return code, json.loads(stream.getvalue())


def row_from_report(context: Any, annotation: dict[str, Any], report: dict[str, Any], code: int,
                    oracle: dict[str, Any], raw_path: str, fingerprint: str) -> dict[str, Any]:
    request = report["request"]
    price = crash_slices._price(report.get("best_split"), request["amount_in"],
                                request["decimals_in"], request["decimals_out"])
    oracle_price = oracle["oraclePrice"]
    gap = (float((__import__("decimal").Decimal(str(price)) / oracle["_decimal"] - 1) * 10_000)
           if price is not None and oracle_price is not None else None)
    return {"id": str(context.block.number), "block": context.block.number, "blockHash": context.block.hash,
            "timestamp": datetime.fromtimestamp(context.block.timestamp, UTC).isoformat().replace("+00:00", "Z"),
            "timestampUnix": context.block.timestamp, "tokenIn": "WETH", "tokenOut": "USDC",
            "amountRaw": str(request["amount_in"]), "aggregatedPrice": price, "oraclePrice": oracle_price,
            "gapBps": gap, "oracleAgeSeconds": oracle["oracleAgeSeconds"], "oracleStatus": oracle["oracleStatus"],
            "oracleEvidence": crash_slices._json_ints(oracle["feed"]), "qualificationMode": annotation["qualification_mode"],
            "fingerprint": fingerprint,
            "sourceCoverage": {"selectedFamilies": report["selected_families"],
                               "usablePools": sum(s.get("usable_pools", 0) for s in report["sources"]),
                               "unsupported": _as_strings(_display_report(report))["unsupported"]},
            "report": {"path": raw_path, "exitCode": code, "display": _as_strings(_display_report(report))}}


def _checkpoint(path: Path, document: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
    document["rows"] = [rows[str(number)] for number in sorted(map(int, rows))]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=23549800)
    parser.add_argument("--end", type=int, default=23550200)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--oracle-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--acquire-oracle", action="store_true")
    parser.add_argument("--benchmark-scopes", action="store_true",
                        help="use the explicitly benchmark-only, single-thread native scope")
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must be at least --start")
    document = json.loads(args.output.read_text()) if args.output.is_file() else {"schemaVersion": 1, "rows": []}
    rows = {row["id"]: row for row in document["rows"]}
    oracle_client = RpcClient(cache_root=args.oracle_cache, offline=not args.acquire_oracle)
    loaded = None
    if args.benchmark_scopes:
        import perf_native
        loaded = perf_native.load_build(perf_native.DEFAULT_BUILD)
    mode = "benchmark-scopes-single-thread" if loaded else "standard"
    started, quoted, resumed = perf_counter(), 0, 0
    for number in range(args.start, args.end + 1):
        context, annotation, offline = prepared_collection_context(number)
        assert offline.network_requests == 0
        fingerprint = _fingerprint(annotation, mode, loaded["manifest"] if loaded else None)
        prior = rows.get(str(number))
        if prior and prior.get("blockHash") == context.block.hash and prior.get("fingerprint") == fingerprint and (PROJECT / prior["report"]["path"]).is_file():
            resumed += 1
            continue
        oracle = crash_slices.chainlink_quote(oracle_client, context.block, "WETH")
        manager = perf_native.optimized(context, loaded) if loaded else __import__("contextlib").nullcontext()
        with manager:
            code, report = _run_quote(context, annotation)
        quoted += 1
        raw = args.raw_dir / f"{number}-WETH-USDC-100.json.gz"
        raw.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(raw, "wt") as handle:
            json.dump(report, handle)
        rows[str(number)] = row_from_report(context, annotation, report, code, oracle,
                                             str(raw.relative_to(PROJECT)), fingerprint)
        document.update({"schemaVersion": 1, "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                         "range": {"start": args.start, "end": args.end}, "case": {"tokenIn": "WETH", "tokenOut": "USDC", "amount": "100"},
                         "qualificationMode": "collection-model-only", "oracleMode": "acquire" if args.acquire_oracle else "cache-only",
                         "aavePolicy": "not collected by this market/oracle series", "executionMode": mode,
                         "run": {"quotedCount": quoted, "resumedCount": resumed,
                                 "elapsedSeconds": perf_counter() - started,
                                 "oracleCacheNetworkRequests": oracle_client.network_requests}})
        _checkpoint(args.output, document, rows)
        print(f"block={number}", flush=True)
    document["run"] = {"quotedCount": quoted, "resumedCount": resumed,
                       "elapsedSeconds": perf_counter() - started,
                       "oracleCacheNetworkRequests": oracle_client.network_requests}
    _checkpoint(args.output, document, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
