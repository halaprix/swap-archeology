"""Small `cast`-backed read helper for bounded adapter verification.

Not part of ``src/swaparch`` - the JSON-RPC client is another worker's file.  This
only exists so the Uniswap V3 adapter can record its own raw evidence.

The archive URL is read from the environment (or the project's env file) and is
never printed, stored or written into any artifact: every command line is
rendered with the URL replaced by ``<rpc-url>`` before it is logged, and stderr
is redacted the same way.
"""

from __future__ import annotations

import re
import subprocess

from swaparch.rpc.client import resolve_rpc_url

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3 = "0x82ad56cb"  # aggregate3((address,bool,bytes)[])

_URL_RE = re.compile(r"https?://[^\s\"']+")

CALL_COUNT = 0


def redact(text: str) -> str:
    return _URL_RE.sub("<rpc-url>", text)


def rpc_url() -> str:
    """Use the project's shared RPC resolution without logging the URL."""
    url = resolve_rpc_url()
    if url:
        return url
    raise RuntimeError("no archive RPC URL found (ETH_RPC_URL / RPC_MAINNET / .env)")


def _run(args: list[str]) -> str:
    global CALL_COUNT
    CALL_COUNT += 1
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"cast failed ({proc.returncode}): {redact(proc.stderr.strip())} "
            f"| cmd: {redact(' '.join(args))}"
        )
    return proc.stdout.strip()


def cast_call(to: str, data: str, block: int | str) -> str:
    """One raw ``eth_call``.  Returns the 0x-prefixed return data."""
    return _run(["cast", "call", "--rpc-url", rpc_url(), "--block", str(block), to, data])


def cast_code_size(address: str, block: int | str) -> int:
    out = _run(["cast", "code", "--rpc-url", rpc_url(), "--block", str(block), address])
    return max(0, (len(out) - 2) // 2)


def cast_block_hash(block: int) -> str:
    out = (
        _run(["cast", "block", str(block), "--rpc-url", rpc_url(), "-f", "hash"]).strip().strip('"')
    )
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", out):
        raise RuntimeError(f"unexpected block hash for {block}: {out!r}")
    return out.lower()


def cast_logs(address: str, topics: list[str | None], from_block: int, to_block: int) -> str:
    args = [
        "cast",
        "logs",
        "--rpc-url",
        rpc_url(),
        "--json",
        "--from-block",
        str(from_block),
        "--to-block",
        str(to_block),
        "--address",
        address,
    ]
    for t in topics:
        args.append(t if t is not None else "null")
    return _run(args)


def pinned_multicall(specs, block, chunk: int = 100):
    """Reuse the project's hash-pinned batching, adaptive chunks and direct fallback."""
    from swaparch.core.types import BlockRef
    from swaparch.rpc.client import RpcClient
    from swaparch.rpc.multicall import Multicall3

    global CALL_COUNT
    client = RpcClient()
    try:
        ref = block if isinstance(block, BlockRef) else client.get_block(block)
        return Multicall3(client, chunk).call(specs, ref)
    finally:
        CALL_COUNT += client.network_requests


def multicall(
    calls: list[tuple[str, str]], block: int | str, chunk: int = 100
) -> list[tuple[bool, bytes]]:
    from swaparch.core.types import CallSpec

    return [
        (r.success, bytes.fromhex(r.raw[2:]))
        for r in pinned_multicall([CallSpec(to, data) for to, data in calls], block, chunk)
    ]
