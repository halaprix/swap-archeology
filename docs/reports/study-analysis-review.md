# Independent saved-study analysis review

Review date: 2026-09-08. Scope: `scripts/analyze_study.py`,
`tests/test_study_analysis.py`, the 195 reports in
`data/results/six-family-full-intermediates`, saved Aave references, and raw
cached snapshots. Reviewer writes are confined to this report. No reviewer RPC,
evidence edits, implementation changes, commits, or external writes occurred.

## Verdict

**PASS for the corrected bounded saved-result analysis.** All four findings below
were repaired by the integrator and independently rechecked. Execution price,
routing gain, oracle ratio/gap, corrected size deterioration, and marginal spots
match independent calculations for the saved dataset. This is a five-pin review,
not a complete historical-window or settlement acceptance.

## Findings resolved during review

1. **Size deterioration uses the wrong reference when the winning route changes.**
   `_solver_limits` includes the winning route's `search_info.search_limits` in
   the cohort identity. Equal configured search budgets can therefore fall into
   separate cohorts when one size selects the baseline incumbent and another
   selects a search route. This affects 16 published rows. At block 23549991,
   search for 100 WETH reports zero deterioration against itself; against the
   available 1 WETH quote its deterioration is exactly
   `638361431600 / 3397803529` bps, approximately 187.874733236. At block 24356381,
   1000 WETH search should likewise reference 1 WETH, producing
   `1944196671920 / 2372717541` bps, approximately 819.396594127, rather than zero.
   The repaired code groups by configured report budgets and retains the original
   winning-route diagnostics in the report. All 16 affected rows are corrected;
   the final analysis has 150 configured cohorts, not 164.
2. **Malformed route feasibility is accepted.** A non-null `best_split` with
   `feasible: false` or positive `residual_in` passes quote validation and is
   counted as a feasible route. All 195 actual saved candidates are feasible
   with the requested input; this is a validation defect rather than an observed
   invalid route in this dataset. Both candidate fields now require explicit
   boolean feasibility, integer zero residual, and exact full input spend.
3. **Boolean oracle identity is accepted.** `block.chain: true` compares equal
   to chain 1 in `load_oracle_reference`. Quote integer fields already reject
   booleans; oracle chain and block number now use the same strict validation.
4. **Quantitative Markdown needs solver/source coverage caveats.** All 65 saved
   search reports are truncated. The 65 old sampled-dual reports record 46
   converged optimizer statuses and 19 optimizer failures, with feasible
   recovered routes. These results do not qualify the newer solver revision.
   Selected families also include unsupported and unavailable families. The
   regenerated Markdown now states these counts, shows per-family usable-pool
   ranges/statuses, and includes the dataset README's old sampled-dual provenance.

## Independent arithmetic and provenance checks

The reviewer matched the original quote fields in every analysis row against
its corresponding saved report, then independently recomputed reduced integer
numerators and denominators. With raw input `x`, raw output `y`, token decimal
counts `d_in`, `d_out`, direct-pool output `b`, and Aave prices `P_in`, `P_out`:

- Execution price in output tokens per input token is
  `(y / x) * 10**d_in / 10**d_out`.
- Routing gain is `10000 * (y / b - 1)` when a positive direct reference exists;
  absent or zero denominators remain null. The 45 wrapper-pair rows without a
  direct baseline correctly have null routing gain.
- Oracle ratio is `P_in / P_out`; the common oracle base unit cancels. The oracle
  gap is `10000 * (1 - execution_price / oracle_ratio)`. This uses the observed
  stablecoin prices, including USDC above its nominal peg at block 23550060.
- Size deterioration is `10000 * (1 - current_price / smallest_input_price)`
  within the same chain/hash/pair/requested-solver/selected-family/configured-
  budget cohort. It includes fees and route changes, rather than isolating pool
  price impact.
- V2 direct marginal spot derives from raw reserve ratios. V3 derives from raw
  `sqrtPriceX96**2 / 2**192`. Token direction determines inversion, followed by
  decimal scaling. Cross-pool spread uses `10000 * (max_spot / min_spot - 1)`.
  These are fee-free marginal prices, not finite-size outputs.

The final independent replay matched **1,674 non-null reduced Fraction
metrics**, including **594 direct pool spots** and **150 spread calculations**;
45 rows correctly have no direct V2/V3 spot. All size references now match the
smallest input in each of the 150 independently checked configured-budget
cohorts. The two exact corrected examples in finding 1 also appear correctly in
the regenerated Markdown table.

For the five pins, **40 reference calls** match raw snapshot target/calldata,
success, and return bytes: ten pool/provider identity calls and thirty endpoint
price calls. The reviewer independently checked the `ADDRESSES_PROVIDER()` →
`getPriceOracle()` → `getAssetPrice(address)` call chain and decoded each price.
All 30 stored price values match their raw replies. These checks concern the
actual historical Aave reference path; they do not assume a USD denomination or
fair-value guarantee.

All five WETH/USDC price pairs and oracle addresses also match the preserved
`evidence/crash-rescue-simulation/standing-prices.json`, including block hashes.
This comparison reuses saved evidence; it is not a fresh reviewer RPC check.

`UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q
tests/test_study_analysis.py` passed **5 tests** after repair. The tests cover
mixed decimals, depegged oracle ratios, negative gains, no-route and zero-size-
reference behavior, wrong reference hashes, input-spend mismatch, malformed
reports, decimal drift, changed winning routes within a configured-budget
cohort, candidate completeness, and boolean oracle identities.

The reviewer independently replayed `analyze_directory` with RPC transport,
HTTP requests, and snapshot writes forbidden. Its complete result equals the
regenerated `data/analysis/saved-quotes/analysis.json`. All 195 CSV rows preserve
the corresponding JSON metric numerators, denominators, decimals, and nulls.
Twelve additional temporary malformed-field/count-mismatch checks rejected
invalid feasibility/residual/spend/boolean identity, or returned unavailable
when the report's usable-pool count differed from cached eligible state.

## Scope limits

The analysis covers five pins and 195 saved reports, with 65 reports per solver.
The search budget is finite, dual results belong to the saved older model, and
the admitted source universe is incomplete. Positive oracle availability is
not proof of an executable oracle-price route. Net-after-gas, atomic settlement,
global optimality, and full-window coverage remain unproven.

Marginal spots use the current local inventory against the pinned raw snapshot.
Usable-family count disagreement makes the metric unavailable. Matching counts
are a conservative consistency check, not a cryptographic identity commitment
to every pool admitted when the older report was generated.
