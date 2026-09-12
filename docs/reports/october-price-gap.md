# October 10, 2025: WETH/USDC market versus oracle

Update: [Independent V2/V3 contract, swap-event and external-market validation](october-independent-validation.md) now confirms the tested pool quotes. V2 does not remove the gap.

Requested interval: **21:14–22:05 UTC**. Delivered **86 observations**, every third
Ethereum block plus the final in-window block: **23549939–23550192**,
21:14:11–22:04:59 UTC. Each observation includes reconstructed aggregate and
reference Uniswap V3 execution prices for **1, 10 and 100 WETH**, V3 spot, the
standard Chainlink ETH/USD ÷ USDC/USD cross, and Aave's configured WETH/USDC cross.

[Interactive local chart](http://127.0.0.1:3007/october-gap) ·
[CSV](../../outputs/october-price-gap/prices.csv) ·
[JSON and provenance](../../outputs/october-price-gap/prices.json).
Keeper replay work is paused; the full sweep remains stopped.

## Findings

The difference is **not uniformly 5%, and not explained solely by stale oracle
updates or a large trade**. Public venue selection and trade size explain part
of it. A large remaining discrepancy persists even for small trades and a
same-block ETH oracle update. Its full economic cause is not yet established.

All prices below are USDC per WETH:

| UTC | V3 spot | Aggregate 1 WETH | Aggregate 100 WETH | Chainlink | Aave |
|---|---:|---:|---:|---:|---:|
| 21:14:11 | 3657.84 | 3750.50 | 3710.30 | 3716.85 | 3846.13 |
| 21:30:23 | 3345.71 | 3519.17 | 3359.01 | 3571.89 | 3571.03 |
| 21:35:23 | 3375.80 | 3420.09 | 3372.35 | 3765.11 | 3718.75 |
| 22:04:59 | 3907.55 | 3905.34 | 3884.60 | 3906.91 | 3933.00 |

Across the 86 samples, median differences measured as `(market/oracle - 1)`:

| Price measure | Versus standard Chainlink | Versus Aave |
|---|---:|---:|
| Reference V3 spot | -4.91% | -4.71% |
| Aggregate 1 WETH | -2.41% | -2.28% |
| Aggregate 100 WETH | -4.13% | -3.96% |

These are sample medians, not time-weighted averages. The three-block sample
can miss extrema: the retained every-block 100-WETH series reaches -10.83%
versus Chainlink at 23550046, versus -10.43% at the sampled block 23550044.

### What the decomposition establishes

1. **Venue selection matters.** At 21:30:23, the direct 0.05% V3 pool spot is
   3345.71; routing 1 WETH through V3 WETH/DAI → DAI/USDC produces 3519.17.
   The gap to Chainlink falls from 6.33% to 1.48%. Both are contemporaneous
   on-chain liquidity models, not a future-price comparison.
2. **Trade size matters.** At that same block, the aggregated price falls from
   3519.17 for 1 WETH to 3467.16 for 10 WETH and 3359.01 for 100 WETH.
   A reference price and a size-dependent executable price are different measures.
3. **Neither explains the whole discrepancy.** At 21:35:23, aggregate 1 WETH
   remains **9.16% below Chainlink**, versus 10.43% for 100 WETH. The standard
   ETH/USD feed's `updatedAt` equals that block's timestamp. Seven sampled
   observations have a >5% small-trade gap despite ETH feed age <=24 seconds.
   Same-block publication does not prove that underlying off-chain observations
   were instantaneous, but it rules out elapsed on-chain update age alone.
4. **The two oracle series use different sources.** Pinned `getSourceOfAsset`
   at the first, peak and last sampled blocks returns Aave WETH feed
   `0x5424384b256154046e9667ddfaaa5e550145215e`, whereas the standard public
   ETH/USD proxy is `0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419`.
   At 21:35:23 Aave's ETH feed reports $3717.98, aged 96 seconds; the standard
   feed reports $3764.31, aged zero. Aave uses `Capped USDC / USD` source
   `0x3f73f03aa83b2a48ed27e964ed0fdb590332095b`. Its `latestRoundData()` reverts,
   so no direct source age is asserted. Aave's resulting pair cross is 1.23%
   below the standard cross at this block, yet remains above the market quote.
   [Raw source identities](../../outputs/october-price-gap/aave-feed-identities.json).
5. **An old USDC timestamp is not evidence that ETH was stale.** ETH and USDC
   ages are exported separately. At the sampled peak, standard USDC/USD was
   0.99978685, a roughly 2.13 bps departure from $1, far too small to explain
   the roughly 900–1000 bps price difference. Its timestamp was 48,900 seconds
   old; the ETH timestamp was zero seconds old.

Chainlink aggregates multiple data sources and publishes periodic on-chain
updates; it is not an executable quote for this particular Ethereum pool
universe. See [Chainlink's feed and update documentation](https://docs.chain.link/data-feeds).
Determining whether the residual gap reflects off-chain/on-chain market
segmentation, observation latency, missing routes, or reconstruction error
requires additional evidence; the prices alone do not prove which dominates.
The next causal check is independent on-chain quoter parity at the worst blocks
and contemporaneous external spot-market data, alongside the coverage below.

## Additional pools and adapters worth checking

At sampled peak 23550044 the current model has **107 usable pools**: 27 V3,
33 V4, 30 Fluid, 10 V2, 4 Curve, and one each Lido, ARM and PSM. Counts include
other admitted tokens, not just WETH/USDC. More discovered pools are not
necessarily active, safe to model, or useful for this pair.

1. **Expand existing adapter inventories first:** direct WETH/USDC and
   WETH/USDT pools across V3 fee tiers, hookless V4 and supported Fluid T1;
   WETH/DAI and USDT/USDC or DAI/USDC connectors already demonstrably improve
   routes. Recheck excluded candidates at these blocks rather than claiming
   all discovery exclusions can be enabled. Current routing budgets also bound
   route quality; adding pools alone is not proof of optimality.
2. **Curve coverage:** more relevant USDT/USDC and DAI/USDC liquidity; only four
   Curve pools are usable at this pin. Pools using a different math family need
   their own validated semantics, not merely an inventory entry.
3. **Balancer V3 plus ERC-4626/buffers:** the selected
   `0x85b2b559bc2d21104c4defdd6efca8a20343361d` was initialized before October
   and can potentially add wrapped USDT/USDC/GHO connector liquidity. It needs
   a new adapter and shared-buffer accounting. The other selected pool,
   `0x1ea5870f7c037930ce1d5d8d9317c670e89e13e3`, was initialized at 23770901,
   **after this crash**, so cannot help. [Activation evidence](balancer-user-pools.md).
4. **Fluid DEX Lite** requires a separate model from existing T1. Investigate
   relevant historical pairs and their depth before implementing it. USDS routes
   through finite Sky conversion/PSM liquidity are another discovery candidate;
   current aggregator screenshots do not prove historical availability.
5. **RFQ/PMM:** potentially relevant for large sizes, but 0x/Bebop/1inch private
   quotes require historical quote archives. A pool adapter or settlement log
   cannot reconstruct unfilled private liquidity.

Useful addresses to supply are **Ethereum pools active before block 23549939**
for WETH/USDC, WETH/USDT, WETH/DAI and stable connectors among USDC/USDT/DAI/USDS.
Historical initialization, usable liquidity and conversion capacity must be
verified before counting a source. No new venue adapters were added in this pass.

## Reproduction and checks

Run `.venv/bin/python scripts/october_price_gap.py` from the repository root.
It reuses the dense 100-WETH results and offline collected state, computes/caches
1/10-WETH reports, and exports CSV/JSON. No additional market RPC is required.
Full raw reports are retained alongside block hashes and collection identities.
Copy `prices.json` and `prices.csv` from `outputs/october-price-gap/` to
`frontend/public/october-prices.json` and `frontend/public/october-prices.csv`.

The script checks all 258 quote reports for exact integer flow conservation,
complete input spending, feasibility, amount and block-hash correspondence.
Two extra small-size cases match the unoptimized Python solver's **entire
report**, recorded in `outputs/october-price-gap/parity.json`. Browser checks
cover size selection, the peak block and feed ages, visible chart strokes and
390px layout (`browser-check.json`). Frontend tests, typecheck and lint pass.

These are **collection-model-only** historical estimates: incomplete venue
coverage, finite tick windows and bounded route search. They are not independently
protocol-quoter-qualified at every sampled block. Execution prices include pool
fees and exclude gas, RFQ and counterfactual trade effects. V3 spot is fee-free,
computed from pinned sqrtPriceX96 in pool
`0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`; it is not a TWAP.
