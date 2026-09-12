"""Inspect intra-block V3 swaps around the October price-gap samples."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from swaparch.rpc.client import RpcClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/october-validation"
CACHE = OUT / "rpc-cache-logs"
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
MINT = "0x712faa344eac6399174fdfa887d9e1451e9b55ce58ee440c91c660229962a5a6"
BURN = "0x367d437198a5c267b114751b997e2aabf3c34c89fec2f2707459b9ac6646deaa"
WINDOWS = ((23550019, 23550021), (23550043, 23550047))


def word(data: str, index: int) -> int:
    return int(data[2 + index * 64 : 2 + (index + 1) * 64], 16)


def signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def spot(sqrt_price_x96: int) -> float:
    return (2**192 / sqrt_price_x96**2) * 10**12


def decode(log: dict, kind: str) -> dict:
    result = {
        "kind": kind,
        "transactionHash": log["transactionHash"],
        "transactionIndex": int(log["transactionIndex"], 16),
        "logIndex": int(log["logIndex"], 16),
        "blockNumber": int(log["blockNumber"], 16),
        "blockHash": log["blockHash"],
        "address": log["address"],
        "topics": log["topics"],
        "data": log["data"],
    }
    if kind == "Swap":
        amount0 = signed(word(log["data"], 0), 256)
        amount1 = signed(word(log["data"], 1), 256)
        sqrt = word(log["data"], 2)
        result.update(
            amount0=amount0,
            amount1=amount1,
            sqrtPriceX96=str(sqrt),
            liquidity=str(word(log["data"], 3)),
            tick=signed(word(log["data"], 4), 256),
            spotUsdcPerWeth=spot(sqrt),
            # V3 amounts are pool deltas: a WETH sale sends WETH to the pool
            # (positive amount1) and removes USDC (negative amount0).
            wethSell=(amount0 < 0 and amount1 > 0),
            executionUsdcPerWeth=(-amount0 / amount1 * 10**12 if amount0 < 0 and amount1 > 0 else None),
        )
    elif kind in ("Mint", "Burn"):
        # Mint includes an unindexed sender before the three amounts; Burn does not.
        offset = 1 if kind == "Mint" else 0
        result.update(liquidity=str(word(log["data"], offset)), amount0=str(word(log["data"], offset + 1)), amount1=str(word(log["data"], offset + 2)))
    else:
        result.update(amount0=str(word(log["data"], 0)), amount1=str(word(log["data"], 1)), liquidity=str(word(log["data"], 2)))
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = RpcClient(cache_root=CACHE)
    by_block: dict[int, dict] = {}
    for start, end in WINDOWS:
        # One request per small window unless the provider requires subdivision.
        logs = client.get_logs(POOL, [[SWAP, MINT, BURN]], start, end, chunk=end - start + 1)
        for log in logs:
            block = int(log["blockNumber"], 16)
            by_block.setdefault(block, {"swaps": [], "mints": [], "burns": []})
            topic = log["topics"][0].lower()
            kind = {SWAP.lower(): "Swap", MINT.lower(): "Mint", BURN.lower(): "Burn"}[topic]
            by_block[block][{"Swap": "swaps", "Mint": "mints", "Burn": "burns"}[kind]].append(decode(log, kind))
    for events in by_block.values():
        for key in ("swaps", "mints", "burns"):
            events[key].sort(key=lambda x: (x["transactionIndex"], x["logIndex"]))

    saved = json.loads((ROOT / "outputs/october-price-gap/prices.json").read_text())["rows"]
    saved_by_block = {row["block"]: row for row in saved}
    blocks = list(range(23550019, 23550022)) + list(range(23550043, 23550048))
    rows = []
    for block in blocks:
        events = by_block.get(block, {"swaps": [], "mints": [], "burns": []})
        swaps = events["swaps"]
        observed = [s["spotUsdcPerWeth"] for s in swaps]
        saved_row = saved_by_block.get(block)
        slot0_sqrt = None
        if saved_row:
            ref = client.get_block(block)
            slot = client.eth_call(POOL, "0x3850c7bd", ref)
            if slot.success:
                slot0_sqrt = str(word(slot.raw, 0))
                assert swaps and swaps[-1]["sqrtPriceX96"] == slot0_sqrt
        rows.append(
            {
                "block": block,
                "swaps": swaps,
                "mintCount": len(events["mints"]),
                "burnCount": len(events["burns"]),
                "swapCount": len(swaps),
                "swapSpotMin": min(observed) if observed else None,
                "swapSpotMax": max(observed) if observed else None,
                "swapSpotLast": swaps[-1]["spotUsdcPerWeth"] if swaps else None,
                "savedEndblockV3Spot": saved_row["v3Spot"] if saved_row else None,
                "savedEndblockExecution100": saved_row["v3100"] if saved_row else None,
                "freshSlot0SqrtPriceX96": slot0_sqrt,
            }
        )
    sells = [swap for row in rows for swap in row["swaps"] if swap["wethSell"]]
    assert sells and all(swap["executionUsdcPerWeth"] > 0 for swap in sells)
    assert any(swap["amount0"] < 0 and swap["amount1"] > 0 for swap in sells)
    payload = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "chain": "Ethereum mainnet",
        "pool": POOL,
        "poolTokens": {"token0": "USDC", "token1": "WETH"},
        "eventTopics": {"Swap": SWAP, "Mint": MINT, "Burn": BURN},
        "windows": [{"fromBlock": a, "toBlock": b} for a, b in WINDOWS],
        "cacheRoot": str(CACHE.relative_to(ROOT)),
        "networkRequests": client.network_requests,
        "rawLogsIncluded": True,
        "rows": rows,
    }
    (OUT / "intrablock.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"output": str(OUT / "intrablock.json"), "networkRequests": client.network_requests, "rows": len(rows)}))


if __name__ == "__main__":
    main()
