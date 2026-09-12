# Uniswap V2

**Deployment.** Factory `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f`. Creation block **10000835, verified exactly**: code present at 10000835, absent at 10000834 (`data/discovery-evidence/identity/creation_univ2_factory.json`). Periphery `UniswapV2Router02 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D` is used only as an arithmetic cross-check.

**Status at the pins.** Code present at all five. `allPairsLength()` 461185 / 461185 / 465650 / 483824 / 520596.

**Discovery.** `eth_getLogs` on the factory, topic0 `0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9`, `fromBlock 10000835`. `PairCreated(address indexed token0, address indexed token1, address pair, uint256)` — topic1/topic2 are the tokens, so the scan can be token-filtered. `getPair(a,b)` is the cheap path for a known pair but proves nothing about others. A pair-filtered scan does not cover a token added later: backfill that token from 10000835.

## Quote semantics

State per block: `getReserves() -> (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)`, plus `token0()`/`token1()` cached from discovery.

There is **no on-chain quoter**. The trusted reference is `UniswapV2Library.getAmountOut`, which `UniswapV2Router02` exposes as a pure function:

```
amountOut = amountIn * 997 * reserveOut / (reserveIn * 1000 + amountIn * 997)
```

Integer floor, no rounding ambiguity. The 0.3% fee is hardcoded in `UniswapV2Pair`; a fork factory is a different fee and a different family.

Capacity is the reserve itself — the curve asymptotes, it does not clamp. No permissioned path exists; every pair is open.

**Trap.** Fee-on-transfer and rebasing tokens break the constant-product identity. None of the endpoint tokens are fee-on-transfer, but stETH rebases and must never be routed as a raw V2 reserve token.

**Discovery result.** Fourteen endpoint/connector pairs were found through calm block 25896003 across 56 ordered pair filters for eight tokens. The inventory retains canonical creation logs, coverage hashes, and incremental/new-token backfill boundaries. Ten pairs qualify at each of the first three pins and eleven at the last two; raw stETH and invalid/dust reserve models remain explicitly excluded. See `docs/reports/v2-review.md`.

**Scope.** Forks (Sushi, Pancake, …) are separate factories, out of this family's scope, and their fee constants must be read from their own pair bytecode rather than assumed.
