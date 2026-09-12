"""Compare saved selected routes with QuoterV2.quoteExactInput at their block hash.

Only pool-disjoint plans qualify: the Quoter reverts each simulated pool swap,
so it cannot verify the updated shared state of repeated pools. Source:
https://github.com/Uniswap/v3-periphery/blob/main/contracts/lens/QuoterV2.sol
"""

import hashlib
import json
import sys
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.core.types import BlockRef, CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3
from swaparch.universe import load_inventory

QUOTER = "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
SELECTOR = "0x" + keccak(text="quoteExactInput(bytes,uint256)")[:4].hex()
ROOT = Path(__file__).resolve().parents[1]


def main(path: Path) -> int:
    raw_report = path.read_bytes()
    report = json.loads(raw_report)
    block = BlockRef(report["chain"], report["block"], report["block_hash"], report["timestamp"])
    records = {record.pool_id: record for record in load_inventory("uniswap_v3")}
    specs, checks = [], []
    for kind in ("saved_reference_pool", "single_pool_baseline", "best_single_path", "best_split"):
        candidate = report.get(kind)
        if candidate is None:
            continue
        request = report["request"]
        if (candidate["feasible"] is not True or candidate["residual_in"] != 0
                or candidate["amount_in_spent"] != request["amount_in"]):
            raise ValueError("candidate is not a full feasible fill")
        pool_ids = [step["pool_id"] for step in candidate["steps"]]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError(f"{kind}: repeated pools need independent stateful verification")
        offset, total, allocated = 0, 0, 0
        for allocation in candidate["search_info"]["allocation"]:
            steps = candidate["steps"][offset:offset + len(allocation["path"]) ]
            offset += len(steps)
            if ([step["pool_id"] for step in steps] != allocation["path"] or not steps
                    or steps[0]["amount_in"] != allocation["amount_in"]):
                raise ValueError("allocation does not match ordered steps")
            packed = bytes.fromhex(steps[0]["token_in"][2:])
            token, amount = request["token_in"], allocation["amount_in"]
            for step in steps:
                record = records[step["pool_id"]]
                if (step["token_in"] != token or step["amount_in"] != amount
                        or {step["token_in"], step["token_out"]} !=
                        {t.address for t in record.tokens}):
                    raise ValueError("path token/amount continuity or pool identity mismatch")
                packed += int(record.config["fee"]).to_bytes(3, "big")
                packed += bytes.fromhex(step["token_out"][2:])
                token, amount = step["token_out"], step["amount_out"]
            if token != request["token_out"]:
                raise ValueError("path ends in the wrong output token")
            data = SELECTOR + encode(["bytes", "uint256"], [packed, allocation["amount_in"]]).hex()
            specs.append(CallSpec(QUOTER, data, "QuoterV2.quoteExactInput"))
            checks.append({"kind": kind, "path": allocation["path"],
                           "amount_in": allocation["amount_in"], "expected_out": steps[-1]["amount_out"]})
            total += steps[-1]["amount_out"]
            allocated += allocation["amount_in"]
        if (offset != len(candidate["steps"]) or total != candidate["amount_out"]
                or allocated != request["amount_in"]):
            raise ValueError("candidate output does not equal allocated path outputs")
    if not checks:
        raise ValueError("report contains no selected routes")
    client = RpcClient()
    results = Multicall3(client, chunk_size=5).call(specs, block)
    for check, result in zip(checks, results, strict=True):
        check["raw_call"] = {"to": result.spec.to, "data": result.spec.data,
                             "success": result.success, "result": result.raw, "via": result.via}
        check["matched"] = False
        if result.success:
            amount_out, prices, ticks, gas = decode(
                ["uint256", "uint160[]", "uint32[]", "uint256"], bytes.fromhex(result.raw[2:]))
            check.update(quoter_out=amount_out, terminal_prices=prices, ticks_crossed=ticks,
                         quoter_gas_estimate=gas, matched=amount_out == check["expected_out"])
    output = ROOT / "data/validation/routes" / path.name
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"chain": block.chain, "block": block.number, "block_hash": block.hash,
               "report": str(path), "report_sha256": hashlib.sha256(raw_report).hexdigest(),
               "checks": checks, "network_requests": client.network_requests,
               "scope": "pool-disjoint QuoterV2 path checks; not atomic settlement"}
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "matched": sum(c["matched"] for c in checks),
                      "network_requests": client.network_requests, "evidence": str(output)}))
    return int(not all(check["matched"] for check in checks))


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
