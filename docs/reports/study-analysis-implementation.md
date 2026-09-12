# Saved quote analysis

`scripts/analyze_study.py` performs a bounded, offline pass over actual
swaparch quote JSON reports. It ignores summary JSON objects that have no
`request`, preserves each complete input report in `analysis.json`, and writes
the derived flat rows to `analysis.csv` plus a human summary in `report.md`.

Run it with:

```bash
uv run python scripts/analyze_study.py \
  --quotes data/results/six-family-full-intermediates \
  --oracle data/validation/aave-reference \
  --output data/analysis/saved-quotes
```

Execution price, routing gain, oracle discount, and size deterioration are
computed with `fractions.Fraction`; each result stores an exact reduced
numerator/denominator and a decimal display string. Execution price is
`amount_out * 10**decimals_in / (amount_in * 10**decimals_out)`. Routing gain
compares `best_split.amount_out` with a positive
`single_pool_baseline.amount_out`. The oracle ratio uses positive available
prices from the Aave reference whose chain, block number, and block hash match
the quote exactly. Missing references, missing routes, and non-positive
baselines produce null derived metrics.

Size deterioration groups by chain, block hash, token addresses, requested
solver, and selected source families. It uses the smallest observed input in
that group as the reference and therefore includes fees and route changes; it
is not an isolated price-impact estimate. The size group identity also includes
both report and route `search_limits`, so different solver budgets cannot be
silently compared as size effects. The report records only pins and scenario
labels actually present, sets `full_window_claim` to false, and says that
post-gas net output is unknown.

When the matching `SnapshotStore` cache and inventory are available, the
analyzer loads eligible V2/V3 states through `swaparch.universe` without RPC.
It reports direct-pool marginal spots only: V2 uses `Rout/Rin`, V3 uses
`sqrtPriceX96**2 / 2**192` with reverse-direction inversion and raw-decimal
scaling. It requires positive reserves/liquidity, compares the actual loaded
state count with each quote report's usable-pool count, and marks the result
unavailable on disagreement. The spread is fee-free and is not a finite-size
quote or a marginal model for other families.

Non-summary malformed JSON is rejected. Token decimal drift across reports and
oracle token chain/decimal metadata mismatches are rejected rather than mixed.

The focused tests cover mixed 6/18-decimal units, a depegged oracle ratio,
positive and negative routing gain, no-route nulls, strict oracle block
identity, strict quote request identity, malformed-report rejection, and
cross-report decimal drift rejection.
