"""Compare sequential, concurrent, and JSON-RPC-batched Multicall3 reads.

This is deliberately an acquisition benchmark, not a cache benchmark.  It does
not write RpcClient's call cache and never records the configured RPC URL.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests

from swaparch.core.types import BlockRef, CallSpec
from swaparch.rpc.client import RpcClient, RpcError, RpcResponseError
from swaparch.rpc.headers import hashes_match
from swaparch.rpc.multicall import MULTICALL3, decode_aggregate3, encode_aggregate3


class BenchmarkError(RuntimeError):
    pass


def _blocks(value: str) -> tuple[int, ...]:
    try:
        blocks = tuple(int(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--blocks must be comma-separated integers") from error
    if not blocks or any(block < 0 for block in blocks) or len(set(blocks)) != len(blocks):
        raise argparse.ArgumentTypeError("--blocks must be unique non-negative integers")
    return blocks


def _report(path: Path, max_specs: int) -> tuple[list[CallSpec], dict[str, Any]]:
    if path.suffix != ".gz":
        raise BenchmarkError("--seed-report must be a .json.gz collection report")
    with gzip.open(path, "rt") as handle:
        report = json.load(handle)
    rows = report.get("read_specs")
    if not isinstance(rows, list) or not rows:
        raise BenchmarkError("seed report has no read_specs")
    if len(rows) > max_specs:
        raise BenchmarkError(
            f"seed report has {len(rows)} read_specs, above --max-specs {max_specs}; "
            "refuse to truncate the compared plan"
        )
    specs = [CallSpec(**row) for row in rows]
    return specs, {"path": str(path), "available_specs": len(rows), "selected_specs": len(specs)}


def _fresh_pins(client: RpcClient, numbers: tuple[int, ...]) -> tuple[BlockRef, ...]:
    """Use cached lookup first, then verify every pinned identity with a fresh header."""
    chain = client.chain_id()
    pins = []
    for number in numbers:
        cached = client.get_block(number)
        fresh = client._fetch_numbered_block(chain, number, use_cache=False)
        if not hashes_match(cached, fresh.hash):
            raise BenchmarkError(
                f"block {number} hash changed: cached {cached.hash}, fresh {fresh.hash}"
            )
        pins.append(fresh)
    return tuple(pins)


def _params(spec: CallSpec, block: BlockRef) -> list[Any]:
    return [{"to": spec.to, "data": spec.data}, {"blockHash": block.hash}]


def _direct_call(client: RpcClient, spec: CallSpec, block: BlockRef) -> dict[str, Any]:
    params = _params(spec, block)
    request = {"method": "eth_call", "params": params}
    try:
        result = client._rpc("eth_call", params)
        if not isinstance(result, str) or not result.startswith("0x"):
            raise BenchmarkError("eth_call returned non-hex result")
        return {"request": request, "response": {"result": result.lower()}}
    except RpcResponseError as error:
        return {"request": request, "response": {"error": {"code": error.code, "message": error.message}}}


def _aggregate_spec(specs: list[CallSpec]) -> CallSpec:
    return CallSpec(MULTICALL3, encode_aggregate3(specs), "multicall3.aggregate3")


def _mode_payload(rows: list[dict[str, Any]], http_requests: int, elapsed: float,
                  subcalls: int, workers: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "elapsed_seconds": elapsed,
        "http_requests": http_requests,
        "method_counts": {"eth_call": len(rows), "multicall3_aggregate3": len(rows)},
        "logical_subcalls": subcalls,
        "requests": [row["request"] for row in rows],
        "responses": [row["response"] for row in rows],
    }
    if workers is not None:
        payload["workers"] = workers
    return payload


def _sequential(client: RpcClient, specs: list[CallSpec], blocks: tuple[BlockRef, ...]) -> dict[str, Any]:
    before = client.network_requests
    started = time.monotonic()
    aggregate = _aggregate_spec(specs)
    rows = [_direct_call(client, aggregate, block) for block in blocks]
    return _mode_payload(rows, client.network_requests - before, time.monotonic() - started,
                         len(blocks) * len(specs))


def _parallel_one(spec: CallSpec, block: BlockRef) -> tuple[dict[str, Any], int]:
    client = RpcClient()
    row = _direct_call(client, spec, block)
    return row, client.network_requests


def _parallel(specs: list[CallSpec], blocks: tuple[BlockRef, ...], workers: int) -> dict[str, Any]:
    aggregate = _aggregate_spec(specs)
    jobs = [(aggregate, block) for block in blocks]
    started = time.monotonic()
    rows: list[dict[str, Any] | None] = [None] * len(jobs)
    requests_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_parallel_one, spec, block): index
                   for index, (spec, block) in enumerate(jobs)}
        for future in as_completed(futures):
            index = futures[future]
            row, count = future.result()
            rows[index] = row
            requests_count += count
    return _mode_payload([row for row in rows if row is not None], requests_count,
                         time.monotonic() - started, len(blocks) * len(specs), workers)


def _match_batch(payload: list[dict[str, Any]], body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, list):
        raise BenchmarkError("JSON-RPC batch response was not a list")
    expected = {request["id"] for request in payload}
    found: dict[int, dict[str, Any]] = {}
    for response in body:
        if not isinstance(response, dict) or not isinstance(response.get("id"), int):
            raise BenchmarkError("JSON-RPC batch response has an invalid id")
        request_id = response["id"]
        if request_id in found:
            raise BenchmarkError(f"JSON-RPC batch response duplicated id {request_id}")
        if request_id not in expected:
            raise BenchmarkError(f"JSON-RPC batch response has unexpected id {request_id}")
        if "error" in response:
            raise BenchmarkError(f"JSON-RPC batch response has an error for id {request_id}")
        if "result" not in response:
            raise BenchmarkError(f"JSON-RPC batch response omitted result for id {request_id}")
        found[request_id] = response
    missing = expected - found.keys()
    if missing:
        raise BenchmarkError(f"JSON-RPC batch response omitted ids {sorted(missing)}")
    return [found[request["id"]] for request in payload]


def _batch(client: RpcClient, specs: list[CallSpec], blocks: tuple[BlockRef, ...], attempts: int) -> dict[str, Any]:
    data = encode_aggregate3(specs)
    payload = [
        {"jsonrpc": "2.0", "id": index + 1, "method": "eth_call",
         "params": [{"to": MULTICALL3, "data": data}, {"blockHash": block.hash}]}
        for index, block in enumerate(blocks)
    ]
    started = time.monotonic()
    request_count = 0
    last_reason = "unknown failure"
    for attempt in range(attempts):
        try:
            request_count += 1
            response = client.session.post(client._url, json=payload, timeout=60)
            if response.status_code == 429 or response.status_code >= 500:
                last_reason = f"HTTP {response.status_code}"
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise BenchmarkError(f"batch failed after {attempts} attempts: {last_reason}")
            if response.status_code >= 400:
                raise BenchmarkError(f"batch failed with HTTP {response.status_code}")
            raw = response.json()
            ordered = _match_batch(payload, raw)
            return {
                "elapsed_seconds": time.monotonic() - started,
                "http_requests": request_count,
                "method_counts": {"eth_call": len(payload), "multicall3_aggregate3": len(payload)},
                "logical_subcalls": len(payload) * len(specs),
                "requests": payload,
                "responses": ordered,
            }
        except (requests.Timeout, requests.ConnectionError) as error:
            last_reason = type(error).__name__
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            raise BenchmarkError(f"batch failed after {attempts} attempts: {last_reason}") from None
        except requests.RequestException as error:
            raise BenchmarkError(f"batch request failed: {type(error).__name__}") from None
        except ValueError:
            raise BenchmarkError("batch returned invalid JSON") from None
    raise BenchmarkError(f"batch failed after {attempts} attempts: {last_reason}")


def _aggregate_bytes(mode: dict[str, Any], blocks: tuple[BlockRef, ...], specs: list[CallSpec]) -> dict[str, str]:
    responses = mode["responses"]
    if len(responses) != len(blocks):
        raise AssertionError("benchmark returned the wrong number of aggregate calls")
    output: dict[str, str] = {}
    for block, response in zip(blocks, responses, strict=True):
        if "error" in response:
            raise AssertionError("aggregate eth_call returned an error; no byte-parity claim is valid")
        result = response.get("result")
        if not isinstance(result, str):
            raise TypeError("aggregate eth_call returned non-hex data")
        decoded = decode_aggregate3(result)
        assert len(decoded) == len(specs), "aggregate3 returned the wrong subcall count"
        assert all(success for success, _ in decoded), "aggregate3 reported a failed subcall"
        output[block.hash] = result.lower()
    return output


def _assert_parity(sequential: dict[str, Any], parallel: dict[str, Any], batch: dict[str, Any],
                   blocks: tuple[BlockRef, ...], specs: list[CallSpec]) -> None:
    expected = _aggregate_bytes(sequential, blocks, specs)
    assert _aggregate_bytes(parallel, blocks, specs) == expected, "parallel aggregate bytes differ"
    assert _aggregate_bytes(batch, blocks, specs) == expected, "JSON-RPC batch aggregate bytes differ"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix != ".gz":
        raise BenchmarkError("--output must end in .gz")
    with gzip.open(path, "wt") as handle:
        json.dump(payload, handle, separators=(",", ":"))


def _self_check() -> None:
    payload = [{"id": 2}, {"id": 1}]
    assert [row["id"] for row in _match_batch(payload, [{"id": 1, "result": "0x"}, {"id": 2, "result": "0x"}])] == [2, 1]
    try:
        _match_batch(payload, [{"id": 1, "result": "0x"}, {"id": 1, "result": "0x"}])
    except BenchmarkError:
        return
    raise AssertionError("duplicate batch IDs were accepted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-report", type=Path)
    parser.add_argument("--blocks", type=_blocks)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-blocks", type=int, default=4)
    parser.add_argument("--max-specs", type=int, default=4_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        _self_check()
        return 0
    if not args.seed_report or not args.blocks or not args.output:
        parser.error("--seed-report, --blocks, and --output are required")
    if not 1 <= args.max_blocks <= 16 or len(args.blocks) > args.max_blocks:
        parser.error("--blocks exceeds bounded --max-blocks (default 4)")
    if not 1 <= args.max_specs <= 4_000 or not 1 <= args.workers <= 32 or not 1 <= args.attempts <= 6:
        parser.error("max-specs (1..4000), workers (1..32), and attempts (1..6) are bounded")

    specs, seed = _report(args.seed_report, args.max_specs)
    client = RpcClient()
    pins = _fresh_pins(client, args.blocks)
    result: dict[str, Any] = {
        "kind": "rpc_batch_benchmark", "seed_report": seed,
        "blocks": [asdict(block) for block in pins], "block_parameter": "blockHash",
        "modes": {}, "performance_claim_valid": False,
    }
    try:
        result["modes"]["sequential"] = _sequential(client, specs, pins)
        result["modes"]["parallel"] = _parallel(specs, pins, args.workers)
        result["modes"]["json_rpc_batch_multicall3"] = _batch(client, specs, pins, args.attempts)
        _assert_parity(result["modes"]["sequential"], result["modes"]["parallel"],
                       result["modes"]["json_rpc_batch_multicall3"], pins, specs)
        result["performance_claim_valid"] = True
    except (AssertionError, BenchmarkError, RpcError, TypeError) as error:
        result["error"] = str(error)
    _write(args.output, result)
    return 0 if result["performance_claim_valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
