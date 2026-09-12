"""Online cross-check of the Uniswap V3 adapter against QuoterV2.

Builds the full local pool state at a pinned block from raw ``cast`` reads
(batched through Multicall3), quotes a size ladder in both directions with
:class:`UniV3State`, and compares wei-for-wei against
``QuoterV2.quoteExactInputSingle`` at the same block.

Every raw request/response pair is written under
``data/adapters-evidence/uniswap_v3/``.  The RPC URL never appears in any file.

Usage::

    uv run python scripts/uniswap_v3_online_check.py 25896003 23549991
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import castlib as C
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from swaparch.adapters.uniswap_v3 import adapter as A
from swaparch.core.protocols import Unsupported
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    PoolRecord,
    SupportStatus,
    Token,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "adapters-evidence" / "uniswap_v3"

POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

SEL_QUOTE_EXACT_INPUT_SINGLE = (
    "0x" + keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
)
QUOTE_RESULT_TYPES = ["uint256", "uint160", "uint32", "uint256"]

WETH_SIZES = [10**17, 10**18, 10 * 10**18, 100 * 10**18, 1000 * 10**18]
USDC_SIZES = [100 * 10**6, 10**4 * 10**6, 10**5 * 10**6, 10**6 * 10**6]


class DictSnapshot:
    """Minimal ``core.protocols.Snapshot``: a dict of CallResults keyed by (to, data)."""

    def __init__(self, block: BlockRef, results: dict[tuple[str, str], CallResult]):
        self.block = block
        self._results = results

    def get(self, spec: CallSpec) -> CallResult:
        return self._results[(spec.to, spec.data)]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self._results


def _record(word_radius: int) -> PoolRecord:
    return PoolRecord(
        family="uniswap_v3",
        chain=1,
        pool_id=f"uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:{POOL}",
        deployment="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        pool=POOL,
        tokens=(
            Token(chain=1, address=USDC, symbol="USDC", decimals=6),
            Token(chain=1, address=WETH, symbol="WETH", decimals=18),
        ),
        config={"fee": 500, "tick_spacing": 10},
        created_block=12376729,
        discovered_by={"method": "logs:PoolCreated"},
        status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )


def acquire(block: int, word_radius: int) -> tuple[DictSnapshot, PoolRecord, list[dict]]:
    """Run the adapter's own read plan against the chain and record every response."""
    raw_log: list[dict] = []
    results: dict[tuple[str, str], CallResult] = {}
    block_hash = C.cast_block_hash(block)
    ref = BlockRef(chain=1, number=block, hash=block_hash, timestamp=0)
    record = _record(word_radius)
    adapter = A.UniswapV3Adapter(word_radius=word_radius)

    def run(specs: list[CallSpec], phase: str) -> None:
        if not specs:
            return
        out = C.pinned_multicall(specs, ref, chunk=200)
        for spec, result in zip(specs, out, strict=True):
            ok, hexret = result.success, result.raw
            results[(spec.to, spec.data)] = CallResult(
                spec=spec, success=ok, raw=hexret, via=result.via
            )
            raw_log.append(
                {
                    "phase": phase,
                    "block": block,
                    "block_hash": block_hash,
                    "to": spec.to,
                    "call": spec.tag,
                    "data": spec.data,
                    "success": ok,
                    "via": result.via,
                    "result": hexret,
                }
            )

    snap = DictSnapshot(ref, results)
    run(adapter.read_requests(record, ref), "read_requests")
    round_no = 0
    while True:
        round_no += 1
        more = adapter.dependent_requests(record, ref, snap)
        if not more:
            break
        run(more, f"dependent_requests[{round_no}]")
        if round_no > 5:  # pragma: no cover
            raise RuntimeError("dependent_requests did not converge")
    if C.cast_block_hash(block) != block_hash:
        raise RuntimeError(f"block {block} hash changed during acquisition")
    return snap, record, raw_log


def quoter(block: BlockRef, token_in: str, token_out: str, amounts: list[int]) -> list[dict]:
    calls = []
    for amount in amounts:
        args = abi_encode(
            ["(address,address,uint256,uint24,uint160)"],
            [(token_in, token_out, amount, 500, 0)],
        )
        calls.append((QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE + args.hex()))
    out = C.pinned_multicall([CallSpec(to, data) for to, data in calls], block, chunk=5)
    rows = []
    for (to, data), amount, result in zip(calls, amounts, out, strict=True):
        ok, ret = result.success, bytes.fromhex(result.raw[2:])
        row = {
            "to": to,
            "data": data,
            "call": "QuoterV2.quoteExactInputSingle",
            "amount_in": amount,
            "success": ok,
            "via": result.via,
            "block_hash": block.hash,
            "result": "0x" + ret.hex(),
        }
        if ok:
            decoded = abi_decode(QUOTE_RESULT_TYPES, ret)
            row["amount_out"] = decoded[0]
            row["sqrt_price_after"] = decoded[1]
            row["initialized_ticks_crossed"] = decoded[2]
            row["gas_estimate"] = decoded[3]
        rows.append(row)
    return rows


def check_block(block: int, word_radius: int) -> dict:
    snap, record, raw_log = acquire(block, word_radius)
    adapter = A.UniswapV3Adapter(word_radius=word_radius)
    state = adapter.load_state(record, snap)
    prov = dict(adapter.provenance(record, snap))

    quotes = {
        "WETH->USDC": quoter(snap.block, WETH, USDC, WETH_SIZES),
        "USDC->WETH": quoter(snap.block, USDC, WETH, USDC_SIZES),
    }

    rows = []
    for direction, sizes, tin, tout in (
        ("WETH->USDC", WETH_SIZES, WETH, USDC),
        ("USDC->WETH", USDC_SIZES, USDC, WETH),
    ):
        for amount, q in zip(sizes, quotes[direction]):
            row = {
                "direction": direction,
                "amount_in": amount,
                "quoter_out": q.get("amount_out"),
                "quoter_sqrt_after": q.get("sqrt_price_after"),
                "quoter_ticks_crossed": q.get("initialized_ticks_crossed"),
                "quoter_ok": q["success"],
            }
            try:
                out, new_state = state.swap(tin, tout, amount)
                row["local_out"] = out
                row["local_sqrt_after"] = new_state.sqrt_price_x96
                row["error"] = None
            except Unsupported as exc:
                row["local_out"] = None
                row["local_sqrt_after"] = None
                row["error"] = str(exc)
            if row["local_out"] is not None and row["quoter_out"] is not None:
                row["diff"] = row["local_out"] - row["quoter_out"]
                row["sqrt_match"] = row["local_sqrt_after"] == row["quoter_sqrt_after"]
            else:
                row["diff"] = None
                row["sqrt_match"] = None
            rows.append(row)

    if C.cast_block_hash(block) != snap.block.hash:
        raise RuntimeError(f"block {block} hash changed during quote validation")

    evidence = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chain": 1,
        "block": block,
        "block_hash": snap.block.hash,
        "pool": POOL,
        "quoter_v2": QUOTER_V2,
        "multicall3": C.MULTICALL3,
        "word_radius": word_radius,
        "state": {
            "sqrtPriceX96": state.sqrt_price_x96,
            "tick": state.tick,
            "liquidity": state.liquidity,
            "fee": state.fee,
            "tickSpacing": state.tick_spacing,
            "token0": state.token0.address,
            "token1": state.token1.address,
            "word_lo": state.word_lo,
            "word_hi": state.word_hi,
            "initialized_ticks": len(state.tick_liquidity_net),
        },
        "provenance": prov,
        "comparison": rows,
        "raw_calls": raw_log,
        "raw_quoter_calls": quotes,
    }
    path = EVID / f"quoter-cross-check-{block}-r{word_radius}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=1))

    # A compact fixture for the offline state test.
    fixture = {
        "chain": 1,
        "block": block,
        "block_hash": snap.block.hash,
        "pool": POOL,
        "word_radius": word_radius,
        "calls": [
            {"to": e["to"], "tag": e["call"], "data": e["data"], "success": e["success"],
             "result": e["result"]}
            for e in raw_log
        ],
        "expected_quotes": [
            {"direction": r["direction"], "amount_in": r["amount_in"],
             "quoter_out": r["quoter_out"], "quoter_sqrt_after": r["quoter_sqrt_after"]}
            for r in rows
        ],
    }
    (EVID / f"snapshot-{block}-r{word_radius}.json").write_text(json.dumps(fixture))
    return evidence


def main(argv: list[str]) -> int:
    blocks = [int(a) for a in argv[1:] if not a.startswith("-")]
    radius = 8
    for a in argv[1:]:
        if a.startswith("--radius="):
            radius = int(a.split("=", 1)[1])
    if not blocks:
        blocks = [25896003, 23549991]
    failed = False
    for block in blocks:
        ev = check_block(block, radius)
        print(f"== block {block} (word radius {radius}) ==")
        print(f"   words [{ev['state']['word_lo']},{ev['state']['word_hi']}] "
              f"ticks {ev['state']['initialized_ticks']}")
        for r in ev["comparison"]:
            failed |= r["diff"] != 0 or r["sqrt_match"] is not True
            print(f"   {r['direction']:>10} in={r['amount_in']:<25} local={r['local_out']} "
                  f"quoter={r['quoter_out']} diff={r['diff']} sqrt_match={r['sqrt_match']}"
                  + (f" ERR={r['error']}" if r["error"] else ""))
    print(f"total cast invocations plus RPC requests this run: {C.CALL_COUNT}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
