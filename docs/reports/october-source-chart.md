# October chart: every block and individual liquidity sources

Update: the public chart now uses the supplemented sell-side export described
in [October stablecoin connectors](october-stablecoin-connectors.md). The
original outputs below remain the before-change baseline.

The October comparison now covers **every Ethereum block** from **23549939 to
23550192**: 254 observations, October 10, 2025 **21:14:11–22:04:59 UTC**.
This is the full set of blocks inside the requested 21:14–22:05 interval,
usually 12 seconds apart. Each observation is an end-of-block snapshot, not
an intrablock replay. Prices are USDC per input token at sizes 1, 10 and 100.

[Local chart](http://127.0.0.1:3007/october-gap).

## What each line means

- **Uniswap V2, V3, V4 and Fluid best direct:** the highest available quote
  among that venue's direct ETH/USDC or WETH/USDC pools at that block and size.
  The winning pool can change between blocks. These are not optimal routed
  quotes for the entire venue.
- **Individual pools:** available under the pool controls, with pool identity,
  input token and available fee metadata. Each pool is evaluated independently
  from its unchanged block state. V4 fee metadata describes its configured LP
  fee; Fluid's state-dependent fee is not presented as a constant.
- **ETH and WETH aggregates:** separate all-supported-source, bounded multi-hop
  searches. The missing direct wrapping connection has not been added here.
- **Chainlink and Aave:** the same pinned pair cross-rates used in the earlier
  study. They are reference valuations, not guaranteed execution prices.

All 15 requested source families remain in the availability table. Families
with usable connector liquidity but no direct ETH/WETH→USDC pair do not get
invented ETH price lines. Unsupported and unavailable source status is shown
separately. Missing observations break plotted lines rather than becoming zero
or being carried forward. Small or exhausted pools may have very low real
execution quotes; enabling those pools can legitimately expand the y-axis.

Use the size selector, source checkboxes and individual-pool controls to compare
curves. Hover/click the chart or use the keyboard-accessible block slider to
inspect one block. Colours remain associated with each series; the chart has
UTC grid ticks and a larger plotting area.

## Reproduction and validation

Run `.venv/bin/python scripts/october_source_prices.py` for the bounded full
export. `--blocks` fills only selected raw caches and does not replace canonical
JSON/CSV. Existing quotes are reused only after checking their provenance,
including the archived dense-collector fingerprint for earlier 100-WETH quotes.
New quotes are calculated offline with the existing native benchmark scope and
the same search budgets. No full sweep, keeper simulation or new RPC acquisition
is started.

Data: `outputs/october-sources/prices.json` and `prices.csv`; compressed direct
quote evidence and aggregate reports are under `raw/` and `aggregate-raw/`.
The public chart copies are `frontend/public/october-sources.json` and `.csv`.

Run `.venv/bin/python scripts/validate_october_sources.py` to check the full
254-block sequence, canonical hashes/timestamps, raw output/price units,
explicit missing reasons and the bound that each aggregate is at least as good
as its available direct candidates for the same input and size. Aggregate raw
reports also undergo exact integer flow-conservation checks during generation.
Frontend helper tests check direct-pool selection and broken lines across gaps.
Browser evidence is stored in `outputs/october-sources/browser-check.json`.

The resulting chart remains **collection-model-only** with incomplete venue
coverage and bounded search. Prior independent contract checks cover selected
pools, blocks and sizes; this export does not promote all displayed observations
to independently contract-qualified results.

Completed checks: 11 direct pools; 8,203 positive pool quotes; all 1,524
aggregate observations present. Data validation, TypeScript, ESLint and all five
frontend test files pass. Browser checks cover all three sizes at block 23550044,
source and pool toggles, stable family colours and no document overflow at 390px.
