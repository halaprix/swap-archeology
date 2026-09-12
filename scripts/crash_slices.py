"""Bounded, resumable crash quotes with exact-block Chainlink comparisons.

This is a research collection utility, not a sweep worker.  It uses the
already-collected state universe and makes only pinned Chainlink ``eth_call``s.
``--benchmark-scopes`` is explicitly labelled in the resulting JSON: it is a
single-thread performance scope and does not alter quote budgets or reports.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from swaparch import cli
from swaparch.collection_quotes import prepared_collection_context
from swaparch.explorer import _as_strings, _display_report
from swaparch.rpc.client import RpcClient

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "frontend/public/crash-slices.json"
DEFAULT_RAW = PROJECT / "outputs/crash-slices/raw"
DEFAULT_RPC_CACHE = PROJECT / "outputs/crash-slices/rpc-cache"
ETH_USD = "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"
USDC_USD = "0x8fffffd4afb6115b954bd326cbe7b4ba576818f6"
SUSDE_USD = "0xff3bc18ccbd5999ce63e788a1c250a88626ad099"
LATEST_ROUND_DATA = "0xfeaf968c"
DECIMALS = "0x313ce567"
DESCRIPTION = "0x7284e416"
CASES = (("sUSDe", "USDC", "1000"), ("sUSDe", "USDC", "100000"),
         ("WETH", "USDC", "100"))
SETTINGS = {"grid_parts": 10, "max_steps": 8, "beam_width": 128, "max_expansions": 5000}


def _window_blocks() -> list[dict[str, Any]]:
    """Load the reviewed exact-block sampling plan; never approximate missing pins."""
    return json.loads((PROJECT / "outputs/crash-slices/selected-pins.json").read_text())


def _signed(word: int) -> int:
    return word - (1 << 256) if word >= 1 << 255 else word


def decode_round(raw: str, decimals: int) -> dict[str, int | float]:
    """Decode AggregatorV3 latestRoundData without an ABI dependency."""
    data = bytes.fromhex(raw.removeprefix("0x"))
    if len(data) != 160:
        raise ValueError(f"expected five ABI words, got {len(data)} bytes")
    words = [int.from_bytes(data[i:i + 32], "big") for i in range(0, 160, 32)]
    answer = _signed(words[1])
    if answer <= 0 or words[3] == 0:
        raise ValueError("non-positive answer or missing updatedAt")
    return {"roundId": words[0], "answerRaw": answer, "price": float(Decimal(answer) / 10 ** decimals),
            "startedAt": words[2], "updatedAt": words[3], "answeredInRound": words[4], "decimals": decimals}


def decode_description(raw: str) -> str:
    data = bytes.fromhex(raw.removeprefix("0x"))
    if len(data) < 64 or int.from_bytes(data[:32], "big") != 32:
        raise ValueError("description is not ABI dynamic string")
    length = int.from_bytes(data[32:64], "big")
    if length > len(data) - 64:
        raise ValueError("description length exceeds response")
    return data[64:64 + length].decode("utf-8")


def _feed_for(token_in: str) -> tuple[str, str, str]:
    if token_in == "WETH":
        return ETH_USD, "ETH/USD", "direct Chainlink ETH/USD reference; USDC is compared as its USD unit"
    if token_in == "sUSDe":
        return SUSDE_USD, "sUSDe/USD", "direct Chainlink DEX-state-price Reference feed; never replaced by redemption or Aave valuation"
    raise ValueError(token_in)


def chainlink_quote(client: RpcClient, block: Any, token_in: str) -> dict[str, Any]:
    address, pair, semantics = _feed_for(token_in)
    decimals_call = client.eth_call(address, DECIMALS, block)
    round_call = client.eth_call(address, LATEST_ROUND_DATA, block)
    description_call = client.eth_call(address, DESCRIPTION, block)
    evidence = {"address": address, "pair": pair, "semantics": semantics,
                "directory": "outputs/crash-slices/chainlink-directory-selected.json",
                "raw": {"decimals": decimals_call.raw, "latestRoundData": round_call.raw,
                        "description": description_call.raw}}
    if not decimals_call.success or not round_call.success or not description_call.success:
        return {"oraclePrice": None, "oracleAgeSeconds": None, "oracleStatus": "call_failed", "feed": evidence}
    try:
        decimal_bytes = bytes.fromhex(decimals_call.raw.removeprefix("0x"))
        if len(decimal_bytes) != 32:
            raise ValueError("decimals response is not one ABI word")
        decimals = int.from_bytes(decimal_bytes, "big")
        if decimals > 255:
            raise ValueError("decimals exceeds uint8")
        decoded = decode_round(round_call.raw, decimals)
        description = decode_description(description_call.raw)
        if description != pair.replace("/", " / "):
            raise ValueError(f"unexpected feed description {description!r}")
    except ValueError as exc:
        return {"oraclePrice": None, "oracleAgeSeconds": None, "oracleStatus": "invalid_round",
                "feed": {**evidence, "error": str(exc)}}
    if token_in in ("WETH", "sUSDe"):
        usdc_decimals = client.eth_call(USDC_USD, DECIMALS, block)
        usdc_round = client.eth_call(USDC_USD, LATEST_ROUND_DATA, block)
        usdc_description = client.eth_call(USDC_USD, DESCRIPTION, block)
        evidence["quoteFeed"] = {"address": USDC_USD, "pair": "USDC/USD",
                                  "raw": {"decimals": usdc_decimals.raw, "latestRoundData": usdc_round.raw,
                                          "description": usdc_description.raw}}
        if not usdc_decimals.success or not usdc_round.success or not usdc_description.success:
            return {"oraclePrice": None, "oracleAgeSeconds": None, "oracleStatus": "call_failed", "feed": evidence}
        try:
            usdc_bytes = bytes.fromhex(usdc_decimals.raw.removeprefix("0x"))
            if len(usdc_bytes) != 32 or (usdc_places := int.from_bytes(usdc_bytes, "big")) > 255:
                raise ValueError("USDC/USD decimals response is invalid")
            usdc = decode_round(usdc_round.raw, usdc_places)
            if decode_description(usdc_description.raw) != "USDC / USD":
                raise ValueError("unexpected USDC/USD feed description")
        except ValueError as exc:
            return {"oraclePrice": None, "oracleAgeSeconds": None, "oracleStatus": "invalid_round",
                    "feed": {**evidence, "error": str(exc)}}
        oracle_decimal = (Decimal(decoded["answerRaw"]) / 10 ** decoded["decimals"]) / (
            Decimal(usdc["answerRaw"]) / 10 ** usdc["decimals"])
        decoded["price"] = float(oracle_decimal)
        decoded["quoteUpdatedAt"] = usdc["updatedAt"]
    else:
        oracle_decimal = Decimal(decoded["answerRaw"]) / 10 ** decoded["decimals"]
    updates = [int(decoded["updatedAt"])]
    if "quoteUpdatedAt" in decoded:
        updates.append(int(decoded["quoteUpdatedAt"]))
    age = max(int(block.timestamp) - updated for updated in updates)
    if any(updated > int(block.timestamp) for updated in updates):
        return {"oraclePrice": None, "oracleAgeSeconds": age, "oracleStatus": "invalid_round",
                "feed": {**evidence, "round": decoded}}
    return {"oraclePrice": decoded["price"], "oracleAgeSeconds": age, "oracleStatus": "ok",
            "feed": {**evidence, "round": decoded}, "_decimal": oracle_decimal}


def _price(result: dict[str, Any] | None, amount_in: int, decimals_in: int, decimals_out: int) -> float | None:
    if not result or not result.get("amount_out"):
        return None
    return float((Decimal(result["amount_out"]) / 10 ** decimals_out) /
                 (Decimal(amount_in) / 10 ** decimals_in))


def _json_ints(value: Any) -> Any:
    """Keep raw on-chain integers exact while allowing display floats."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, float)):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return [_json_ints(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ints(item) for key, item in value.items()}
    raise ValueError(f"unsupported JSON value {type(value).__name__}")


def row_from_report(pin: dict[str, Any], context: Any, annotation: dict[str, Any],
                    case: tuple[str, str, str], report: dict[str, Any], exit_code: int,
                    oracle: dict[str, Any], raw_report: str, execution_mode: str) -> dict[str, Any]:
    token_in, token_out, human = case
    request = report["request"]
    aggregate = _price(report.get("best_split"), request["amount_in"],
                       request["decimals_in"], request["decimals_out"])
    baseline = _price(report.get("single_pool_baseline"), request["amount_in"],
                      request["decimals_in"], request["decimals_out"])
    oracle_price = oracle["oraclePrice"]
    deviation = (float((Decimal(str(aggregate)) / oracle["_decimal"] - 1) * 10_000)
                 if aggregate is not None and oracle_price is not None else None)
    coverage = {"selectedFamilies": report.get("selected_families", []), "sources": report.get("sources", []),
                "usablePools": sum(source.get("usable_pools", 0) for source in report.get("sources", [])),
                "unsupported": _as_strings(_display_report(report))["unsupported"]}
    slice_id = pin["window"]
    return {"id": f"{context.block.number}:{token_in}:{token_out}:{request['amount_in']}",
            "sliceId": slice_id, "window": pin["window"], "label": pin["label"], "block": context.block.number,
            "blockHash": context.block.hash,
            "timestamp": datetime.fromtimestamp(context.block.timestamp, UTC).isoformat().replace("+00:00", "Z"),
            "timestampUnix": context.block.timestamp, "tokenIn": token_in, "tokenOut": token_out,
            "amount": human, "amountRaw": str(request["amount_in"]),
            "aggregatedPrice": aggregate, "singlePoolBaselinePrice": baseline,
            "oraclePrice": oracle_price, "deviationBps": deviation,
            "oracleStatus": oracle["oracleStatus"], "oracleAgeSeconds": oracle["oracleAgeSeconds"],
            "feed": _json_ints(oracle["feed"]), "qualificationMode": annotation["qualification_mode"],
            "displayReport": _as_strings(_display_report(report)),
            "sourceCoverage": coverage,
            "report": {"path": raw_report, "exitCode": exit_code, "offline": report.get("offline"),
                       "networkRequests": report.get("network_requests"), "executionMode": execution_mode,
                       "bestSplit": _as_strings(report.get("best_split")),
                       "singlePoolBaseline": _as_strings(report.get("single_pool_baseline")),
                       "solverDiagnostics": _as_strings(report.get("solver_diagnostics"))}}


def _run_quote(context: Any, annotation: dict[str, Any], case: tuple[str, str, str], *,
               allow_psm_dai_refund: bool = False) -> tuple[int, dict[str, Any]]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = cli.quote_command(context.block.number, *case, prepared_context=context, offline=True,
                                 report_annotations=annotation, solver_name="search",
                                 allow_psm_dai_refund=allow_psm_dai_refund, **SETTINGS)
    return code, json.loads(stream.getvalue())


def _existing(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schemaVersion": 1, "qualificationMode": "collection-model-only", "slices": [], "rows": []}
    value = json.loads(path.read_text())
    if value.get("schemaVersion") != 1 or not isinstance(value.get("rows"), list):
        raise ValueError(f"not a crash-slices schema v1 file: {path}")
    return value


def _fingerprint(annotation: dict[str, Any], execution_mode: str, native_build: dict[str, Any] | None) -> str:
    sources = [str(path.relative_to(PROJECT)) for path in sorted((PROJECT / "src/swaparch").rglob("*.py"))]
    sources += ["scripts/crash_slices.py", "scripts/perf_native.py", "scripts/perf_solver.py",
                "scripts/perf_state_memo.py", "scripts/eval_quote_performance.py",
                "outputs/crash-slices/selected-pins.json"]
    payload = {"settings": SETTINGS, "collection": annotation.get("collection_evidence_identity"),
               "executionMode": execution_mode, "feeds": {"ethUsd": ETH_USD, "usdcUsd": USDC_USD,
               "susdeUsd": SUSDE_USD}, "sources": {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
               for name in sources}, "nativeBuild": native_build}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _checkpoint(path: Path, document: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
    document["rows"] = sorted(rows.values(), key=lambda row: row["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--rpc-cache", type=Path, default=DEFAULT_RPC_CACHE)
    parser.add_argument("--benchmark-scopes", action="store_true")
    parser.add_argument("--offline", action="store_true", help="require every Chainlink call in the supplied cache")
    args = parser.parse_args(argv)
    # The scopes are optional because native builds are explicitly benchmark-only.
    loaded = None
    if args.benchmark_scopes:
        import perf_native
        loaded = perf_native.load_build(perf_native.DEFAULT_BUILD)
    document = _existing(args.output)
    rows = {row["id"]: row for row in document["rows"]}
    client = RpcClient(cache_root=args.rpc_cache, offline=args.offline)
    for pin in _window_blocks():
        context, annotation, offline_client = prepared_collection_context(pin["block"])
        assert offline_client.network_requests == 0
        if pin.get("blockHash") and context.block.hash.lower() != pin["blockHash"].lower():
            raise ValueError(f"collection block hash differs from selected pin {pin['block']}")
        if pin.get("timestamp") and context.block.timestamp != pin["timestamp"]:
            raise ValueError(f"collection timestamp differs from selected pin {pin['block']}")
        oracle_cache: dict[str, dict[str, Any]] = {}
        for case in CASES:
            identifier = f"{context.block.number}:{case[0]}:{case[1]}:{int(Decimal(case[2]) * 10 ** (18 if case[0] != 'USDC' else 6))}"
            fingerprint = _fingerprint(annotation, "benchmark-scopes-single-thread" if loaded else "standard",
                                       loaded["manifest"] if loaded else None)
            existing = rows.get(identifier)
            if (existing and existing.get("blockHash") == context.block.hash
                    and existing.get("fingerprint") == fingerprint
                    and (PROJECT / existing.get("report", {}).get("path", "")).is_file()):
                continue
            oracle = oracle_cache.get(case[0])
            if oracle is None:
                oracle = chainlink_quote(client, context.block, case[0])
                oracle_cache[case[0]] = oracle
            manager = perf_native.optimized(context, loaded) if loaded else contextlib.nullcontext()
            with manager:
                code, report = _run_quote(context, annotation, case)
            raw = args.raw_dir / f"{context.block.number}-{case[0]}-{case[1]}-{case[2]}.json"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(json.dumps(report, indent=2) + "\n")
            row = row_from_report(pin, context, annotation, case, report, code, oracle,
                                  str(raw.relative_to(PROJECT)),
                                  "benchmark-scopes-single-thread" if loaded else "standard")
            row["fingerprint"] = fingerprint
            rows[row["id"]] = row
            _checkpoint(args.output, document, rows)
            print(f"block={context.block.number} case={'/'.join(case)}", flush=True)
    slice_rows = _window_blocks()
    document.update({"schemaVersion": 1, "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                     "qualificationMode": "collection-model-only",
                     "executionMode": "benchmark-scopes-single-thread" if loaded else "standard",
                     "oracleOffline": args.offline,
                     "slices": [{"id": window, "label": window, "pins": [pin["block"] for pin in slice_rows if pin["window"] == window]}
                                for window in dict.fromkeys(pin["window"] for pin in slice_rows)],
                     "rows": sorted(rows.values(), key=lambda row: row["id"]),
                     "feeds": [{"pair": "ETH/USD", "address": ETH_USD}, {"pair": "USDC/USD", "address": USDC_USD},
                               {"pair": "sUSDe/USD", "address": SUSDE_USD,
                                "semantics": "Chainlink directory dex_state_price Reference feed"}]})
    _checkpoint(args.output, document, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
