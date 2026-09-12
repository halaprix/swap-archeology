"""Compare the legacy 3pool model against cached or direct pool ``get_dy`` calls."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from eth_abi import decode, encode

from swaparch.adapters.curve_legacy_3pool import POOL, CurveLegacy3PoolAdapter
from swaparch.core.types import CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import pool_record_from_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/october-connectors"
BLOCKS = (23549939, 23550060, 23550192)
SIZES = (1, 100, 1_000)


def get_dy_spec(i: int, j: int, amount: int) -> CallSpec:
    return CallSpec(POOL, "0x5e0d443f" + encode(["int128", "int128", "uint256"], [i, j, amount]).hex(), "get_dy")


def main() -> None:
    client = RpcClient(cache_root=OUT / "rpc-cache")
    store = SnapshotStore(OUT / "snapshots")
    rows = []
    for number in BLOCKS:
        header = next((path for path in (OUT / "snapshots/1").glob("*/header.json")
                       if json.loads(path.read_text())["number"] == number), None)
        if header is None:
            raise ValueError(f"missing supplemental snapshot for {number}")
        snapshot = store.load(1, header.parent.name)
        record = pool_record_from_json(json.loads((OUT / "records" / f"{snapshot.block.hash}.json").read_text())["records"][0])
        state = CurveLegacy3PoolAdapter().load_state(record, snapshot)
        for i in range(3):
            for j in range(3):
                if i == j:
                    continue
                for size in SIZES:
                    amount = size * 10**record.tokens[i].decimals
                    spec = get_dy_spec(i, j, amount)
                    result = client.eth_call(spec.to, spec.data, snapshot.block)
                    if not result.success:
                        raise ValueError(f"get_dy failed at {number}, {i}->{j}, {size}")
                    reference = decode(["uint256"], bytes.fromhex(result.raw[2:]))[0]
                    quoted = state.quote_exact_in(record.tokens[i].address, record.tokens[j].address, amount)
                    executed = state.swap(record.tokens[i].address, record.tokens[j].address, amount)[0]
                    rows.append({"block": asdict(snapshot.block), "i": i, "j": j, "amount_in": amount,
                                 "reference_get_dy": reference, "model_get_dy": quoted,
                                 "model_exchange": executed, "get_dy_matched": quoted == reference,
                                 "exchange_minus_get_dy": executed - reference,
                                 "reference": asdict(result)})
    output = OUT / "qualification/curve-legacy-3pool.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"pool": POOL, "source": {
        "repository": "curvefi/curve-contract", "commit": "574f44027d089de0eac765f5a74ea5ae96aba968",
        "path": "contracts/pools/3pool/StableSwap3Pool.vy",
        "sha256": "03e0bf29ae2fe945a3629b8085062da8f9d9d25957e86001dfc3df68c895f9dc",
        "url": "https://github.com/curvefi/curve-contract/blob/574f44027d089de0eac765f5a74ea5ae96aba968/contracts/pools/3pool/StableSwap3Pool.vy"},
        "checks": rows, "matches": sum(row["get_dy_matched"] for row in rows), "total": len(rows)}, indent=1) + "\n")
    print(json.dumps({"output": str(output), "matches": sum(row["get_dy_matched"] for row in rows),
                      "total": len(rows), "network_requests": client.network_requests}))


if __name__ == "__main__":
    main()
