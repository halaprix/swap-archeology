"""Verification script for historical Fluid DEX resolvers across 5 crash dates.

Validates:
1. Deployment blocks and bytecode presence.
2. ABI compatibility of getPoolReservesAdjusted with existing POOL_RESERVES_TYPES.
3. Wei-exact parity between on-chain estimateSwapIn and adapter quote_exact_in.
"""

from __future__ import annotations

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from swaparch.adapters.fluid_dex import POOL_RESERVES_TYPES, FluidDexAdapter
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    PoolRecord,
    SupportStatus,
    Token,
)
from swaparch.rpc.client import RpcClient

PINS = [
    21762844,  # Feb 3, 2025 (Crash 1)
    21921840,  # Feb 25, 2025 (Crash 2)
    22215371,  # Apr 7, 2025 (Crash 3)
    22755534,  # Jun 2025 (Crash 4)
    23549973,  # Oct 2025 (Crash 5)
]

ETH_USDC_POOL = "0x836951eb21f3df98273517b7249dceff270d34bf"
FACTORY = "0x91716c4eda1fb55e84bf8b4c7085f84285c19085"
LIQUIDITY = "0x52aa899454998be5b000ad077a46bbe360f4e497"

RESOLVERS = {
    "EARLIEST_INITIAL": "0xF38082d58bF0f1e07C04684FF718d69a70f21e62",
    "EARLIEST_CANONICAL": "0xb387f9C2092cF7c4943F97842887eBff7AE96EB3",
    "OLD_INTERMEDIATE": "0xC93876C0EEd99645DD53937b25433e311881A27C",
    "NEW_CURRENT": "0x05Bd8269A20C472b148246De20E6852091BF16Ff",
}

DEPLOYMENT_BLOCKS = {
    "0xF38082d58bF0f1e07C04684FF718d69a70f21e62": 21578805,
    "0xb387f9C2092cF7c4943F97842887eBff7AE96EB3": 21596670,
    "0xC93876C0EEd99645DD53937b25433e311881A27C": 22487434,
    "0x05Bd8269A20C472b148246De20E6852091BF16Ff": 23881741,
}

class EphemeralSnapshot:
    def __init__(self, block: BlockRef, client: RpcClient):
        self.block = block
        self.client = client

    def has(self, spec: CallSpec) -> bool:
        return True

    def get(self, spec: CallSpec) -> CallResult:
        raw = self.client._rpc("eth_call", [{"to": spec.to, "data": spec.data}, hex(self.block.number)])
        return CallResult(spec, True, raw, "ephemeral")

def get_eth_usdc_record() -> PoolRecord:
    token_usdc = Token(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
    token_eth = Token(1, "0x0000000000000000000000000000000000000000", "ETH", 18)
    return PoolRecord(
        family="fluid_dex",
        chain=1,
        pool_id=f"fluid_dex:{FACTORY}:{ETH_USDC_POOL}",
        deployment=FACTORY,
        pool=ETH_USDC_POOL,
        tokens=(token_usdc, token_eth),
        config={
            "liquidity": LIQUIDITY,
            "fee": 1000,
        },
        created_block=21354360,
        discovered_by={"method": "canonical_discovery"},
        status=SupportStatus.SUPPORTED,
    )

def main() -> None:
    client = RpcClient()
    adapter = FluidDexAdapter()
    pool_rec = get_eth_usdc_record()
    pool_addr = pool_rec.pool

    sel_reserves = "0x" + keccak(text="getPoolReservesAdjusted(address)")[:4].hex()
    sel_estimate = "0x" + keccak(text="estimateSwapIn(address,bool,uint256,uint256)")[:4].hex()
    reserves_calldata = sel_reserves + abi_encode(["address"], [pool_addr]).hex()

    print("================================================================================")
    print("FLUID RESOLVER HISTORICAL AUDIT & VERIFICATION")
    print("================================================================================")

    print("\n1. Bytecode Existence & Deployment Boundaries")
    print("--------------------------------------------------------------------------------")
    for name, addr in RESOLVERS.items():
        ck = to_checksum_address(addr)
        dep_block = DEPLOYMENT_BLOCKS[ck]
        print(f"[{name}] {ck} (deployed at block {dep_block}):")
        for pin in PINS:
            code = client._rpc("eth_getCode", [ck, hex(pin)])
            has_code = len(code) > 2
            print(f"  Pin {pin}: {'PRESENT (' + str(len(code)) + ' bytes)' if has_code else 'ABSENT (0x)'}")

    print("\n2. Querying getPoolReservesAdjusted with canonical earlier resolver (0xb387...)")
    print("--------------------------------------------------------------------------------")
    earliest_resolver = RESOLVERS["EARLIEST_CANONICAL"]
    swap_test_amount = 1000 * 10**6  # 1,000 USDC

    for pin in PINS[:3]:
        block_ref = client.get_block(pin)
        snap = EphemeralSnapshot(block_ref, client)

        # 2a. Raw on-chain call
        raw_reserves = client._rpc("eth_call", [{"to": earliest_resolver, "data": reserves_calldata}, hex(pin)])
        decoded = abi_decode(POOL_RESERVES_TYPES, bytes.fromhex(raw_reserves[2:]))[0]
        p_addr, _t0, _t1, fee, center, _col, _debt, limits = decoded

        print(f"\n--- PIN {pin} ({block_ref.timestamp}) ---")
        print(f"  Resolver: {earliest_resolver}")
        print(f"  Raw Call Data: {reserves_calldata}")
        print(f"  Raw Return Bytes: {len(raw_reserves)} chars ({raw_reserves[:42]}...)")
        print(f"  Decoded: pool={p_addr}, fee={fee}, centerPrice={center}")
        print(f"  Withdrawable token0/1: {limits[0][0]} / {limits[1][0]}")
        print(f"  Borrowable token0/1:   {limits[2][0]} / {limits[3][0]}")

        # 2b. Adapter state decode & local quote
        orig_resolver_fn = adapter._resolver
        adapter._resolver = lambda p, b: earliest_resolver
        try:
            state = adapter.load_state(pool_rec, snap)
            local_out = state.quote_exact_in(pool_rec.tokens[0].address, pool_rec.tokens[1].address, swap_test_amount)
        finally:
            adapter._resolver = orig_resolver_fn

        # 2c. Canonical on-chain estimateSwapIn comparison
        estimate_data = sel_estimate + abi_encode(["address", "bool", "uint256", "uint256"], [pool_addr, True, swap_test_amount, 0]).hex()
        raw_estimate = client._rpc("eth_call", [{"to": earliest_resolver, "data": estimate_data}, hex(pin)])
        onchain_out = int(raw_estimate, 16)

        print("  Swap Check (1,000 USDC -> ETH):")
        print(f"    Local Adapter Quote:   {local_out} wei ({local_out / 1e18:.6f} ETH)")
        print(f"    On-chain Quoter Value: {onchain_out} wei ({onchain_out / 1e18:.6f} ETH)")
        assert local_out == onchain_out, f"Parity mismatch at pin {pin}: {local_out} != {onchain_out}"
        print("    Parity: WEI-FOR-WEI EXACT MATCH (Delta: 0 wei)")

    print("\n================================================================================")
    print("All 3 early crash dates successfully validated against 0xb387f9C2092cF7c4943F97842887eBff7AE96EB3!")
    print("================================================================================")

if __name__ == "__main__":
    main()
