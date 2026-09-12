"""Block-hash-pinned snapshot persistence."""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.rpc.client import PROJECT_ROOT, RpcClient
from swaparch.rpc.multicall import Multicall3


@dataclass(frozen=True)
class StoredSnapshot:
    block: BlockRef
    calls: tuple[CallResult, ...]

    _calls_by_key: Mapping[tuple[str, str], CallResult] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        indexed: dict[tuple[str, str], CallResult] = {}
        for result in self.calls:
            # Preserve the existing first-result semantics for duplicate identities.
            indexed.setdefault((result.spec.to, result.spec.data), result)
        object.__setattr__(self, "_calls_by_key", MappingProxyType(indexed))

    def get(self, spec: CallSpec) -> CallResult:
        return self._calls_by_key[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._calls_by_key


class SnapshotStore:
    def __init__(
        self, root: Path | str = PROJECT_ROOT / "data/snapshots", batch_size: int = 200
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.root = Path(root)
        self.batch_size = batch_size
        self.acquisition_batches: list[dict[str, int]] = []
        self.requested_specs: dict[tuple[str, str], CallSpec] = {}

    def build(
        self, block: BlockRef, specs: list[CallSpec], client: RpcClient
    ) -> StoredSnapshot:
        self.requested_specs.update({(spec.to, spec.data): spec for spec in specs})
        before = getattr(client, "network_requests", 0)
        snapshot = StoredSnapshot(block, Multicall3(client, self.batch_size).call(specs, block))
        self._persist(snapshot)
        self._record_batch(len(specs), len(specs), before, client)
        return snapshot

    def load(self, chain: int, blockhash: str) -> StoredSnapshot:
        directory = self._directory(chain, blockhash)
        header = json.loads((directory / "header.json").read_text())
        block = BlockRef(
            chain=int(header["chain"]),
            number=int(header["number"]),
            hash=str(header["hash"]).lower(),
            timestamp=int(header["timestamp"]),
        )
        with gzip.open(directory / "calls.json.gz", "rt") as handle:
            rows = json.load(handle)
        calls = tuple(
            CallResult(
                CallSpec(row["to"], row["data"], row.get("tag", "")),
                bool(row["success"]),
                str(row["raw"]).lower(),
                str(row.get("via", "")),
            )
            for row in rows
        )
        return StoredSnapshot(block, calls)

    def extend(
        self, block: BlockRef, specs: list[CallSpec], client: RpcClient
    ) -> StoredSnapshot:
        self.requested_specs.update({(spec.to, spec.data): spec for spec in specs})
        try:
            existing = self.load(block.chain, block.hash)
        except FileNotFoundError:
            before = getattr(client, "network_requests", 0)
            snapshot = StoredSnapshot(block, Multicall3(client, self.batch_size).call(specs, block))
            self._persist(snapshot)
            self._record_batch(len(specs), len(specs), before, client)
            return snapshot
        missing = [spec for spec in specs if not existing.has(spec)]
        before = getattr(client, "network_requests", 0)
        if missing:
            added = Multicall3(client, self.batch_size).call(missing, block)
            snapshot = StoredSnapshot(block, existing.calls + added)
            self._persist(snapshot)
        else:
            snapshot = existing
        self._record_batch(len(specs), len(missing), before, client)
        return snapshot

    def _persist(self, snapshot: StoredSnapshot) -> None:
        directory = self._directory(snapshot.block.chain, snapshot.block.hash)
        directory.mkdir(parents=True, exist_ok=True)
        header = {
            "chain": snapshot.block.chain,
            "number": snapshot.block.number,
            "hash": snapshot.block.hash,
            "timestamp": snapshot.block.timestamp,
        }
        self._atomic_text(directory / "header.json", json.dumps(header, separators=(",", ":")))
        rows = [
            {
                "to": result.spec.to,
                "data": result.spec.data,
                "tag": result.spec.tag,
                "success": result.success,
                "raw": result.raw,
                "via": result.via,
            }
            for result in snapshot.calls
        ]
        target = directory / "calls.json.gz"
        temporary = directory / "calls.json.gz.tmp"
        with gzip.open(temporary, "wt") as handle:
            json.dump(rows, handle, separators=(",", ":"))
        temporary.replace(target)

    def _directory(self, chain: int, blockhash: str) -> Path:
        return self.root / str(chain) / blockhash.lower()

    def _record_batch(self, requested: int, missing: int, before: int, client: RpcClient) -> None:
        self.acquisition_batches.append(
            {
                "logical_requested": requested,
                "missing": missing,
                "network_requests": getattr(client, "network_requests", before) - before,
            }
        )

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content)
        temporary.replace(path)
