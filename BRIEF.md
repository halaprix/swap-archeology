# Historical routing and liquidity study

Initial project brief, updated source scope — 2026-09-07. Status: design and reuse inventory only; no new route quotes, pool ranking or routing implementation yet. Research-only. No production changes, live transactions, commits or pushes.

## The question

At each block during the earlier crashes, how much could someone receive by selling a fixed amount through the selected public on-chain liquidity sources? Compare the earlier single-pool route with the best tested single route and a split across routes. This measures how much of the apparent exit problem was a venue-selection problem, and how much persisted across the selected market.

A route optimizer compares actual output for a trade size. Averaging pool prices cannot answer this question. Pools need not agree during stress, and quoted output changes as a trade consumes liquidity.

## How an aggregator works

1. Discover pools and other immediately usable conversions.
2. Represent tokens as nodes and directed conversions as connections. Each connection returns an amount-dependent quote, not one fixed price.
3. Quote direct paths and paths through intermediate tokens. Try splitting the input across independent routes.
4. Compare total output, accounting for route fees and optionally gas. Building a transaction that executes the chosen route is a separate component.

Uniswap's open-source smart-order-router already demonstrates route search, split allocation and gas-aware ranking. Inspect and reuse suitable quote/routing components rather than building another full swap product. Its coverage alone does not supply Curve or PSM integration. [Source](https://github.com/Uniswap/smart-order-router).

## Initial scope

Ethereum mainnet. Requested endpoints: USDC, USDT, DAI, WETH, wstETH and sUSDe. USDT is confirmed by the follow-up route examples. The detailed source-discovery and Multicall requirements are in [SOURCE_DISCOVERY.md](SOURCE_DISCOVERY.md). ETH and stETH are allowed intermediate assets, because wrapping conversions can connect otherwise missed routes. Consider USDS as an intermediate only if the historical PSM path actually requires it; verify deployment and conversion availability at each event.

Sources: Fluid, Curve, Uniswap V2/V3/V4, Spark, Maker/Sky PSM, Lista Stable, Ekubo, Balancer V3, issuer-specific ERC-4626 conversions, Lido and Origin ARM. Bebop and 0x RFQ are included in the feasibility inventory; complete historical offered liquidity requires external quote archives. Duplicate names in the requested list are deduplicated, while deployments and versions remain separate. These are candidate sources, not a claim to exhaustive market coverage. Discover pools that existed before each event, rank candidates using pre-event activity and executable depth, retain the original pool as the baseline, and record exclusions and measured coverage. Do not select historical winners from today's popularity ranking. If omitted venues have material historical activity, report that and expand the source set before making market-wide claims.

For wstETH, include direct pools and the conversion wstETH -> stETH -> ETH -> WETH or a stablecoin, wherever the chosen pools support it. Unwrapping produces stETH, not instantly withdrawable ETH at the staking exchange rate. The stETH-to-ETH leg needs its own market quote. [Lido wrapper source](https://docs.lido.fi/contracts/wsteth/).

The PSM is a conversion facility, not an ordinary LP curve. Use its historical version, fees, enabled directions and actual usable capacity. The original PSM's debt-ceiling constraints differ from LitePSM's pre-minted DAI and stablecoin inventory. Never model either as unlimited 1:1 liquidity. [Original PSM](https://github.com/sky-ecosystem/dss-psm), [LitePSM](https://github.com/sky-ecosystem/dss-lite-psm).

Priority paths are WETH <-> USDC and WETH <-> USDT <-> USDC; wstETH <-> WETH <-> USDC with optional USDT in the stablecoin leg. Keep stETH/ETH wrapping paths and Origin Lido ARM as additional candidates. Add sUSDe stablecoin exits through discovered markets, including USDe or the previously studied sDAI/DAI connection when supported. A cooldown-enabled sUSDe redemption is not an instant hop. Use the historical cooldown settings and actual conversion capacity.

Discover each source once into a file cache with explicit block/filter coverage, then refresh only missing ranges. New-token discovery reuses a full inventory or backfills new filters. Per-block liquidity stays in a separate cache. Later acquisition targets one pinned Multicall per block where feasible; dependent quotes, tick discovery and provider limits may need additional batches. See SOURCE_DISCOVERY.md for the per-source methods, limitations and status.

## Measurement method

Begin with the five existing pins: 23549991, 23550060, 24356381, 23728292 and calm control 25896003. Then cover every block in the previously saved crash windows. Resolve and save the exact window endpoints from existing artifacts before acquisition. Preserve block hashes, UTC times, pool identities and missing-data classifications. Block-end observations do not recreate an earlier price excursion inside the block.

The first comparison is WETH -> USDC with identical explicit swap inputs across venues. Add a fixed token-unit size ladder spanning small through large trades for each requested pair and direction. Publish all input quantities; dollar-equivalent labels must state their reference and never assume every stablecoin equals one dollar.

Obtain historical outputs through the appropriate pool quote methods or verified protocol math. Reuse the existing archive client, cache and raw-data capture after inspecting their live source. Pin every route leg to the same block. Concentrated-liquidity pools require ticks and liquidity across the price range, or a historical quoter that traverses them; pool TVL or slot0 alone cannot establish finite-size depth. Curve quoting must match its pool implementation and rate/fee settings; one generic constant-product formula is inadequate. [Curve's StableSwap model](https://docs.curve.finance/assets/pdf/whitepaper_stableswap.pdf).

All available sources are considered together. The preferred research design is pool-level flow optimization with shared state/capacity, permitting splits and merges across sources. The earlier three-disjoint-route cap is superseded. Short paths remain useful baselines and candidate seeds, not an exhaustive route whitelist. [ROUTING_ALGORITHM.md](ROUTING_ALGORITHM.md) proposes a modular convex-flow core for supported AMMs, bounded search for custom/discrete behavior, and a common ordered integer-accurate evaluator. This is a design proposal; no implementation or global-optimality claim exists yet. Every source's support status, search restriction and historical-data gap stays visible.

Cache immutable historical responses by chain, block hash and call identity. Acquire serially within provider limits. Prefer verified native quote implementations over reproducing every AMM. No new funded workflow is part of this initial quote study; prior tool restrictions remain respected.

## What to report

For each block, pair, input size and tested routing option, save output amount, average output/input price, pool fees as included by the quote, route allocation and availability. Compare single-pool, best tested single path and best tested split. Report output before gas; any gas-adjusted comparison must label gas estimates and historical gas-price assumptions separately from measured execution gas.

Keep three different comparisons visible: venue disagreement before the trade; extra deterioration from trade size (plus fees when they cannot be separated); and the gap against the actual Aave collateral/debt price ratio. Chainlink may be an additional independently identified reference, not a replacement name for Aave prices. Do not simply add percentages with different denominators.

Chart each crash separately: time along the bottom and output-token-per-input-token price vertically, one stable color per source/route, separate panels or controls for input size. Add a size-versus-output curve at the worst blocks and a chart of the gain from routing across sources. Threshold recovery times mean the first observed block the tested quote passes, plus subsequent failures; they do not guarantee transaction inclusion or execution latency. Show acquisition gaps and failures explicitly.

## Reuse and validation

Reuse only evidence included in the publication or available from the cited public sources. Validate raw inputs before reuse and keep unpublished research artifacts outside the source checkout.

First reproduce the existing five-block single-pool values. Check unit/decimal conversions, unavailable directions and capacity limits. The split search must retain the unsplit candidate and never choose less output before gas; allocation must sum exactly to the input. Compare selected quotes with independent protocol evidence where available. Preserve any quote-versus-settlement uncertainty: composed historical quotes do not prove an atomic transaction.

Deliver a source manifest, reproducible block-by-block results and a human-readable chart report. Conclusion: how much routing improved available output, what sizes still failed a stated floor, and which omitted sources or execution assumptions limit that answer. Without an external quote archive, the study cannot reconstruct private RFQ liquidity, solver inventories or a commercial aggregator's historical internal choices; their feasibility status remains visible in the source inventory.
