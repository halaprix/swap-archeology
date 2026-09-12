"""Offline, per-pool ETH/WETH -> USDC quotes for the October 10 window.

This deliberately evaluates each direct pool independently from its unchanged
prepared block state.  It is a pool comparison, not a routed-price claim.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import monotonic, time_ns
from typing import Any

import crash_slices
import october_eth_prices
import perf_native
from eval_quote_performance import check_report

from swaparch.collection_quotes import prepared_collection_context
from swaparch.core.protocols import Unsupported
from swaparch.core.types import Plan, Step, TradeRequest
from swaparch.evaluator.evaluate import Evaluator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "outputs/october-sources-expanded"
OUT = DEFAULT_OUT
MARKET = ROOT / "outputs/dense-crash/market-oracle-23549800-23550200.json"
REFERENCES = ROOT / "outputs/dense-crash/references.json"
PREV_OUT = ROOT / "outputs/october-sources-connectors"
PREV_PRICES = PREV_OUT / "prices.json"
PREV_AGGREGATES = PREV_OUT / "aggregate-raw"
FRONTEND_PUBLIC = ROOT / "frontend/public"

START, END = 23_549_939, 23_550_192
SIZES = (1, 10, 100)
ASSETS = ("ETH", "WETH")
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _source_fingerprint() -> str:
    """Only code and pinned input identities invalidate direct-pool cache."""
    files = [ROOT / "scripts/october_source_prices.py", ROOT / "src/swaparch"]
    digest = hashlib.sha256()
    for source in files:
        paths = [source] if source.is_file() else sorted(source.rglob("*.py"))
        for path in paths:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    digest.update(json.dumps({"sizes": SIZES, "assets": ASSETS, "usdc": USDC}, sort_keys=True).encode())
    return digest.hexdigest()


def _ensure_perf_native_build() -> dict[str, Any]:
    try:
        return perf_native.load_build(perf_native.DEFAULT_BUILD)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Native build stale or missing ({exc}), rebuilding...", flush=True)
        subprocess.run(
            [
                "uv", "run", "--offline", "--with", "cython", "--with", "setuptools",
                "python", "scripts/build_perf_native.py",
            ],
            check=True,
            cwd=ROOT,
        )
        return perf_native.load_build(perf_native.DEFAULT_BUILD)


def validate_supplement_coverage(blocks: Iterable[int], supplements: list[Path], market: dict[int, dict[str, Any]]) -> None:
    """Fail-closed check: every requested block must have complete records and snapshots in every supplement."""
    for s in supplements:
        if not s.is_dir():
            raise FileNotFoundError(f"Mandatory supplement directory does not exist: {s}")
    for number in blocks:
        block_hash = market[number]["blockHash"]
        for s in supplements:
            rec_file = s / "records" / f"{block_hash}.json"
            if not rec_file.is_file():
                raise FileNotFoundError(f"Fail-closed: missing supplement record {rec_file} for block {number}")
            snap_dir = s / "snapshots" / "1" / block_hash
            if not snap_dir.is_dir():
                raise FileNotFoundError(f"Fail-closed: missing supplement snapshot dir {snap_dir} for block {number}")
            if not (snap_dir / "header.json").is_file():
                raise FileNotFoundError(f"Fail-closed: missing snapshot header in {snap_dir} for block {number}")
            if not (snap_dir / "calls.json.gz").is_file():
                raise FileNotFoundError(f"Fail-closed: missing snapshot calls in {snap_dir} for block {number}")


def load_supplemented_context(number: int, supplements: list[Path]) -> tuple[Any, dict[str, Any], Any]:
    context, annotation, client = prepared_collection_context(number)
    from swaparch.collection_supplement import supplement_context
    for supp_root in supplements:
        context, annotation = supplement_context(context, annotation, supp_root)
    return context, annotation, client


def _fee_bps(state: Any) -> float | None:
    config = state.record.config
    if state.record.family == "uniswap_v2":
        numerator, denominator = config.get("fee_numerator"), config.get("fee_denominator")
        return (denominator - numerator) * 10_000 / denominator if isinstance(numerator, int) and isinstance(denominator, int) else 30.0
    if state.record.family == "fluid_dex":
        return None  # Fluid's effective fee is stateful; a static metadata number would mislead.
    fee = config.get("fee")
    # Uniswap V3/V4, Pancake V3, and Fluid record fees in millionths; preserve the actual
    # configured rate as bps without pretending a missing fee is zero.
    return fee / 100 if isinstance(fee, int) else None


def _pool_metadata(states: tuple[Any, ...]) -> list[dict[str, Any]]:
    pools = []
    for state in states:
        tokens = {token.symbol: token for token in state.tokens()}
        if "USDC" not in tokens:
            continue
        for asset in ASSETS:
            if asset in tokens:
                pools.append({
                    "id": state.record.pool_id, "family": state.record.family,
                    "address": state.record.pool, "inputSymbol": asset,
                    "feeBps": _fee_bps(state), "label": f"{state.record.family}:{state.record.pool}",
                })
    return sorted(pools, key=lambda item: item["id"])


def _quote(state: Any, asset: str, amount: int) -> dict[str, Any]:
    tokens = {token.symbol: token for token in state.tokens()}
    try:
        # quote_exact_in calls swap on immutable/replacement state; no quote is
        # allowed to consume a prior pool's state.
        amount_out = state.quote_exact_in(tokens[asset].address, tokens["USDC"].address, amount)
        if amount_out <= 0:
            raise ValueError("non-positive direct output")
    except (Unsupported, ValueError, OverflowError) as exc:
        return {"price": None, "amountOut": None, "reason": str(exc)}
    return {
        "price": float(Decimal(amount_out) / Decimal(10**6) / (Decimal(amount) / Decimal(10**18))),
        "amountOut": str(amount_out),
    }


def _best_direct(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A family/pool winner is max positive price; unavailable is never zero."""
    candidates = [quote for quote in quotes if quote["price"] is not None]
    return max(candidates, key=lambda quote: quote["price"]) if candidates else None


def _self_check() -> None:
    assert _best_direct([{"price": None}, {"price": 2.0}, {"price": 3.0}])["price"] == 3.0
    assert _best_direct([{"price": None}]) is None


def _raw_path(block_hash: str) -> Path:
    return OUT / "raw" / f"{block_hash.lower()}.json.gz"


def _aggregate_path(block_hash: str) -> Path:
    return OUT / "aggregate-raw" / f"{block_hash.lower()}.json.gz"


def _old_report(context: Any, annotation: dict[str, Any], build: dict[str, Any], asset: str, size: int,
                aggregate_fingerprint: str) -> dict[str, Any] | None:
    """Adopt only old wrappers whose recorded identity still matches current code."""
    if asset == "WETH" and size in (1, 10):
        path = ROOT / "outputs/october-price-gap" / f"{context.block.number}-{size}.json.gz"
        if not path.is_file():
            return None
        with gzip.open(path, "rt") as handle:
            wrapped = json.load(handle)
        if wrapped.get("identity") != aggregate_fingerprint:
            return None
        report = wrapped.get("report")
    elif asset == "ETH":
        path = ROOT / "outputs/october-eth-prices/raw" / (
            f"{context.block.number}-{context.block.hash.removeprefix('0x')}-ETH-USDC-{size}.json.gz"
        )
        if not path.is_file():
            return None
        with gzip.open(path, "rt") as handle:
            wrapped = json.load(handle)
        if wrapped.get("fingerprint") != october_eth_prices._fingerprint(annotation, build):
            return None
        report = wrapped.get("report")
    elif asset == "WETH" and size == 100:
        payload = {
            "script": hashlib.sha256((ROOT / "outputs/dense-crash/dense-collector-source.py.txt").read_bytes()).hexdigest(),
            "crashSlices": hashlib.sha256((ROOT / "scripts/crash_slices.py").read_bytes()).hexdigest(),
            "settings": crash_slices.SETTINGS, "case": ("WETH", "USDC", "100"),
            "collection": annotation.get("collection_evidence_identity"),
            "executionMode": "benchmark-scopes-single-thread", "nativeBuild": build["manifest"],
            "crashSlicesFingerprint": aggregate_fingerprint,
        }
        dense = _market_rows()[context.block.number]
        if dense.get("fingerprint") != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest():
            return None
        path = ROOT / "outputs/dense-crash/raw" / f"{context.block.number}-WETH-USDC-100.json.gz"
        if not path.is_file():
            return None
        with gzip.open(path, "rt") as handle:
            report = json.load(handle)
    else:
        return None
    if not isinstance(report, dict) or report.get("block_hash") != context.block.hash:
        return None
    if report.get("request", {}).get("amount_in") != size * 10**18:
        return None
    check_report(report)
    return report


def _read_raw(path: Path, block_hash: str, fingerprint: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with gzip.open(path, "rt") as handle:
        value = json.load(handle)
    if value.get("blockHash") == block_hash and value.get("fingerprint") == fingerprint:
        return value
    return None


def _write_gzip(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time_ns()}")
    with gzip.open(temporary, "wt") as handle:
        json.dump(value, handle, separators=(",", ":"))
    temporary.replace(path)


def _write_checkpoint(out_dir: Path, data: dict[str, Any]) -> None:
    path = out_dir / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time_ns()}")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def _publish_frontend_atomically(out_dir: Path, public_dir: Path) -> None:
    public_dir.mkdir(parents=True, exist_ok=True)
    json_src = out_dir / "prices.json"
    csv_src = out_dir / "prices.csv"
    if not json_src.is_file() or not csv_src.is_file():
        raise FileNotFoundError("Cannot publish: canonical prices.json or prices.csv missing")

    json_dst = public_dir / "october-sources.json"
    json_tmp = json_dst.with_suffix(f".json.tmp-{os.getpid()}-{time_ns()}")
    json_tmp.write_bytes(json_src.read_bytes())
    json_tmp.replace(json_dst)

    csv_dst = public_dir / "october-sources.csv"
    csv_tmp = csv_dst.with_suffix(f".csv.tmp-{os.getpid()}-{time_ns()}")
    csv_tmp.write_bytes(csv_src.read_bytes())
    csv_tmp.replace(csv_dst)


def _market_rows() -> dict[int, dict[str, Any]]:
    rows = json.loads(MARKET.read_text())["rows"]
    return {row["block"]: row for row in rows if START <= row["block"] <= END}


def _reference_rows() -> dict[int, dict[str, Any]]:
    return {row["block"]: row for row in json.loads(REFERENCES.read_text())["rows"] if START <= row["block"] <= END}


def _availability(context: Any, family_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    inventory = {item["family"]: item for item in context.inventories}
    direct_by_family = {family: 0 for family in inventory}
    for row in family_rows:
        direct_by_family[row["family"]] += 1
    return {
        family: {
            "status": item["status"],
            "usableCount": sum(state.record.family == family for state in context.states),
            "directCount": direct_by_family[family],
        }
        for family, item in sorted(inventory.items())
    }


def _re_evaluate_candidate(
    context: Any,
    annotation: dict[str, Any],
    asset: str,
    size: int,
    prev_split: dict[str, Any],
) -> dict[str, Any] | None:
    tokens_by_symbol = {t.symbol: t for t in context.tokens.values()}
    token_in = tokens_by_symbol.get(asset)
    token_out = tokens_by_symbol.get("USDC")
    if not token_in or not token_out or "steps" not in prev_split:
        return None
    req = TradeRequest(
        token_in,
        token_out,
        size * 10**18,
        allow_psm_dai_refund=bool(annotation.get("supplement_identity")),
    )
    steps = tuple(
        Step(s["pool_id"], s["token_in"], s["token_out"], s["amount_in"])
        for s in prev_split["steps"]
    )
    plan = Plan(
        req,
        steps,
        prev_split.get("solver", "incumbent_floor"),
        prev_split.get("search_info", {}),
    )
    evaluator = Evaluator()
    re_eval = evaluator.evaluate(plan, context.states)
    if re_eval.feasible and re_eval.residual_in == 0 and re_eval.amount_in_spent == req.amount_in:
        return {
            "amount_out": re_eval.amount_out,
            "amount_in_spent": re_eval.amount_in_spent,
            "residual_in": re_eval.residual_in,
            "feasible": re_eval.feasible,
            "solver": plan.solver,
            "search_info": plan.search_info,
            "steps": [
                {
                    "pool_id": r.step.pool_id,
                    "token_in": r.step.token_in,
                    "token_out": r.step.token_out,
                    "amount_in": r.step.amount_in,
                    "amount_out": r.amount_out,
                }
                for r in re_eval.steps
            ],
            "gas_estimate": re_eval.gas_estimate,
            "terminal_refund": dict(re_eval.terminal_refund),
        }
    return None


def _aggregate_report(
    context: Any,
    annotation: dict[str, Any],
    build: dict[str, Any],
    asset: str,
    size: int,
    fingerprint: str,
) -> dict[str, Any]:
    """Use a valid saved quote when available, otherwise quote the frozen state with candidate floor fallback."""
    path = _aggregate_path(context.block.hash)
    cached = _read_raw(path, context.block.hash, fingerprint)
    key = f"{asset}:{size}"
    if cached and key in cached.get("reports", {}):
        return cached["reports"][key]
    report = _old_report(context, annotation, build, asset, size, fingerprint)
    if report is None:
        with perf_native.optimized(context, build):
            _code, report = crash_slices._run_quote(
                context,
                annotation,
                (asset, "USDC", str(size)),
                allow_psm_dai_refund=bool(annotation.get("supplement_identity")),
            )
        # Check against previous candidate floor from outputs/october-sources-connectors
        prev_agg_path = PREV_AGGREGATES / f"{context.block.hash.lower()}.json.gz"
        if prev_agg_path.is_file():
            with gzip.open(prev_agg_path, "rt") as handle:
                prev_data = json.load(handle)
            prev_rep = prev_data.get("reports", {}).get(key)
            if prev_rep and prev_rep.get("best_split", {}).get("feasible"):
                prev_split = prev_rep["best_split"]
                prev_out = int(prev_split["amount_out"])
                cur_split = report.get("best_split")
                cur_out = int(cur_split["amount_out"]) if (cur_split and cur_split.get("feasible")) else 0
                if cur_out < prev_out:
                    re_eval = _re_evaluate_candidate(context, annotation, asset, size, prev_split)
                    if re_eval and re_eval["amount_out"] >= prev_out:
                        report["best_split"] = re_eval
                        cur_out = int(re_eval["amount_out"])
                if cur_out < prev_out:
                    raise AssertionError(
                        f"Candidate floor regressed at block {context.block.number} {key}: {cur_out} < {prev_out}"
                    )

    check_report(report)
    if report["block_hash"] != context.block.hash or report["request"]["amount_in"] != size * 10**18:
        raise ValueError(f"aggregate identity mismatch for {context.block.number} {key}")
    existing = cached or {
        "schemaVersion": 1,
        "block": context.block.number,
        "blockHash": context.block.hash,
        "fingerprint": fingerprint,
        "reports": {},
    }
    existing["reports"][key] = report
    _write_gzip(path, existing)
    return report


def _aggregate_price(report: dict[str, Any]) -> tuple[float | None, str | None]:
    result = report.get("best_split")
    if not result or not result.get("feasible") or result.get("residual_in") != 0:
        return None, "no full-fill aggregate candidate"
    request = report["request"]
    return (
        float(
            Decimal(result["amount_out"])
            / Decimal(10 ** request["decimals_out"])
            / (Decimal(request["amount_in"]) / Decimal(10 ** request["decimals_in"]))
        ),
        None,
    )


def _row(
    context: Any,
    annotation: dict[str, Any],
    build: dict[str, Any],
    market: dict[str, Any],
    reference: dict[str, Any],
    fingerprint: str,
    aggregate_fingerprint: str,
) -> dict[str, Any]:
    pools = _pool_metadata(context.states)
    raw_path = _raw_path(context.block.hash)
    raw = _read_raw(raw_path, context.block.hash, fingerprint)
    if raw is None:
        quotes: dict[str, dict[str, dict[str, Any]]] = {}
        states = {state.record.pool_id: state for state in context.states}
        for pool in pools:
            state = states[pool["id"]]
            quotes[pool["id"]] = {str(size): _quote(state, pool["inputSymbol"], size * 10**18) for size in SIZES}
        raw = {
            "schemaVersion": 1,
            "block": context.block.number,
            "blockHash": context.block.hash,
            "fingerprint": fingerprint,
            "pools": pools,
            "quotes": quotes,
        }
        _write_gzip(raw_path, raw)
    assert raw["block"] == context.block.number
    assert set(raw["quotes"]) == {pool["id"] for pool in pools}
    aggregate_reasons: dict[str, dict[str, str | None]] = {}
    aggregates: dict[str, dict[str, float | None]] = {}
    refunds: dict[str, dict[str, Any]] = {}
    for asset in ASSETS:
        refunds[asset] = {}
        aggregates[asset], aggregate_reasons[asset] = {}, {}
        for size in SIZES:
            report = _aggregate_report(context, annotation, build, asset, size, aggregate_fingerprint)
            price, reason = _aggregate_price(report)
            refunds[asset][str(size)] = report.get("best_split", {}).get("terminal_refund")
            best = _best_direct([
                quote[str(size)]
                for pool, quote in raw["quotes"].items()
                if next(item for item in pools if item["id"] == pool)["inputSymbol"] == asset
            ])
            if price is not None and best and price + 1e-9 < best["price"]:
                raise AssertionError(f"aggregate is below direct subset at {context.block.number} {asset} {size}")
            aggregates[asset][str(size)], aggregate_reasons[asset][str(size)] = price, reason
    return {
        "block": context.block.number,
        "blockHash": context.block.hash,
        "timestamp": datetime.fromtimestamp(context.block.timestamp, UTC).isoformat().replace("+00:00", "Z"),
        "chainlink": market["oraclePrice"],
        "aave": reference["aaveWethPrice"] / reference["aaveUsdcPrice"],
        "aggregates": aggregates,
        "aggregateRefunds": refunds,
        "pools": raw["quotes"],
        "availability": _availability(context, pools),
        "rawIndex": {
            "direct": str(raw_path.relative_to(ROOT)) if raw_path.is_relative_to(ROOT) else str(raw_path),
            "aggregates": str(_aggregate_path(context.block.hash).relative_to(ROOT)) if _aggregate_path(context.block.hash).is_relative_to(ROOT) else str(_aggregate_path(context.block.hash)),
            "aggregateReasons": aggregate_reasons,
        },
    }


def _check(payload: dict[str, Any]) -> None:
    assert len(payload["rows"]) == END - START + 1
    ids = {pool["id"] for pool in payload["pools"]}
    for row in payload["rows"]:
        assert set(row["pools"]) <= ids
        for quotes in row["pools"].values():
            for size, quote in quotes.items():
                assert size in {"1", "10", "100"}
                assert (quote["amountOut"] is None) == (quote["price"] is None)
                if quote["amountOut"] is None:
                    assert quote.get("reason")
                else:
                    assert int(quote["amountOut"]) > 0 and quote["price"] > 0
        for family, info in row["availability"].items():
            assert info["directCount"] == sum(payload["poolById"][pool]["family"] == family for pool in row["pools"])


def main(argv: list[str] | None = None) -> int:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", help="comma-separated subset; never writes canonical final output")
    parser.add_argument(
        "--supplement",
        action="append",
        type=Path,
        default=[],
        help="explicit same-block supplemental pool snapshots (repeatable)",
    )
    parser.add_argument(
        "--supplements",
        type=str,
        default="",
        help="comma-separated list of supplemental pool directories",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=1, help="bounded process concurrency for quotes")
    parser.add_argument("--publish", action="store_true", help="force atomic export to frontend/public when all 254 blocks complete")
    args = parser.parse_args(argv)
    OUT = args.output_dir.resolve()

    _self_check()
    market, refs = _market_rows(), _reference_rows()
    required = set(range(START, END + 1))
    if set(market) != required or set(refs) != required:
        raise ValueError("pinned market/reference headers do not cover the exact 254-block scope")
    wanted = required if args.blocks is None else {int(value) for value in args.blocks.split(",")}
    if not wanted <= required:
        raise ValueError("requested block is outside the exact October scope")

    supplements: list[Path] = []
    for s in args.supplement:
        supplements.append(s.resolve())
    if args.supplements:
        for part in args.supplements.split(","):
            if part.strip():
                supplements.append(Path(part.strip()).resolve())

    # Default to standard expanded supplements if none provided and output dir is expanded
    if not supplements and OUT == DEFAULT_OUT:
        supplements = [ROOT / "outputs/october-connectors", ROOT / "outputs/october-expansion"]

    # Fail-closed full-input validation: all supplements must exist and cover all wanted blocks
    if supplements:
        validate_supplement_coverage(wanted, supplements, market)

    # Concurrency path: if multiple workers and multiple blocks, distribute via worker subprocesses
    if args.workers > 1 and len(wanted) > 1:
        OUT.mkdir(parents=True, exist_ok=True)
        worker_count = min(args.workers, len(wanted))
        sorted_blocks = sorted(wanted)
        chunks: list[list[int]] = [[] for _ in range(worker_count)]
        for idx, block_num in enumerate(sorted_blocks):
            chunks[idx % worker_count].append(block_num)

        print(f"Launching {worker_count} workers across {len(wanted)} blocks...", flush=True)
        procs = []
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            block_str = ",".join(str(b) for b in chunk)
            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--blocks", block_str,
                "--output-dir", str(OUT),
                "--workers", "1",
            ]
        import contextlib
        with contextlib.ExitStack() as stack:
            procs = []
            for i, chunk in enumerate(chunks):
                if not chunk:
                    continue
                block_str = ",".join(str(b) for b in chunk)
                cmd = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--blocks", block_str,
                    "--output-dir", str(OUT),
                    "--workers", "1",
                ]
                for supp in supplements:
                    cmd.extend(["--supplement", str(supp)])
                log_file = OUT / f"worker-{i}.log"
                handle = stack.enter_context(open(log_file, "w"))
                proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
                procs.append((proc, log_file))

            for proc, log_file in procs:
                ret = proc.wait()
                if ret != 0:
                    err_log = log_file.read_text()[-1000:]
                    raise RuntimeError(f"Worker process failed with exit code {ret}:\n{err_log}")
        print("All worker processes completed successfully.", flush=True)

    fingerprint, started, rows, pools = _source_fingerprint(), monotonic(), [], {}
    build = _ensure_perf_native_build()

    for number in sorted(wanted):
        context, annotation, client = load_supplemented_context(number, supplements)
        assert client.network_requests == 0 and context.block.hash == market[number]["blockHash"] == refs[number]["blockHash"]
        aggregate_fingerprint = crash_slices._fingerprint(annotation, "benchmark-scopes-single-thread", build["manifest"])
        row_fingerprint = hashlib.sha256((fingerprint + annotation["collection_evidence_identity"]).encode()).hexdigest()
        row = _row(context, annotation, build, market[number], refs[number], row_fingerprint, aggregate_fingerprint)
        pools.update({pool["id"]: pool for pool in _pool_metadata(context.states)})
        rows.append(row)
        _write_checkpoint(OUT, {
            "completed": len(rows),
            "total": len(wanted),
            "lastBlock": number,
            "lastBlockHash": context.block.hash,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })
        print(json.dumps({"block": number, "completed": len(rows), "total": len(wanted)}), flush=True)

    if args.blocks is not None:
        return 0

    payload = {
        "schemaVersion": 1,
        "scope": {
            "startBlock": START, "endBlock": END, "startUtc": rows[0]["timestamp"],
            "endUtc": rows[-1]["timestamp"], "stride": 1, "sizes": list(SIZES),
        },
        "pools": sorted(pools.values(), key=lambda pool: pool["id"]),
        "families": [{"id": family, "label": family} for family in rows[0]["availability"]],
        "rows": rows,
        "qualification": "direct per-pool model quotes from frozen offline state; not family-optimal routing",
        "fingerprint": fingerprint,
        "runtimeSeconds": monotonic() - started,
    }
    # Internal index supports the direct-count identity check but is not exported.
    payload["poolById"] = pools
    _check(payload)
    payload.pop("poolById")

    OUT.mkdir(parents=True, exist_ok=True)
    temporary = (OUT / "prices.json").with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    temporary.replace(OUT / "prices.json")

    with (OUT / "prices.csv.tmp").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("block", "blockHash", "timestamp", "poolId", "size", "price", "amountOut", "reason"),
        )
        writer.writeheader()
        for row in rows:
            for pool_id, quotes in row["pools"].items():
                for size, quote in quotes.items():
                    writer.writerow({
                        "block": row["block"], "blockHash": row["blockHash"], "timestamp": row["timestamp"],
                        "poolId": pool_id, "size": size, "price": quote["price"],
                        "amountOut": quote["amountOut"], "reason": quote.get("reason"),
                    })
    (OUT / "prices.csv.tmp").replace(OUT / "prices.csv")

    # Atomic publish to frontend/public ONLY when all 254 blocks complete
    if len(rows) == END - START + 1 and (OUT == DEFAULT_OUT or args.publish):
        _publish_frontend_atomically(OUT, FRONTEND_PUBLIC)
        print("Atomically published dataset to frontend/public/october-sources.{json,csv}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
