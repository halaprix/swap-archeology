"""Discover and qualify canonical V2 pools with pinned Router02 reference calls.

uv run python scripts/uniswap_v2_run.py 25896003 [other historical blocks...]
Discovery is incremental per token-pair filter; raw logs and RPC responses persist.
"""

import json
import sys
from dataclasses import replace
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.adapters.uniswap_v2 import FACTORY, ROUTER02, STETH, UniswapV2Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import CallSpec
from swaparch.discovery.uniswap_v2 import (
    FACTORY_CREATION_BLOCK,
    build_pair_created_filters,
    pool_record_from_log,
)
from swaparch.discovery.uniswap_v3 import pool_record_to_json
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory, pools_live_at

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data/discovery/1/uniswap_v2.json"
GET_AMOUNT_OUT = "0x" + keccak(text="getAmountOut(uint256,uint256,uint256)")[:4].hex()
GET_AMOUNTS_OUT = "0x" + keccak(text="getAmountsOut(uint256,address[])")[:4].hex()


def discover(client, block):
    inventory = json.loads(INVENTORY.read_text())
    tokens = {token.address: token for pool in load_inventory("uniswap_v3") for token in pool.tokens}
    records = {row["pool_id"]: row for row in inventory["pools"]}
    for spec in build_pair_created_filters({t.symbol: t.address for t in tokens.values()},
                                           to_block=block.number):
        previous = [row for row in inventory["coverage"]
                    if row.get("topics") == list(spec.topics)
                    and row["from_block"] == FACTORY_CREATION_BLOCK]
        for row in previous:
            anchor = block if row["to_block"] == block.number else client.get_block(row["to_block"])
            if anchor.hash != row["to_block_hash"]:
                raise ValueError("V2 discovery coverage hash changed; refresh the affected filter")
        start = max((row["to_block"] + 1 for row in previous), default=FACTORY_CREATION_BLOCK)
        if start > block.number:
            continue
        logs = client.get_logs(FACTORY, list(spec.topics), start, block.number,
                               chunk=block.number - start + 1)
        for log in logs:
            record = pool_record_from_log(log, tokens, filter_meta=spec.meta)
            # Named endpoint tokens use the standard no-transfer-fee quote model.
            # Raw stETH stays excluded. This does not validate account permissions
            # or execution transfers; reserve/balance equality is checked per block.
            record = replace(record, config={**record.config, "transfer_semantics": {
                token.address: "standard" for token in record.tokens if token.address != STETH}})
            if record.pool_id not in records:
                records[record.pool_id] = pool_record_to_json(record)
        evidence = ROOT / "data/discovery-evidence/uniswap_v2" / (
            f"{spec.meta['token0']}-{spec.meta['token1']}-{start}-{block.number}.json")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({"chain": block.chain, "to_block_hash": block.hash,
                                       "request": spec.as_dict(), "actual_from_block": start,
                                       "logs": logs}, indent=2) + "\n")
        if previous:
            row = max(previous, key=lambda item: item["to_block"])
            row.update(to_block=block.number, to_block_hash=block.hash)
            row.setdefault("evidence", []).append(str(evidence.relative_to(ROOT)))
        else:
            inventory["coverage"].append({"method": "logs:PairCreated", "deployment": FACTORY,
                                          "topics": list(spec.topics), "filter": dict(spec.meta),
                                          "from_block": start, "to_block": block.number,
                                          "to_block_hash": block.hash,
                                          "evidence": [str(evidence.relative_to(ROOT))]})
        inventory["pools"] = sorted(records.values(), key=lambda row: row["pool_id"])
        INVENTORY.write_text(json.dumps(inventory, indent=1) + "\n")
    return load_inventory("uniswap_v2")


def qualify(client, block, records):
    adapter = UniswapV2Adapter()
    live = pools_live_at(records, block.number)
    snapshot, _acquisition = acquire({adapter.family: adapter}, live, block, SnapshotStore(), client)
    rows, specs, pending = {}, [], []
    for record in live:
        row = rows[record.pool_id] = {"pool": record.pool, "supported": False, "checks": []}
        try:
            state = adapter.load_state(record, snapshot)
            for token_in, token_out in (state.tokens(), state.tokens()[::-1]):
                reserve_in, reserve_out = (state.reserve0, state.reserve1) if token_in == state.token0 else (state.reserve1, state.reserve0)
                for units in (1, 100):
                    amount = units * 10 ** token_in.decimals
                    check = {"token_in": token_in.address, "token_out": token_out.address,
                             "amount_in": amount}
                    row["checks"].append(check)
                    try:
                        check["local_out"] = state.swap(token_in.address, token_out.address, amount)[0]
                    except Unsupported as exc:
                        check["reason"] = str(exc)
                        continue
                    for method, data in (
                        ("getAmountOut", GET_AMOUNT_OUT + encode(["uint256"] * 3,
                                                                [amount, reserve_in, reserve_out]).hex()),
                        ("getAmountsOut", GET_AMOUNTS_OUT + encode(["uint256", "address[]"],
                                                                   [amount, [token_in.address, token_out.address]]).hex()),
                    ):
                        specs.append(CallSpec(ROUTER02, data, method))
                        pending.append((check, method))
        except Unsupported as exc:
            row["reason"] = str(exc)
    results = Multicall3(client).call(specs, block)
    for (check, method), result in zip(pending, results, strict=True):
        check[method] = {"to": result.spec.to, "data": result.spec.data, "success": result.success,
                         "result": result.raw, "via": result.via}
        if result.success:
            value = decode(["uint256" if method == "getAmountOut" else "uint256[]"],
                           bytes.fromhex(result.raw[2:]))[0]
            check[method]["amount_out"] = value if method == "getAmountOut" else value[-1]
        check[method]["matched"] = (result.success and check[method]["amount_out"] == check["local_out"])
    for row in rows.values():
        row["supported"] = len(row["checks"]) == 4 and all(
            check.get(method, {}).get("matched", False)
            for check in row["checks"] for method in ("getAmountOut", "getAmountsOut"))
        if not row["supported"]:
            row.setdefault("reason", "four size/direction checks did not all pass both Router references")
    path = ROOT / f"data/validation/uniswap_v2/{block.hash}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chain": block.chain, "block": block.number, "block_hash": block.hash,
                                "router": ROUTER02, "rows": rows,
                                "snapshot": f"data/snapshots/{block.chain}/{block.hash}",
                                "scope": "standard-token reserve quotes; no execution-transfer claim"}, indent=2) + "\n")
    inventory = json.loads(INVENTORY.read_text())
    for record in inventory["pools"]:
        hashes = set(record["config"].get("validated_block_hashes", []))
        hashes.discard(block.hash)
        row = rows.get(record["pool_id"])
        if row and row["supported"]:
            hashes.add(block.hash)
        record["config"]["validated_block_hashes"] = sorted(hashes)
        record["status"] = "supported" if hashes else "discovered_unsupported"
        record["notes"] = "Block-specific Router02 checks in data/validation/uniswap_v2/" if hashes else (row or {}).get("reason", "not deployed or unqualified at requested block")
    inventory["status"] = "supported" if any(row["status"] == "supported" for row in inventory["pools"]) else "discovered_unsupported"
    inventory["unresolved"] = [row for row in inventory["unresolved"] if row["what"] != "pool list for the endpoint/connector token set"]
    INVENTORY.write_text(json.dumps(inventory, indent=1) + "\n")
    print(json.dumps({"block": block.number, "discovered": len(records), "live": len(live),
                      "supported": sum(row["supported"] for row in rows.values()),
                      "network_requests": client.network_requests, "evidence": str(path)}), flush=True)


if __name__ == "__main__":
    client = RpcClient()
    numbers = [int(value) for value in sys.argv[1:]] or [25896003]
    records = discover(client, client.get_block(max(numbers)))
    for number in numbers:
        qualify(client, client.get_block(number), records)
