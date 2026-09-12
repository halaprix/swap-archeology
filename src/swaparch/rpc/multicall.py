"""Multicall3 aggregate3 batching with direct-call fallback."""

from __future__ import annotations

from collections.abc import Sequence

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.rpc.client import RpcClient, RpcError

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
AGGREGATE3_SELECTOR = keccak(text="aggregate3((address,bool,bytes)[])")[:4]


def encode_aggregate3(specs: Sequence[CallSpec]) -> str:
    calls = [(spec.to, True, bytes.fromhex(spec.data[2:])) for spec in specs]
    encoded = AGGREGATE3_SELECTOR + encode(["(address,bool,bytes)[]"], [calls])
    return "0x" + encoded.hex()


def decode_aggregate3(raw: str) -> tuple[tuple[bool, str], ...]:
    decoded = decode(["(bool,bytes)[]"], bytes.fromhex(raw[2:]))[0]
    return tuple((bool(success), "0x" + return_data.hex()) for success, return_data in decoded)


class Multicall3:
    def __init__(self, client: RpcClient, chunk_size: int = 200) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.client = client
        self.chunk_size = chunk_size

    def call(self, specs: Sequence[CallSpec], block: BlockRef) -> tuple[CallResult, ...]:
        results: list[CallResult | None] = [None] * len(specs)
        missing: list[tuple[int, CallSpec]] = []
        missing_indexes: dict[tuple[str, str], list[int]] = {}
        for index, spec in enumerate(specs):
            cached = self.client.cached_call(spec, block)
            if cached:
                results[index] = cached
            else:
                key = (spec.to, spec.data)
                indexes = missing_indexes.setdefault(key, [])
                indexes.append(index)
                if len(indexes) == 1:
                    missing.append((index, spec))

        offset = 0
        while offset < len(missing):
            size = min(self.chunk_size, len(missing) - offset)
            while True:
                selected = missing[offset : offset + size]
                try:
                    chunk_results = self._call_chunk([spec for _, spec in selected], block)
                    for (result_index, _), result in zip(selected, chunk_results, strict=True):
                        for index in missing_indexes[(result.spec.to, result.spec.data)]:
                            results[index] = result
                    offset += size
                    break
                except (DecodingError, RpcError, ValueError):
                    if size > 1:
                        size = max(1, size // 2)
                        continue
                    result_index, spec = selected[0]
                    results[result_index] = self.client.eth_call(spec.to, spec.data, block)
                    offset += 1
                    break

        assert all(result is not None for result in results)
        return tuple(result for result in results if result is not None)

    def _call_chunk(self, specs: Sequence[CallSpec], block: BlockRef) -> tuple[CallResult, ...]:
        aggregate = self.client.eth_call(MULTICALL3, encode_aggregate3(specs), block)
        if not aggregate.success:
            raise RpcError("Multicall3 aggregate3 failed")
        decoded = decode_aggregate3(aggregate.raw)
        if len(decoded) != len(specs):
            raise ValueError("Multicall3 returned the wrong result count")

        results: list[CallResult] = []
        for spec, (success, raw) in zip(specs, decoded, strict=True):
            if success:
                result = CallResult(spec, True, raw, "multicall3")
                self.client.store_call_result(result, block)
            else:
                result = self.client.eth_call(spec.to, spec.data, block)
            results.append(result)
        return tuple(results)
