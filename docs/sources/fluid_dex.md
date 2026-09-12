# Fluid DEX

**Deployment.** DexFactory `0x91716C4EDA1Fb55e84Bf8b4c7085f84285c19085` (documented creation 21015648). Liquidity layer `0x52Aa899454998Be5b000Ad077a46Bbe360F4e497`.

**The version trap — verified on chain.** There are two `FluidDexReservesResolver` deployments and they do not cover the same blocks:

| resolver | 23549991 | 23550060 | 23728292 | 24356381 | 25896003 |
|---|---|---|---|---|---|
| `0xC93876C0EEd99645DD53937b25433e311881A27C` (older) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `0x05Bd8269A20C472b148246De20E6852091BF16Ff` (current) | — | — | — | ✓ | ✓ |
| `0x11D80CfF056Cef4F9E6d23da8672fE9873e5cC07` (DexResolver) | — | — | — | ✓ | ✓ |

The runner **must** select `0xC93876C0…` for the first three pins, or every early-pin Fluid read silently returns empty rather than erroring. Pool counts agree across resolvers where both exist: `totalDexes()` / `getTotalPools()` = 39 / 39 / 42 / 43 / 50 at the five pins (48 at 25760917).

`FluidDexLiteResolver 0x12a47cEB96A952E8D4A6eA9FE3b40b79bbaeb4e9` has code at all five pins but is a **separate product** not counted by the DexFactory; its pool set and math are unresolved.

**Discovery.** Preferred: `getTotalPools()` then `getAllPools()` on the version-correct resolver, or `getPoolAddress(uint256)` / `getPoolTokens(address)` per id. Event fallback: `LogDexDeployed(address indexed dex, uint256 indexed dexId)`, topic0 `0x80d4769bbf5966f1c91cdab7c477bd8f74016bd5f5ed3ad18af6b32e29f6da7f`, `fromBlock 21015648`.

## Quote semantics

Trusted reference: `estimateSwapIn(address dex, bool swap0to1, uint256 amountIn, uint256 amountOutMin) -> uint256 amountOut` (and `estimateSwapOut`), on either resolver.

It is declared `public payable returns` — **not a view**. It must be run as an `eth_call` simulation, and it is caller-sensitive: inside Multicall3, `msg.sender` is the Multicall3 contract. Compare batched against individual reads before trusting it, per INTERFACES.md. The same applies to `getDexCollateralReserves`, `getDexDebtReserves` and `getPoolsReserves`, which are also non-view. `getDexLimits(address)` *is* a view and carries the withdraw/borrow caps that bound a swap.

State per block: a Fluid DEX pool is backed by the Liquidity layer, **not** by its own token balances — `balanceOf` on the pool is meaningless. Collateral reserves and debt reserves are separate curves and one swap can route through both. Read collateral reserves, debt reserves, the prices/exchange-prices struct and the swap limits at the same block. `*Adjusted` variants normalise reserves to 1e12 decimals.

**Open.** Pool list not enumerated (39–50 pools expected per pin). DEX Lite unresolved. Fluid DEX V2 is documented only on Polygon; re-check before the final run.
