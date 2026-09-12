# Balancer V3

**Deployment.** Vault `0xbA1333333333a1BA1108E8412f11850A5C319bA9` (documented creation block 21332121, 2024-12-04). Read on chain at 23549991 / 24356381 / 25896003: `getVaultExtension()` `0x0E8B07657D719B86e06bF0806D6729e3D528C9A9`, `getVaultAdmin()` `0x35fFB749B273bEb20F40f35EdeB805012C539864`, `getProtocolFeeController()` `0x212F884252792ebaaA811FB0678444b21c7C2879`, `isQueryDisabled()` **false** at all three.

Routers (all present at every probed pin): Router v2 `0xAE563E3f8219521950555F5962419C8919758Ea2`, BatchRouter `0x136f1EFcC3f8f88516B9E94110D56FDBfB1778d1`, AggregatorRouter `0x309abcAeFa19CA6d34f0D8ff4a4103317c138657`, AggregatorBatchRouter `0xDADa7bE438bdD89416F4802B679E320b15c92D49`, BufferRouter `0x9179C06629ef7f17Cb5759F501D89997FE0E7b45`.

**Factory version trap — verified on chain.** `getPoolCount()` at 23549991 / 24356381 / 25896003:

| factory | 23549991 | 24356381 | 25896003 |
|---|---|---|---|
| StablePoolFactory v3 `0x4eFcd8bc…` | no code | 1 | 265 |
| StablePoolFactory v2 `0xe42C2E15…` | 38 | 63 | 63 |
| StablePoolFactory v1 `0xB9d01CA6…` | 38 | 38 | 38 |
| WeightedPoolFactory v2 `0x332694Ef…` | no code | 2 | 79 |
| WeightedPoolFactory v1 `0x201efd50…` | 130 | 191 | 191 |
| StableSurge v3 `0x187a05fb…` | no code | 1 | 37 |
| StableSurge v2 `0x355bD33F…` | 44 | 53 | 53 |
| StableSurge v1 `0xD53F5d8d…` | 15 | 15 | 15 |
| GyroECLP v2 `0x04d58419…` | no code | 1 | 30 |
| GyroECLP v1 `0xE9B0a3bc…` | 182 | 963 | 1476 |
| ReClamm v3 `0x3ccD7868…` | no code | no code | 16 |

Old factories keep their old pools, so a factory-enumeration run must use the per-pin set.

**Discovery.** `eth_getLogs` on the Vault, topic0 `0xbc1561eeab9f40962e2fb827a7ff9c7cdb47a9d7c84caeefa4ed90e043842dad`, `fromBlock 21332121`, for `PoolRegistered(address indexed pool, address indexed factory, TokenConfig[] , uint256 swapFeePercentage, uint32 pauseWindowEndTime, PoolRoleAccounts, HooksConfig, LiquidityManagement)`. topic1 = pool, topic2 = factory: **factory-filterable, not token-filterable.** Registration is not liquidity — also scan `PoolInitialized(address indexed pool)`, topic0 `0xcad8c9d32507393b6508ca4a888b81979919b477510585bde8488f153072d6f3`, and treat registered-but-uninitialised as zero depth. Per-pin `getPools()` on each factory is cheaper but misses pools whose factory was later disabled, so the log scan is authoritative.

## Quote semantics

Trusted reference: `BatchRouter.querySwapExactIn(SwapPathExactAmountIn[] paths, address sender, bytes userData)` or `Router.querySwapSingleTokenExactIn(pool, tokenIn, tokenOut, exactAmountIn, sender, userData)`.

Neither is `view`. They re-enter the Vault through `IVaultExtension.quote(bytes)`, which gates on `EVMCallModeHelpers.isStaticCall()` and therefore only works from an off-chain `eth_call`. They **return normally** — this is not Balancer V2's revert-encoded query; `quoteAndRevert` is the separate revert variant. Check `Vault.isQueryDisabled()` first. Inside Multicall3 they are non-view and run under the Multicall3 sender, so the batched-vs-individual parity check is mandatory.

**Shared capacity.** A boosted pool routes through ERC-4626 buffers held by the Vault. Two different pool paths can consume the same buffer, and the same wrapper also appears in the `erc4626` family. These must share one `capacity_id` or a split route double-counts. This is the single biggest correctness risk in this family.

**Open.** Pool list not scanned. Hook contracts (StableSurge and others change the effective fee dynamically) must be classified; an unmodelled hook keeps a pool `discovered_unsupported`.
