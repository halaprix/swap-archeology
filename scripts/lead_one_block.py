"""Lead integration check: acquire one block for every live Uniswap V3 pool and quote.

usage: uv run python scripts/lead_one_block.py <block_number>
"""

from __future__ import annotations

import sys
from decimal import Decimal

from swaparch.adapters.uniswap_v3.adapter import UniswapV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory, load_states, pools_live_at

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def main(number: int) -> int:
    client = RpcClient()
    block = client.get_block(number)
    all_records = load_inventory("uniswap_v3")
    records = pools_live_at(all_records, number)
    adapters = {"uniswap_v3": UniswapV3Adapter()}
    snapshot, rep = acquire(adapters, records, block, SnapshotStore(), client)
    print(f"block {block.number} {block.hash} pools_live={rep.pools}/{len(all_records)} "
          f"phases={rep.phases} specs={rep.specs_total} network_requests={rep.network_requests}")
    states, unsupported = load_states(adapters, records, snapshot, rep.unsupported)
    print(f"states={len(states)} unsupported={len(unsupported)}")
    for rec, why in unsupported:
        print("  unsupported", rec.pool, why)
    sizes = [10**17, 10**18, 10**19, 10**20, 10**21]
    print("WETH->USDC exact-in by pool (USDC per WETH average):")
    for st in states:
        addrs = {t.address for t in st.tokens()}
        if addrs != {WETH, USDC}:
            continue
        row = []
        for amt in sizes:
            try:
                out = st.quote_exact_in(WETH, USDC, amt)
                row.append(f"{Decimal(out) * Decimal(10**12) / Decimal(amt):.2f}")
            except Unsupported as exc:
                row.append(f"UNSUPPORTED({str(exc)[:30]})")
        print(f"  {st.record.pool} fee={st.record.config['fee']:>5}  " + "  ".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
