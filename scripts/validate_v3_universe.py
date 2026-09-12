"""Qualify discovered V3 pools at one hash against independent QuoterV2 reads.

Run: uv run python scripts/validate_v3_universe.py 25896003
This checks quote math, not token transfers or atomic route execution.
"""

import json
import sys
from pathlib import Path

from eth_abi import decode, encode
from uniswap_v3_online_check import QUOTE_RESULT_TYPES, QUOTER_V2, SEL_QUOTE_EXACT_INPUT_SINGLE

from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory, pools_live_at

ROOT = Path(__file__).resolve().parents[1]
STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"


def main(number: int) -> int:
    client = RpcClient()
    block = client.get_block(number)
    records = pools_live_at(load_inventory("uniswap_v3"), number)
    adapter = UniswapV3Adapter()
    snapshot, acquisition = acquire({adapter.family: adapter}, records, block,
                                    SnapshotStore(), client)
    rows, specs, pending = {}, [], []
    for record in records:
        row = rows[record.pool_id] = {"pool": record.pool, "checks": [], "supported": False}
        try:
            if any(token.address == STETH for token in record.tokens):
                raise Unsupported("stETH transfer/rebasing semantics require separate validation")
            if record.pool_id in acquisition.unsupported:
                raise Unsupported(acquisition.unsupported[record.pool_id])
            state = adapter.load_state(record, snapshot)
            for token_in, token_out in (state.tokens(), state.tokens()[::-1]):
                for units in (1, 100):
                    amount = units * 10 ** token_in.decimals
                    check = {"token_in": token_in.address, "token_out": token_out.address,
                             "amount_in": amount}
                    row["checks"].append(check)
                    try:
                        output, after = state.swap(token_in.address, token_out.address, amount)
                        if output <= 0:
                            raise Unsupported("zero output at validation size")
                        check.update(local_out=output, local_sqrt_after=after.sqrt_price_x96)
                    except Unsupported as exc:
                        check["reason"] = str(exc)
                        continue
                    data = SEL_QUOTE_EXACT_INPUT_SINGLE + encode(
                        ["(address,address,uint256,uint24,uint160)"],
                        [(token_in.address, token_out.address, amount, state.fee, 0)],
                    ).hex()
                    specs.append(CallSpec(QUOTER_V2, data, "QuoterV2.quoteExactInputSingle"))
                    pending.append(check)
        except Unsupported as exc:
            row["reason"] = str(exc)

    results = Multicall3(client, chunk_size=5).call(specs, block)
    for check, result in zip(pending, results, strict=True):
        check["raw_call"] = {"to": result.spec.to, "data": result.spec.data,
                             "success": result.success, "result": result.raw, "via": result.via}
        if result.success:
            output, price, ticks, gas = decode(QUOTE_RESULT_TYPES, bytes.fromhex(result.raw[2:]))
            check.update(quoter_out=output, quoter_sqrt_after=price,
                         quoter_ticks=ticks, quoter_gas_estimate=gas,
                         matched=output == check["local_out"] and price == check["local_sqrt_after"])
        else:
            check["reason"] = "QuoterV2 reverted"
    for row in rows.values():
        row["supported"] = len(row["checks"]) == 4 and all(
            check.get("matched", False) for check in row["checks"])
        if not row["supported"]:
            row.setdefault("reason", "not all four size/direction checks matched")

    path = ROOT / f"data/validation/uniswap_v3/{block.hash}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
              "snapshot": f"data/snapshots/{block.chain}/{block.hash}",
              "quoter": QUOTER_V2, "rows": rows, "network_requests": client.network_requests,
              "scope": "1 and 100 token units each direction; integer quote math only; no settlement"}
    path.write_text(json.dumps(report, indent=2) + "\n")

    inventory_path = ROOT / "data/discovery/1/uniswap_v3.json"
    inventory = json.loads(inventory_path.read_text())
    for record in inventory["pools"]:
        hashes = set(record["config"].get("validated_block_hashes", []))
        hashes.discard(block.hash)
        row = rows.get(record["pool_id"])
        if row and row["supported"]:
            hashes.add(block.hash)
        record["config"]["validated_block_hashes"] = sorted(hashes)
        record["status"] = "supported" if hashes else "discovered_unsupported"
        record["notes"] = ("Quote validation is block-specific; see data/validation/uniswap_v3/"
                           if hashes else (row or {}).get("reason", "no per-pool quote validation"))
    inventory_path.write_text(json.dumps(inventory, indent=1) + "\n")
    supported = sum(row["supported"] for row in rows.values())
    print(json.dumps({"block": number, "supported": supported, "excluded": len(rows) - supported,
                      "network_requests": client.network_requests, "evidence": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1])))
