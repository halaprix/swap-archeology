# Independent loaded-domain V3 continuous-model review

2026-09-08. This review owns this report only. It used local canonical Solidity,
qualified cached states, independent arithmetic, and offline tests. It performed
no RPC, source/data mutation, or historical acquisition.

## Verdict and scope

The corrected continuous V3 interval/support calculation passes the bounded
independent checks below. Its outer numerical contribution is now separate from
integer recovery. This closes the previously sampled-only V3 local-model gap;
it does not complete phase 3 or establish a continuous model for every admitted
source, continuous flow recovery, global optimality, or an upper bound.

At this review stage the V2 contribution remains the value of a rounded exact
proposal, while V3 contributes its continuous numerical support. The combined
outer objective must therefore retain its explicit mixed-model label. Exact
plans remain the responsibility of funded search and fresh evaluation. A
continuous optimum, including one with no useful integer proposal, is not an
executable trade.

The existing 195-report six-family matrix predates this extension. Its code
identity is recorded in `data/validation/six-family-matrix-code.json`, including
the old dual module hash
`2dedade81715813228da74ea4fbd4c1a1aea21e9532fcfa3bcd68d510746ffa0`.
That matrix is not benchmark evidence for the new continuous V3 model. A new
code-identified benchmark and the other gaps in `general-solver-review.md` remain
separate work.

## Canonical evidence

Read the actual `UniswapV3Pool.swap` and `SwapMath.computeSwapStep` sources in
`<external-repos>/uniswap-v3-core/contracts/`. This checkout has a
CodeGraph directory; the required first CodeGraph exploration failed with
`unable to open database file`, so the named source files were read directly.
Their observed SHA-256 identities are:

| Source | SHA-256 |
|---|---|
| `UniswapV3Pool.sol` | `d515775b7f3ffe921dd70aca86b8bad16280fa4c122425d82b4dbea4dc564a7a` |
| `libraries/SwapMath.sol` | `d6cb9a153be4ea9fb2377ef88641ef7979b5cee6933162f1b732d0289e26e1b6` |

The canonical swap loop clips targets to the supplied price limit, continues
while input remains, and applies the direction-specific liquidity-net sign only
when it reaches the actual tick price. At zero liquidity both token deltas and
the fee are zero, so price can advance through known empty spans to an
initialized tick. Unloaded bitmap words and unknown liquidity-net values are
not evidence of empty liquidity.

## Findings corrected during review

1. **Continuous support was discarded by integer recovery.** The initial outer
   objective summed `_Action.value` from the floored/clamped exact quote instead
   of the calculated continuous support. For Q96 price, tick 0, L=100, fee 500,
   spacing 1, empty loaded words −1/0 and raw-unit prices 1/2, the continuous
   optimum is gross input 1.28880664786904423, output 1.27177965914223495, and
   support 1.25475267041542567. The integer proposal is input 1/output 0/value −1;
   the original `_support` consequently returned no local contribution. The
   corrected `_LocalSupport` retains the continuous value, compares directions
   by that value, and allows an absent recovery action. Zero-output proposals
   are omitted. Diagnostics and documentation distinguish rounded V2 support.
2. **Known zero-liquidity spans were incorrectly terminal.** Starting at Q96,
   tick 0, L=0, fee 500 and spacing 1, use initialized ticks
   `{−10: −10^18, −100: +10^18}` downward or
   `{10: +10^18, 100: −10^18}` upward, with words −1/0 loaded. The exact adapter
   accepts input 10^12 and outputs 998500052003 in either direction. Initially
   the continuous domain was empty with `zero_liquidity_gap`. The corrected walk
   skips only the zero-flow span, applies the known tick update, and includes
   later positive-liquidity intervals. It still stops at missing evidence.
3. **Protocol extremes overshot and looped.** With a positive-liquidity state at
   tick MIN+1 or MAX−1 and the corresponding empty bitmap word, the initial walk
   reached the TickMath endpoint and ran to `walk_limit` after 100,000 steps.
   The corrected endpoint is exactly MIN_SQRT_RATIO+1 = 4295128740 downward, or
   MAX_SQRT_RATIO−1 = 1461446703485210103287273052203988822378723970341 upward.
   Both independently retested fixtures terminate after one interval with
   `protocol_price_limit`, without crossing an initialized tick past that limit.
4. **Import changed the caller's Decimal context.** Importing the original
   module changed precision from 28 to 96. The corrected implementation uses
   scoped `localcontext`; import, normal evaluation, and an arithmetic exception
   preserve the caller's precision.
5. **Approximate cache keys erased numerical derivatives.** Rounding token
   prices to 12 decimal places makes nearby log-price probes share an objective
   value near small token prices. The corrected cache uses exact log-price
   tuples. Independently invoking the actual objective with p=10^−6 and a
   10^−8 log-price increment changes the objective by 0.0100000009406358 for the
   large-input tiny-pool fixture, exactly matching an uncached calculation.
6. **A probe budget was not necessarily a domain maximum.** The existing
   exact-quote helper can stop after 32 successful doublings, without finding a
   rejected input. The corrected helper and profile distinguish
   `verified_lower_bound_doubling_budget` from `exact_accepted_prefix_cap`.
   Independently, calm USDC→WETH with seed 1 returns 4294967296 with the lower-bound
   status, and amount-plus-one is still accepted. Seeds 10^6 and 10^18 both find
   33039737212565 with the exact-prefix status; that input is accepted and
   amount-plus-one is excluded. This preserves bounded probing without claiming
   that every resulting recovery cap is the maximum loaded-domain input.

## Independent arithmetic and boundary checks

The reference segment construction enumerates set bits directly from each
loaded bitmap, sorts initialized ticks in price order, and applies signed
liquidity-net updates. It does not call the implementation's bitmap-walk helper.
Adjacent equal-liquidity segments are merged before comparison, so harmless
word-edge partitioning cannot manufacture agreement.

For synthetic fixtures, independently specified positive-liquidity intervals
include a current initialized boundary, subsequent changes in liquidity, a
zero-liquidity gap, and a later active interval. Rational target prices set the
stationary point at three interior positions in each segment. Independent
Fraction arithmetic calculates cumulative input/output, using
`L*(a-b)/(a*b)` instead of reciprocal subtraction for downward input or upward
output. Twenty-three checks pass, including the stationary price inside the
zero-flow jump, already-crossed tick −1 versus tick 0 sign behavior, and both
directions of the no-trade fee band. Gross input, output, and objective agree
within relative 10^−70, with the selected Q96 price within one integer unit.

The same independently enumerated domains match **280 directions** across all
qualified V3 states at the five canonical pins and the additionally cached
25760917 observation. Independent rational-form totals match **836 support
checks** using first, middle and last positive-liquidity intervals where
distinct. These checks use Decimal precision 120 in the reference and tolerance
10^−65 relative to scale; they do not establish outward numerical enclosures.

At the five canonical pins only, independent integer arithmetic calculated
per-interval rounded-up input, rounded-down output and rounded-up fee. These
amounts were accumulated to selected first/last initialized ticks and loaded
endpoints, then replayed against the exact adapter. **685 endpoint inputs**
matched both the independent output and endpoint price; all **685 amount-minus-one**
checks passed. Of **685 amount-plus-one** checks, 451 remained accepted and 234
correctly reached a documented coverage or capacity exclusion. This is actual
boundary arithmetic, not merely testing the presence of an initialized tick.

## Validation commands

Final reviewed SHA-256 identities:

| File | SHA-256 |
|---|---|
| `solver/v3_continuous.py` | `e4e0c3d666d5ae11f75e959a60c861efb9bf1ac57bacb35524b3e38a8c233310` |
| `solver/dual.py` | `856dae27ba31fe150ba25681b22a674abc2682b43edfcbc9dd75270634763190` |
| `tests/test_v3_continuous.py` | `aa2a436c5ad16b18aef72701cba18869f222043df8b7a269bf2bc810396eed32` |

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest \
  tests/test_v3_continuous.py tests/test_general_solver.py tests/test_evaluator.py -q
38 passed in 3.56s
```

Independent scripts were run with `uv run --offline python` from `/tmp`:
`v3-continuous-reference.py`, `v3-continuous-cache-reference.py`, and
`v3-continuous-boundary-reference.py`. Their outputs were respectively 23
synthetic checks, 280 domains/836 supports, and 685 exact endpoints with the
minus-one/plus-one breakdown above. The reference formulas, coverage and
tolerances are recorded here so these transient scripts are not mistaken for
committed acceptance fixtures.

A bounded model-only timing check used the 23 calm V3 states, one initial pass
and ten subsequent price-vector passes. Domain construction and exact cap
probing made the first pass 1.595 seconds; subsequent passes averaged 0.01010
seconds with those caches populated. This checks the intended cache reuse, not
optimizer runtime, route quality, or performance superiority. A full new-model
benchmark remains required.

## Remaining numerical/recovery limits

This is continuous support over loaded, known V3 state, not the entire pool or
a proof that arbitrary repeated/reversed visits are contained in one aggregate
trade model. Decimal results enter a floating-point optimizer and have no
outward-error certificate. The optimizer's positive bounded log-price domain is
an experimental restriction, and numerical success alone does not prove a
global optimum.

Integer recovery floors a continuous proposal and still must obey its separately
verified quote domain, currently loaded ticks, actual held inventory and shared
state. No guarantee is made that this single rounded suggestion is the best
integer action or that funded search will recover the continuous flow. Missing
non-V2/V3 local models, tied finite-rate support/recovery, and complete budget
and quality benchmarks remain explicitly open.

The capped doubling branch additionally restricts integer recovery below the
full known continuous domain in some cases. Its explicit lower-bound status is
an honest partial-domain result; it does not fulfill a universal maximum-domain
reconstruction claim. Reporting and benchmarking must retain that distinction.
