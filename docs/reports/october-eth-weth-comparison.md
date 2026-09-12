# Native ETH and WETH → USDC, October 10, 2025

Compared **86 identical Ethereum block hashes**, every third block plus the
last in-window block, **21:14:11–22:04:59 UTC**, at 1/10/100 token sizes.
The requested interval is 21:14–22:05 UTC. Each price below is USDC per input
token, including modelled pool fees and excluding gas.

[Interactive comparison](http://127.0.0.1:3007/october-gap) ·
[CSV](../../outputs/october-eth-prices/prices.csv) ·
[JSON/raw report links](../../outputs/october-eth-prices/prices.json).

## Result

Native ETH routes sometimes improve the saved WETH quotes substantially, but
**do not explain the whole market/oracle discrepancy**. They are not consistently
better. This is a comparison of separate route graphs, **not an ETH/WETH depeg**.
The current graph lacks a direct ETH↔WETH wrap/unwrap edge; this pass measures
that coverage difference without adding the edge or changing the core solver.

| Size | Samples with better native ETH quote | Median ETH/WETH quote difference | Largest ETH advantage |
|---|---:|---:|---:|
| 1 token | 40 / 86 | -6.57 bps | +446.38 bps |
| 10 tokens | 24 / 86 | -28.73 bps | +235.43 bps |
| 100 tokens | 16 / 86 | -37.38 bps | +258.52 bps |

All 258 native quotes were feasible in the final dataset. Differences use
`ETH_quote / WETH_quote - 1`. These are equally weighted sample medians.
An eventual unified graph should admit both sets and wrapping; taking the best
of these two quotes is only a baseline, not a globally optimal split-route result.

### Selected 1-token prices

| UTC | Native ETH aggregate | WETH aggregate | Chainlink cross |
|---|---:|---:|---:|
| 21:14:11 | 3759.91 | 3750.50 | 3716.85 |
| 21:26:11 | 3556.28 | 3404.32 | 3642.66 |
| 21:30:23 | 3423.78 | 3519.17 | 3571.89 |
| 21:35:23 | 3425.82 | 3420.09 | 3765.11 |
| 22:04:59 | 3890.99 | 3905.34 | 3906.91 |

At 21:26:11 (23549999), native ETH improves the 1-token price by **4.46%**.
The route splits 0.9 ETH through V4 ETH/DAI → V3 DAI/USDC and 0.1 ETH through
V4 ETH/USDT → V4 USDT/USDC. Four distinct pools are used, and all four leg
outputs independently match the historical protocol quoters exactly.

At 23550125 the largest 100-token improvement is **2.59%**, using Fluid
ETH/USDC directly: **100 ETH → 384,023.548051 USDC**. Fluid's historical
pre-operation resolver independently matches this output.

At the previously inspected peak block 23550044 (21:35:23), native prices are
**3425.82 / 3378.70 / 3362.23** for 1/10/100 ETH respectively. The 1-ETH quote
is still approximately **9.01% below** the 3765.11 Chainlink cross. Native
liquidity improves this particular small quote by only 16.74 bps over WETH.
The 100-ETH quote is about 30 bps worse than the saved 100-WETH quote.

## Independent validation and limits

- **36 direct checks**: two V4 ETH/USDC pools and Fluid ETH/USDC, sizes 1/10/100,
  at 23550020, 23550044, 23550046 and 23550125. Every model output matches
  V4 Quoter or Fluid `estimateSwapIn` exactly.
- **Four additional quote checks** cover the complete distinct-pool 1-ETH route
  at 23549999. No pool is reused, so no repeated-pool state is silently ignored.
- Exact integer flow conservation and block/amount correspondence are checked
  for the saved native and WETH reports. Native quote generation is offline.
- Fluid checks establish pre-operation resolver equality, not full Liquidity
  settlement. None of these checks execute a funded atomic transaction.
- The aggregate search remains bounded, with incomplete venue coverage and
  finite state windows. These checks do not independently qualify all 258
  aggregate routes. No historical RFQ liquidity is invented.

## Reproduction

`.venv/bin/python scripts/october_eth_prices.py` generates the canonical 86-row
JSON/CSV and caches raw reports under `outputs/october-eth-prices/raw/`.
`--blocks` fills only selected raw caches and must not replace the full summary.
The input pins and saved WETH routes come from `outputs/october-price-gap/prices.json`.

`.venv/bin/python scripts/october_native_check.py` writes direct comparisons to
`outputs/october-native-validation/checks.json`.
`.venv/bin/python scripts/october_native_route_check.py` writes the improved
route's four quoter comparisons to `improved-route.json` in the same directory.
Both retain pinned raw RPC evidence in the output directory.

Run `.venv/bin/python scripts/export_october_eth_prices.py` to export compact
price-only JSON/CSV for the app; detailed diagnostics stay in research outputs.
The app loads `frontend/public/october-eth-prices.json` alongside its original
October data, joining by block and hash, and exports the combined native/WETH
CSV. No full sweep or keeper replay was restarted.
