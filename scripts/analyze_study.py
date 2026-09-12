"""Analyze saved quote reports without RPC access or floating point arithmetic."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUOTES = ROOT / "data/results/six-family-full-intermediates"
DEFAULT_ORACLE = ROOT / "data/validation/aave-reference"
DEFAULT_OUTPUT = ROOT / "data/analysis/saved-quotes"
REQUESTED_PINS = (23549991, 23550060, 24356381, 23728292, 25896003)


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _decimal(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = max(64, len(str(abs(value.numerator))) + 32)
        text = format(Decimal(value.numerator) / Decimal(value.denominator), ".24f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _metric(value: Fraction | None) -> dict | None:
    if value is None:
        return None
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "decimal": _decimal(value),
    }


def _scaled_amount(raw: int | None, decimals: int | None) -> str | None:
    if raw is None or decimals is None:
        return None
    return _decimal(Fraction(raw, 10 ** decimals))


def _read_json(path: Path) -> object:
    """Read a plain or gzip-compressed JSON artifact."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text())


def load_oracle_reference(path: Path, *, chain: int, block: int, block_hash: str) -> dict:
    """Load one Aave reference and reject a block identity mismatch."""
    value = _read_json(path)
    identity = value.get("block")
    if not isinstance(identity, dict):
        raise TypeError(f"oracle reference {path} has no block identity")
    actual_hash = str(identity.get("hash", "")).lower()
    if (_integer(identity.get("chain"), "oracle chain") != chain
            or _integer(identity.get("number"), "oracle block number") != block
            or actual_hash != block_hash.lower()):
        raise ValueError(f"oracle reference {path} has a block hash or number mismatch")
    if not isinstance(value.get("prices"), list):
        raise TypeError(f"oracle reference {path} has no prices list")
    return value


def _validate_quote(value: object, path: Path) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("request"), dict):
        raise TypeError(f"quote {path} is missing its request object")
    request = value["request"]
    for key in ("token_in", "token_out", "symbol_in", "symbol_out", "decimals_in", "decimals_out"):
        if key not in request:
            raise ValueError(f"quote {path} request is missing {key}")
    for key in ("token_in", "token_out", "symbol_in", "symbol_out"):
        if not isinstance(request[key], str):
            raise TypeError(f"quote {path} request.{key} must be a string")
    if not isinstance(value.get("requested_solver"), str) or not isinstance(value.get("selected_families"), list):
        raise TypeError(f"quote {path} is missing requested_solver or selected_families")
    amount_in = _integer(request.get("amount_in"), f"quote {path} request.amount_in", positive=True)
    decimals_in = _integer(request["decimals_in"], f"quote {path} request.decimals_in")
    decimals_out = _integer(request["decimals_out"], f"quote {path} request.decimals_out")
    if request["token_in"].lower() == request["token_out"].lower():
        raise ValueError(f"quote {path} has identical input and output tokens")
    chain = _integer(value.get("chain"), f"quote {path} chain")
    block = _integer(value.get("block"), f"quote {path} block")
    block_hash = str(value.get("block_hash", "")).lower()
    if not block_hash:
        raise ValueError(f"quote {path} is missing block_hash")
    for field in ("single_pool_baseline", "best_split"):
        candidate = value.get(field)
        if candidate is None:
            continue
        if not isinstance(candidate, dict):
            raise TypeError(f"quote {path} {field} must be an object or null")
        if "amount_out" not in candidate:
            raise ValueError(f"quote {path} {field} is missing amount_out")
        _integer(candidate["amount_out"], f"quote {path} {field}.amount_out")
        if candidate.get("feasible") is not True:
            raise ValueError(f"quote {path} {field} is not explicitly feasible")
        if _integer(candidate.get("residual_in"), f"quote {path} {field}.residual_in") != 0:
            raise ValueError(f"quote {path} {field} has residual input")
        spent = candidate.get("amount_in_spent")
        if _integer(spent, f"quote {path} {field}.amount_in_spent") != amount_in:
            raise ValueError(f"quote {path} {field}.amount_in_spent does not match request.amount_in")
    return {
        "chain": chain,
        "block": block,
        "block_hash": block_hash,
        "request": request,
        "decimals_in": decimals_in,
        "decimals_out": decimals_out,
        "amount_in": amount_in,
    }


def _oracle_for(quote: dict, oracle_dir: Path | None) -> dict | None:
    if oracle_dir is None:
        return None
    path = oracle_dir / f"{quote['block_hash']}.json"
    if not path.is_file():
        return None
    return load_oracle_reference(path, chain=quote["chain"], block=quote["block"],
                                 block_hash=quote["block_hash"])


def _oracle_price(quote: dict, reference: dict | None) -> Fraction | None:
    if reference is None:
        return None
    request = quote["request"]
    prices = {}
    for row in reference["prices"]:
        token = row.get("token", {})
        address = str(token.get("address", "")).lower()
        if address in {str(request["token_in"]).lower(), str(request["token_out"]).lower()}:
            expected_decimals = (request["decimals_in"] if address == str(request["token_in"]).lower()
                                 else request["decimals_out"])
            if ("chain" in token and (type(token["chain"]) is not int
                                       or token["chain"] != quote["chain"])
                    or ("decimals" in token and (type(token["decimals"]) is not int
                                                  or token["decimals"] != expected_decimals))):
                raise ValueError("oracle token metadata does not match quote request")
        if (address and row.get("available") is True and type(row.get("price_base")) is int
                and row["price_base"] > 0):
            prices[address] = row["price_base"]
    price_in = prices.get(str(request["token_in"]).lower())
    price_out = prices.get(str(request["token_out"]).lower())
    if price_in is None or price_out is None:
        return None
    return Fraction(price_in, price_out)


def _families(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted(str(item) for item in value))


def _solver_limits(value: dict) -> str:
    return json.dumps(value.get("search_limits"),
                      sort_keys=True, separators=(",", ":"))


def _spot_for(value: dict, identity: dict, cache: dict[tuple[int, str], dict]) -> dict:
    """Load only cached V2/V3 states and calculate direct-pool marginal spots."""
    key = (identity["chain"], identity["block_hash"])
    if key not in cache:
        try:
            from swaparch.snapshot.store import SnapshotStore
            from swaparch.universe import (
                implemented_adapters,
                load_inventory,
                load_states,
                pools_live_at,
            )

            snapshot = SnapshotStore().load(*key)
            if snapshot.block.number != identity["block"] or snapshot.block.hash != identity["block_hash"]:
                raise ValueError("cached snapshot block identity does not match quote")
            adapters = implemented_adapters()
            records = []
            for family in ("uniswap_v2", "uniswap_v3"):
                records.extend(pools_live_at(load_inventory(family, identity["chain"]), identity["block"]))
            states, unsupported = load_states(adapters, records, snapshot)
            cache[key] = {"states": states, "unsupported": len(unsupported)}
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError) as error:
            cache[key] = {"error": str(error)}
    loaded = cache[key]
    if "error" in loaded:
        return {"status": "unavailable", "reason": f"cached state unavailable: {loaded['error']}"}

    expected = {}
    for source in value.get("sources", []):
        if source.get("family") in {"uniswap_v2", "uniswap_v3"}:
            expected[source["family"]] = source.get("usable_pools")
    actual = {family: sum(state.record.family == family for state in loaded["states"])
              for family in ("uniswap_v2", "uniswap_v3")}
    if any(type(expected.get(family)) is not int or expected[family] != actual[family]
           for family in actual):
        return {"status": "unavailable",
                "reason": f"cached eligible state counts {actual} differ from report usable counts {expected}"}

    request = identity["request"]
    in_address = str(request["token_in"]).lower()
    out_address = str(request["token_out"]).lower()
    in_decimals, out_decimals = identity["decimals_in"], identity["decimals_out"]
    family_rows = {"uniswap_v2": [], "uniswap_v3": []}
    for state in loaded["states"]:
        tokens = state.tokens()
        if len(tokens) != 2 or {tokens[0].address, tokens[1].address} != {in_address, out_address}:
            continue
        if any(token.decimals != (in_decimals if token.address == in_address else out_decimals)
               for token in tokens):
            continue
        if state.record.family == "uniswap_v2":
            if state.reserve0 <= 0 or state.reserve1 <= 0:
                continue
            raw = (Fraction(state.reserve1, state.reserve0)
                   if in_address == state.token0.address else Fraction(state.reserve0, state.reserve1))
        elif state.record.family == "uniswap_v3":
            if state.liquidity <= 0 or state.sqrt_price_x96 <= 0:
                continue
            raw = (Fraction(state.sqrt_price_x96 ** 2, 2 ** 192)
                   if in_address == state.token0.address
                   else Fraction(2 ** 192, state.sqrt_price_x96 ** 2))
        else:
            continue
        spot = raw * Fraction(10 ** in_decimals, 10 ** out_decimals)
        family_rows[state.record.family].append({
            "pool_id": state.record.pool_id,
            "spot": _metric(spot),
        })
    spots = [Fraction(row["spot"]["numerator"], row["spot"]["denominator"])
             for rows in family_rows.values() for row in rows]
    if not spots:
        return {"status": "no_direct_pool", "reason": "no positive cached direct V2/V3 pool state"}
    return {
        "status": "available",
        "reason": "direct V2/V3 pool marginal spot only; no fees and no finite-size quote",
        "uniswap_v2": {"pools": family_rows["uniswap_v2"]},
        "uniswap_v3": {"pools": family_rows["uniswap_v3"]},
        "spread_bps": _metric(10000 * (max(spots) / min(spots) - 1)),
        "finite_size_quote": False,
    }


def _row(path: Path, value: dict, oracle_dir: Path | None,
         spot_cache: dict[tuple[int, str], dict]) -> dict:
    identity = _validate_quote(value, path)
    route = value.get("best_split")
    baseline = value.get("single_pool_baseline")
    route_output = None if route is None else route["amount_out"]
    baseline_output = None if baseline is None else baseline["amount_out"]
    route_status = "quoted" if route is not None else "no_tested_route"
    execution = None
    gain = None
    oracle = None
    discount = None
    if route is not None:
        execution = Fraction(route_output * 10 ** identity["decimals_in"],
                             identity["amount_in"] * 10 ** identity["decimals_out"])
        if baseline_output is not None and baseline_output > 0:
            gain = Fraction(10000 * (route_output - baseline_output), baseline_output)
        oracle = _oracle_price(identity, _oracle_for(identity, oracle_dir))
        if oracle is not None and oracle > 0:
            discount = 10000 * (1 - execution / oracle)
    analysis = {
        "route_status": route_status,
        "amount_in_raw": identity["amount_in"],
        "amount_in_human": _scaled_amount(identity["amount_in"], identity["decimals_in"]),
        "best_split_amount_out_raw": route_output,
        "best_split_amount_out_human": _scaled_amount(route_output, identity["decimals_out"]),
        "single_pool_baseline_amount_out_raw": baseline_output,
        "single_pool_baseline_amount_out_human": _scaled_amount(baseline_output, identity["decimals_out"]),
        "execution_price": _metric(execution),
        "routing_gain_bps": _metric(gain),
        "oracle_price": _metric(oracle),
        "oracle_discount_bps": _metric(discount),
        "size_reference_amount_in_raw": None,
        "size_reference_amount_in_human": None,
        "size_deterioration_bps": None,
        "solver_limits": _solver_limits(value),
        "spot_metrics": _spot_for(value, identity, spot_cache),
    }
    result = dict(value)
    result["quote_path"] = path.name
    result["analysis"] = analysis
    return result


def analyze_directory(quote_dir: Path | str = DEFAULT_QUOTES,
                      oracle_dir: Path | str | None = DEFAULT_ORACLE) -> dict:
    """Read actual quote reports, skipping summary/manifest JSON objects."""
    quote_dir = Path(quote_dir)
    oracle_path = None if oracle_dir is None else Path(oracle_dir)
    spot_cache: dict[tuple[int, str], dict] = {}
    rows = []
    decimals_by_token: dict[tuple[int, str], int] = {}
    paths = [*quote_dir.glob("*.json"), *quote_dir.glob("*.json.gz")]
    for path in sorted(paths):
        value = _read_json(path)
        if path.name.removesuffix(".gz") in {"summary.json", "manifest.json"}:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("request"), dict):
            raise TypeError(f"malformed quote report {path}: missing request object")
        if "best_split" not in value or "single_pool_baseline" not in value:
            raise TypeError(f"malformed quote report {path}: missing route result fields")
        checked = _validate_quote(value, path)
        request = checked["request"]
        for address, decimals in ((request["token_in"], checked["decimals_in"]),
                                  (request["token_out"], checked["decimals_out"])):
            key = (checked["chain"], str(address).lower())
            if key in decimals_by_token and decimals_by_token[key] != decimals:
                raise ValueError(f"token {address} has inconsistent decimals across quote reports")
            decimals_by_token[key] = decimals
        rows.append(_row(path, value, oracle_path, spot_cache))

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        request = row["request"]
        key = (
            row["chain"], row["block_hash"], str(request["token_in"]).lower(),
            str(request["token_out"]).lower(), row.get("requested_solver", ""),
            _families(row.get("selected_families")), row["analysis"]["solver_limits"],
        )
        groups[key].append(row)
    for members in groups.values():
        reference = min(members, key=lambda item: item["analysis"]["amount_in_raw"])
        small = reference["analysis"]["execution_price"]
        reference_amount = reference["analysis"]["amount_in_raw"]
        for row in members:
            analysis = row["analysis"]
            analysis["size_reference_amount_in_raw"] = reference_amount
            analysis["size_reference_amount_in_human"] = reference["analysis"]["amount_in_human"]
            current = analysis["execution_price"]
            if small is not None and small["numerator"] != 0 and current is not None:
                current_price = Fraction(current["numerator"], current["denominator"])
                small_price = Fraction(small["numerator"], small["denominator"])
                analysis["size_deterioration_bps"] = _metric(10000 * (1 - current_price / small_price))
    blocks = sorted({row["block"] for row in rows})
    scenarios = defaultdict(lambda: {"blocks": set(), "sizes": set(), "rows": 0})
    for row in rows:
        request = row["request"]
        label = (request["symbol_in"], request["symbol_out"], row.get("requested_solver", ""),
                 _families(row.get("selected_families")))
        scenarios[label]["blocks"].add(row["block"])
        scenarios[label]["sizes"].add(row["analysis"]["amount_in_raw"])
        scenarios[label]["rows"] += 1
    scenario_rows = [
        {"token_in": label[0], "token_out": label[1], "requested_solver": label[2],
         "selected_families": list(label[3]), "blocks": sorted(item["blocks"]),
         "amounts_in_raw": sorted(item["sizes"]), "rows": item["rows"]}
        for label, item in sorted(scenarios.items(), key=lambda pair: pair[0])
    ]
    return {
        "metadata": {
            "scope": "bounded offline analysis of saved swaparch quote reports",
            "quote_directory": str(quote_dir),
            "oracle_directory": None if oracle_path is None else str(oracle_path),
            "requested_pins": list(REQUESTED_PINS),
            "pins_present": blocks,
            "missing_requested_pins": [pin for pin in REQUESTED_PINS if pin not in blocks],
            "full_window_claim": False,
            "net_after_gas": "unknown; gas is not fabricated from saved quotes",
            "peg_assumption": "none; oracle ratios use positive available same-block Aave prices",
            "spot_metrics": {"status": "cached_direct_v2_v3",
                             "reason": "only positive cached V2/V3 direct-pool marginal spots are eligible"},
            "scenario_count": len(scenario_rows),
            "scenarios_present": scenario_rows,
        },
        "rows": rows,
    }


CSV_COLUMNS = (
    "quote_path", "chain", "block", "block_hash", "token_in", "symbol_in", "decimals_in",
    "token_out", "symbol_out", "decimals_out", "amount_in_raw", "amount_in_human",
    "requested_solver", "selected_families", "solver_limits", "route_status", "best_split_amount_out_raw",
    "best_split_amount_out_human", "single_pool_baseline_amount_out_raw",
    "single_pool_baseline_amount_out_human", "execution_price_numerator",
    "execution_price_denominator", "execution_price_decimal", "routing_gain_bps_numerator",
    "routing_gain_bps_denominator", "routing_gain_bps_decimal", "oracle_price_numerator",
    "oracle_price_denominator", "oracle_price_decimal", "oracle_discount_bps_numerator",
    "oracle_discount_bps_denominator", "oracle_discount_bps_decimal", "size_reference_amount_in_raw",
    "size_reference_amount_in_human", "size_deterioration_bps_numerator",
    "size_deterioration_bps_denominator", "size_deterioration_bps_decimal", "spot_status",
    "spot_spread_bps_numerator", "spot_spread_bps_denominator", "spot_spread_bps_decimal",
)


def _csv_row(row: dict) -> dict:
    request = row["request"]
    analysis = row["analysis"]
    output = {column: "" for column in CSV_COLUMNS}
    output.update({
        "quote_path": row["quote_path"], "chain": row["chain"], "block": row["block"],
        "block_hash": row["block_hash"], "token_in": request["token_in"],
        "symbol_in": request["symbol_in"], "decimals_in": request["decimals_in"],
        "token_out": request["token_out"], "symbol_out": request["symbol_out"],
        "decimals_out": request["decimals_out"], "requested_solver": row.get("requested_solver", ""),
        "selected_families": "|".join(_families(row.get("selected_families"))),
        "solver_limits": analysis["solver_limits"],
    })
    for key, value in analysis.items():
        if key in output:
            output[key] = value
    for metric_name in ("execution_price", "routing_gain_bps", "oracle_price", "oracle_discount_bps",
                        "size_deterioration_bps"):
        metric = analysis[metric_name]
        if metric is not None:
            output[f"{metric_name}_numerator"] = metric["numerator"]
            output[f"{metric_name}_denominator"] = metric["denominator"]
            output[f"{metric_name}_decimal"] = metric["decimal"]
    spot = analysis["spot_metrics"]
    output["spot_status"] = spot["status"]
    if spot.get("spread_bps") is not None:
        output["spot_spread_bps_numerator"] = spot["spread_bps"]["numerator"]
        output["spot_spread_bps_denominator"] = spot["spread_bps"]["denominator"]
        output["spot_spread_bps_decimal"] = spot["spread_bps"]["decimal"]
    return output


def _weth_conclusion_rows(rows: list[dict]) -> list[dict]:
    candidates = [row for row in rows
                  if row["request"].get("symbol_in") == "WETH"
                  and row["request"].get("symbol_out") == "USDC"
                  and row["analysis"]["route_status"] == "quoted"]
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in candidates:
        grouped[(row["block"], row["analysis"]["amount_in_raw"])].append(row)
    selected = []
    for key in sorted(grouped):
        group = grouped[key]
        selected.append(min(group, key=lambda row: (
            0 if row.get("requested_solver") == "search" else 1,
            row.get("requested_solver", ""), row["analysis"]["solver_limits"],
            row["quote_path"],
        )))
    return selected


def _markdown(result: dict) -> str:
    metadata = result["metadata"]
    rows = result["rows"]
    quoted = sum(row["analysis"]["route_status"] == "quoted" for row in rows)
    search = [row for row in rows if row.get("requested_solver") == "search"]
    truncated = sum(row.get("solver_diagnostics", {}).get("truncated") is True for row in search)
    dual_status = Counter(row.get("solver_diagnostics", {}).get("status", "unknown")
                          for row in rows if row.get("requested_solver") == "dual")
    lines = [
        "# Saved quote analysis",
        "",
        "This is a bounded offline analysis of the saved swaparch quote JSON reports.",
        "It does not make a full historical-window or market-wide claim.",
        "",
        f"- Requested pins: {', '.join(map(str, metadata['requested_pins']))}.",
        (f"- Pins actually present: {', '.join(map(str, metadata['pins_present'])) or 'none'} "
         f"({len(metadata['pins_present'])}/{len(metadata['requested_pins'])}); missing: "
         f"{', '.join(map(str, metadata['missing_requested_pins'])) or 'none'}."),
        f"- Reports read: {len(rows)}; feasible saved routes: {quoted}; no tested route: {len(rows) - quoted}.",
        f"- Search budget exhausted: {truncated}/{len(search)} search reports; these are best tested routes.",
        f"- Recorded dual statuses: {dict(sorted(dual_status.items()))}; numerical estimates are not certified bounds.",
        f"- Dataset directory: `{metadata['quote_directory']}`. Preserve its recorded solver revision when comparing results.",
        "- Net-after-gas is unknown; no gas-adjusted result is fabricated.",
        "- No peg is assumed; oracle ratios require positive available prices from the exact same block hash.",
        "- Pretrade V2/V3 spread uses positive cached direct-pool marginal spots only; it has no fees and is not a finite-size quote.",
        "",
        "## Source coverage recorded in reports",
        "",
        "Counts below are ranges across reports; discovered identities do not establish pricing support.",
        "",
        "| Family | Reports selected | Usable pools (min–max) | Recorded statuses |",
        "|---|---:|---:|---|",
    ]
    families = sorted({source["family"] for row in rows for source in row.get("sources", [])})
    for family in families:
        sources = [source for row in rows for source in row.get("sources", [])
                   if source["family"] == family]
        usable = [source.get("usable_pools", 0) for source in sources]
        lines.append(f"| {family} | {sum(source.get('selected') is True for source in sources)} "
                     f"| {min(usable)}–{max(usable)} "
                     f"| {', '.join(sorted({source.get('status', 'unknown') for source in sources}))} |")
    provenance = Path(metadata["quote_directory"]) / "README.md"
    if provenance.is_file():
        lines += ["", "## Dataset provenance", "", provenance.read_text().strip()]
    lines += [
        "",
        "## Scenario labels present",
        "",
        "| Input | Output | Solver | Selected families | Blocks | Sizes (raw) | Reports |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for scenario in metadata["scenarios_present"]:
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            scenario["token_in"], scenario["token_out"], scenario["requested_solver"],
            ", ".join(scenario["selected_families"]),
            ", ".join(map(str, scenario["blocks"])),
            ", ".join(map(str, scenario["amounts_in_raw"])), scenario["rows"],
        ))
    lines += [
        "",
        "## WETH to USDC observed conclusion table",
        "",
        "The table selects the recorded `search` row for each pin and size when present; otherwise it labels the deterministic fallback solver.",
        "",
        "| Pin | Size (WETH) | Solver | Baseline USDC | Best USDC | Gain (bps) | Oracle gap (bps) | Size deterioration (bps) |",
        "|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in _weth_conclusion_rows(rows):
        analysis = row["analysis"]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            row["block"], analysis["amount_in_human"], row.get("requested_solver", ""),
            analysis["single_pool_baseline_amount_out_human"] or "null",
            analysis["best_split_amount_out_human"] or "null",
            (analysis["routing_gain_bps"] or {}).get("decimal", "null"),
            (analysis["oracle_discount_bps"] or {}).get("decimal", "null"),
            (analysis["size_deterioration_bps"] or {}).get("decimal", "null"),
        ))
    lines += [
        "",
        ("Execution price is `output/token input` after decimal scaling. Routing gain compares "
         "`best_split` with `single_pool_baseline`; size deterioration compares each observed "
         "price with the smallest observed input in the same chain/hash/pair/solver/source-set "
         "group, so it includes fees and route changes rather than isolating price impact."),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(result: dict, output_dir: Path | str = DEFAULT_OUTPUT) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    with (output_dir / "analysis.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(_csv_row(row) for row in result["rows"])
    (output_dir / "report.md").write_text(_markdown(result))
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quotes", type=Path, default=DEFAULT_QUOTES)
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = analyze_directory(args.quotes, args.oracle)
    write_outputs(result, args.output)
    print(f"wrote {len(result['rows'])} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
