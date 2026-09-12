# Uniswap V4

**Deployment.** PoolManager `0x000000000004444c5dc75cB358380D2e3dE08A90` (singleton; pools are `bytes32` PoolIds, there is no per-pool contract). StateView `0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227`, V4Quoter `0x52F0E24D1c21C8A0cB1e5a5dD6198556BD9E1203`, PositionManager `0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e`. All three periphery contracts returned the PoolManager address from `poolManager()` at 25896003, which is the on-chain link between them.

**Activation.** Documented creation block 21688329 (2025-01-23); first `Initialize` at 21688545. PoolManager, StateView and V4Quoter all have code at **all five pins** — V4 is live for the whole study.

**Discovery.** `eth_getLogs` on the PoolManager, topic0 `0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438`, `fromBlock 21688329`.

```
Initialize(PoolId indexed id, Currency indexed currency0, Currency indexed currency1,
           uint24 fee, int24 tickSpacing, IHooks hooks, uint160 sqrtPriceX96, int24 tick)
```

**Exactly three indexed params** — `id`, `currency0`, `currency1`. `hooks` is *not* indexed in the final release; older draft ABIs indexed it and carried a `sender` param, and porting that code silently mis-decodes every log. Store the complete PoolKey; `poolId = keccak256(abi.encode(PoolKey))`.

## Quote semantics

State per block, all plain views on StateView and therefore Multicall3-safe:

- `getSlot0(bytes32) -> (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee)`
- `getLiquidity(bytes32) -> uint128`
- `getTickBitmap(bytes32 poolId, int16 wordPos) -> uint256` — note `int16`, the word position, not a tick
- `getTickLiquidity(bytes32, int24) -> (uint128 liquidityGross, int128 liquidityNet)`

Tick acquisition is a **dependent pass**: the bitmap tells you which ticks to fetch next, so it cannot collapse into one Multicall batch.

Trusted quote reference: `V4Quoter.quoteExactInputSingle((PoolKey, bool zeroForOne, uint128 exactAmount, bytes hookData)) -> (uint256 amountOut, uint256 gasEstimate)`. It is **not `view`** — it unlocks the PoolManager and decodes a revert. `eth_call` it individually; it is not safe inside a staticcall-shaped batch, and INTERFACES.md's batched-vs-individual parity rule must pass before anything batched is trusted.

Use `lpFee` from `getSlot0`, not the `fee` field in the PoolKey, whenever a dynamic-fee hook is present.

**User-accessible vs permissioned.** Swaps go through UniversalRouter or a direct unlock; nothing here is permissioned. The real gate is the hook.

**Open.** Pool list not scanned. Hook classification is the blocker for `supported`: only `hooks == address(0)` admits unmodified V3-style math, and a dynamic-fee pool (fee flag `0x800000`) cannot be priced from the static fee field at all. Native ETH (`currency0 == address(0)`) and WETH pools are different venues; keep them distinct.
