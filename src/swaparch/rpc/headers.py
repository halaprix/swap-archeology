"""Block-header parsing, comparison, and disk caching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swaparch.core.types import BlockRef


def block_ref_from_rpc(chain: int, raw: dict[str, Any]) -> BlockRef:
    return BlockRef(
        chain=chain,
        number=int(raw["number"], 16),
        hash=str(raw["hash"]).lower(),
        timestamp=int(raw["timestamp"], 16),
    )


def hashes_match(block: BlockRef, expected_hash: str) -> bool:
    return block.hash.lower() == expected_hash.lower()


def require_hash(block: BlockRef, expected_hash: str) -> None:
    if not hashes_match(block, expected_hash):
        raise ValueError(
            f"block {block.number} hash mismatch: expected {expected_hash.lower()}, got {block.hash}"
        )


class HeaderCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, chain: int, number: int) -> BlockRef | None:
        path = self._path(chain, number)
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return block_ref_from_rpc(chain, payload["response"]["result"])

    def store(self, chain: int, raw: dict[str, Any]) -> BlockRef:
        block = block_ref_from_rpc(chain, raw)
        path = self._path(chain, block.number)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request": {
                "method": "eth_getBlockByNumber",
                "params": [hex(block.number), False],
            },
            "response": {"result": raw},
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")))
        temporary.replace(path)
        return block

    def _path(self, chain: int, number: int) -> Path:
        return self.root / "headers" / str(chain) / f"{number}.json"

