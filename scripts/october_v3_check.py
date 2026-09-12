"""Independent October-2025 Uniswap V3 state and QuoterV2 validation.

Reads the model state only from the prepared collection artifacts.  All
independent RPC reads go to ``outputs/october-validation/rpc-cache-v3`` and are
pinned with a freshly fetched canonical header hash.  No collection artifact is
modified.

Run: uv run python scripts/october_v3_check.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.collection_quotes import prepared_collection_context
from swaparch.rpc.client import RpcClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/october-validation/v3.json"
CACHE = ROOT / "outputs/october-validation/rpc-cache-v3"

BLOCKS = (23_550_020, 23_550_044, 23_550_046)
QUOTER_V2 = "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
POOLS = {
    "weth_usdc": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
    "weth_dai": "0x60594a405d53811d3bc4766596efd80fd545a270",
    "dai_usdc": "0x5777d92f208679db4b9778590fa3cab3ac9e2168",
}
SLOT0 = "0x3850c7bd"
LIQUIDITY = "0x1a686502"
QUOTE = "0x" + keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
QUOTE_TYPES = ("uint256", "uint160", "uint32", "uint256")


def _state_by_pool(context):
    states = {state.record.pool.lower(): state for state in context.states}
    missing = set(POOLS.values()) - set(states)
    if missing:
        raise RuntimeError(f"collection state missing requested pools: {sorted(missing)}")
    return states


def _raw_call(client: RpcClient, block, to: str, data: str, tag: str) -> dict:
    result = client.eth_call(to, data, block)
    return {"to": to, "data": data, "tag": tag, "success": result.success,
            "raw": result.raw, "via": result.via}


def _fresh_pool_state(client: RpcClient, block, pool: str) -> dict:
    slot = _raw_call(client, block, pool, SLOT0, "slot0()")
    liq = _raw_call(client, block, pool, LIQUIDITY, "liquidity()")
    if not slot["success"] or not liq["success"]:
        raise RuntimeError(f"pool state call failed for {pool}")
    sqrt_price_x96, tick, *_ = decode(
        ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"),
        bytes.fromhex(slot["raw"][2:]),
    )
    return {"slot0": {"sqrt_price_x96": sqrt_price_x96, "tick": tick, "raw_call": slot},
            "liquidity": {"value": decode(("uint128",), bytes.fromhex(liq["raw"][2:]))[0],
                          "raw_call": liq}}


def _quote(client: RpcClient, block, token_in: str, token_out: str, amount: int, fee: int) -> dict:
    data = QUOTE + encode(
        ["(address,address,uint256,uint24,uint160)"],
        [(token_in, token_out, amount, fee, 0)],
    ).hex()
    raw = _raw_call(client, block, QUOTER_V2, data, "QuoterV2.quoteExactInputSingle")
    row = {"amount_in": amount, "token_in": token_in, "token_out": token_out, "fee": fee,
           "raw_call": raw}
    if raw["success"]:
        amount_out, sqrt_after, ticks_crossed, gas_estimate = decode(
            QUOTE_TYPES, bytes.fromhex(raw["raw"][2:])
        )
        row.update(amount_out=amount_out, sqrt_price_after=sqrt_after,
                   initialized_ticks_crossed=ticks_crossed, gas_estimate=gas_estimate)
    return row


def _compare_quote(state, token_in: str, token_out: str, amount: int, quoter: dict) -> dict:
    local_out, after = state.swap(token_in, token_out, amount)
    return {"amount_in": amount, "model_out": local_out,
            "model_sqrt_price_after": after.sqrt_price_x96,
            "quoter_out": quoter.get("amount_out"),
            "quoter_sqrt_price_after": quoter.get("sqrt_price_after"),
            "out_difference_wei": local_out - quoter["amount_out"],
            "sqrt_price_match": after.sqrt_price_x96 == quoter["sqrt_price_after"],
            "exact_match": local_out == quoter["amount_out"]
            and after.sqrt_price_x96 == quoter["sqrt_price_after"],
            "quoter": quoter}


def _model_metadata(state) -> dict:
    return {"pool_id": state.record.pool_id, "fee": state.fee, "tick_spacing": state.tick_spacing,
            "sqrt_price_x96": state.sqrt_price_x96, "tick": state.tick,
            "liquidity": state.liquidity, "word_lo": state.word_lo, "word_hi": state.word_hi,
            "initialized_ticks": len(state.tick_liquidity_net)}


def check_block(client: RpcClient, number: int) -> dict:
    context, annotations, offline_client = prepared_collection_context(number, {"uniswap_v3"})
    if offline_client.network_requests:
        raise RuntimeError("prepared collection context unexpectedly used RPC")
    model = _state_by_pool(context)
    fresh = client.get_block(number)
    if fresh.hash.lower() != context.block.hash.lower():
        raise RuntimeError(f"fresh header differs from collection hash at {number}")

    pool_rows = {}
    for name, pool in POOLS.items():
        state = model[pool]
        live = _fresh_pool_state(client, fresh, pool)
        pool_rows[name] = {
            "address": pool, "model": _model_metadata(state), "fresh": live,
            "slot0_match": state.sqrt_price_x96 == live["slot0"]["sqrt_price_x96"]
            and state.tick == live["slot0"]["tick"],
            "liquidity_match": state.liquidity == live["liquidity"]["value"],
        }

    direct = []
    for amount in (10**18, 10 * 10**18, 100 * 10**18):
        quote = _quote(client, fresh, WETH, USDC, amount, model[POOLS["weth_usdc"]].fee)
        direct.append(_compare_quote(model[POOLS["weth_usdc"]], WETH, USDC, amount, quote))

    first_quote = _quote(client, fresh, WETH, DAI, 10**18, model[POOLS["weth_dai"]].fee)
    first = _compare_quote(model[POOLS["weth_dai"]], WETH, DAI, 10**18, first_quote)
    second_quote = _quote(client, fresh, DAI, USDC, first["model_out"], model[POOLS["dai_usdc"]].fee)
    second = _compare_quote(model[POOLS["dai_usdc"]], DAI, USDC, first["model_out"], second_quote)
    return {"block": number, "block_hash": fresh.hash, "timestamp": fresh.timestamp,
            "collection": annotations, "pools": pool_rows, "direct_weth_usdc": direct,
            "sequential_weth_dai_usdc": {"input_weth": 10**18, "leg_1": first, "leg_2": second,
                                           "model_out": second["model_out"],
                                           "quoter_out": second["quoter_out"],
                                           "out_difference_wei": second["out_difference_wei"],
                                           "exact_match": first["exact_match"] and second["exact_match"]}}


def main() -> None:
    client = RpcClient(cache_root=CACHE)
    rows = [check_block(client, number) for number in BLOCKS]
    output = {"scope": "three exact October blocks; cached model state versus fresh hash-pinned V3 and QuoterV2 calls",
              "chain": 1, "quoter_v2": QUOTER_V2, "rpc_cache": str(CACHE),
              "network_requests": client.network_requests, "blocks": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n")
    assert all(all(p["slot0_match"] and p["liquidity_match"] for p in r["pools"].values())
               and all(q["exact_match"] for q in r["direct_weth_usdc"])
               and r["sequential_weth_dai_usdc"]["exact_match"] for r in rows), "V3 independent validation failed; inspect retained evidence"
    print(json.dumps({"output": str(OUT), "network_requests": client.network_requests,
                      "all_exact": all(
                          all(pool["slot0_match"] and pool["liquidity_match"] for pool in row["pools"].values())
                          and all(quote["exact_match"] for quote in row["direct_weth_usdc"])
                          and row["sequential_weth_dai_usdc"]["exact_match"]
                          for row in rows)}, indent=2))


if __name__ == "__main__":
    main()
