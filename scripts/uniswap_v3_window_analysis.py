"""Offline: how wide a tick-bitmap window each trade size actually needs.

Re-loads the recorded radius-8 snapshot fixtures and rebuilds
:class:`UniV3State` with progressively narrower word windows, so the report can
state the *needed* window per size rather than asserting that 8 is enough.
Also exercises an oversized trade to show the ``Unsupported`` message.

No RPC.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from eth_abi import decode as abi_decode

from swaparch.adapters.uniswap_v3 import adapter as A
from swaparch.adapters.uniswap_v3 import math as m
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import Unsupported
from swaparch.core.types import PoolRecord, SupportStatus, Token

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "adapters-evidence" / "uniswap_v3"

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def load(fixture: pathlib.Path) -> dict:
    return json.loads(fixture.read_text())


def build_state(fix: dict, radius: int | None = None) -> UniV3State:
    by_tag = {c["tag"]: c for c in fix["calls"] if c["success"]}
    slot0 = abi_decode(A.SLOT0_TYPES, bytes.fromhex(by_tag["univ3:slot0"]["result"][2:]))
    liquidity = abi_decode(["uint128"], bytes.fromhex(by_tag["univ3:liquidity"]["result"][2:]))[0]
    fee = abi_decode(["uint24"], bytes.fromhex(by_tag["univ3:fee"]["result"][2:]))[0]
    spacing = abi_decode(["int24"], bytes.fromhex(by_tag["univ3:tickSpacing"]["result"][2:]))[0]
    t0 = abi_decode(["address"], bytes.fromhex(by_tag["univ3:token0"]["result"][2:]))[0].lower()
    t1 = abi_decode(["address"], bytes.fromhex(by_tag["univ3:token1"]["result"][2:]))[0].lower()

    centre = m.word_pos_for_tick(slot0[1], spacing)
    bitmap: dict[int, int] = {}
    for tag, call in by_tag.items():
        if not tag.startswith("univ3:tickBitmap:"):
            continue
        w = int(tag.rsplit(":", 1)[1])
        if radius is not None and abs(w - centre) > radius:
            continue
        bitmap[w] = abi_decode(["uint256"], bytes.fromhex(call["result"][2:]))[0]
    lo, hi = min(bitmap), max(bitmap)

    liq_net: dict[int, int] = {}
    for tag, call in by_tag.items():
        if not tag.startswith("univ3:ticks:"):
            continue
        t = int(tag.rsplit(":", 1)[1])
        if not (lo <= m.word_pos_for_tick(t, spacing) <= hi):
            continue
        liq_net[t] = abi_decode(A.TICKS_TYPES, bytes.fromhex(call["result"][2:]))[1]

    record = PoolRecord(
        family="uniswap_v3", chain=1,
        pool_id=f"uniswap_v3:0x1f98431c8ad98523631ae4a59f267346ea31f984:{fix['pool']}",
        deployment="0x1f98431c8ad98523631ae4a59f267346ea31f984", pool=fix["pool"],
        tokens=(Token(1, t0, "USDC", 6), Token(1, t1, "WETH", 18)),
        config={"fee": fee, "tick_spacing": spacing}, created_block=12376729,
        discovered_by={}, status=SupportStatus.DISCOVERED_UNSUPPORTED,
    )
    return UniV3State(
        record=record, sqrt_price_x96=slot0[0], tick=slot0[1], liquidity=liquidity,
        fee=fee, tick_spacing=spacing, token0=record.tokens[0], token1=record.tokens[1],
        tick_bitmap=bitmap, tick_liquidity_net=liq_net, word_lo=lo, word_hi=hi,
    )


def minimal_radius(fix: dict, tin: str, tout: str, amount: int, expected: int | None) -> dict:
    last = "no usable tick coverage"
    for r in range(9):
        try:
            state = build_state(fix, radius=r)
        except (Unsupported, KeyError, ValueError) as exc:
            last = str(exc)
            continue
        try:
            out, _ = state.swap(tin, tout, amount)
        except Unsupported as exc:
            last = str(exc)
            continue
        return {"radius": r, "out": out, "matches_expected": expected is None or out == expected}
    return {"radius": None, "out": None, "error": last}


def main() -> int:
    report: dict[str, object] = {}
    for fixture in sorted(EVID.glob("snapshot-*-r8.json")):
        fix = load(fixture)
        block = fix["block"]
        rows = []
        for q in fix["expected_quotes"]:
            tin, tout = (WETH, USDC) if q["direction"] == "WETH->USDC" else (USDC, WETH)
            res = minimal_radius(fix, tin, tout, q["amount_in"], q["quoter_out"])
            rows.append({"direction": q["direction"], "amount_in": q["amount_in"],
                         "quoter_out": q["quoter_out"], **res})
        # deliberately oversized trade to show the coverage failure
        big = 200_000 * 10**18
        state8 = build_state(fix, radius=8)
        try:
            out, _ = state8.swap(WETH, USDC, big)
            oversized = {"amount_in": big, "out": out, "error": None}
        except Unsupported as exc:
            oversized = {"amount_in": big, "out": None, "error": str(exc)}
        report[str(block)] = {
            "pool": fix["pool"],
            "block_hash": fix["block_hash"],
            "minimal_word_radius_per_size": rows,
            "oversized_trade_at_radius_8": oversized,
        }
        print(f"== block {block} ==")
        for r in rows:
            print(f"   {r['direction']:>10} in={r['amount_in']:<25} minimal_radius={r['radius']} "
                  f"out_matches_quoter={r.get('matches_expected')}")
        print(f"   oversized {big // 10**18} WETH at radius 8 -> "
              f"{oversized['error'] or oversized['out']}")
    (EVID / "window-analysis.json").write_text(json.dumps(report, indent=1))
    print(f"written: {EVID / 'window-analysis.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
