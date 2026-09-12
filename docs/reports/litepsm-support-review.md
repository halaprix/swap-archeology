# Independent LitePSM support and dual integration review

2026-09-08. Reviewed `solver/litepsm_support.py` and its `DualSolver` integration
against the qualified immutable `LitePsmState`, local canonical
`data/protocol-sources/litepsm/DssLitePsm.sol`, and the phase-3 design. No RPC or
source/data modification was performed by this reviewer.

## Verdict

The corrected helper passes independent capacity, fee, lattice and support
checks. Integration selects at most one directional contribution per PSM,
preserves continuous support separately from exact integer actions, and retains
tied intervals in diagnostics. It adds a bounded local model, not continuous
flow recovery, shared-owner containment, settlement proof or an upper bound.

## Corrected findings

The first buy-side capacity used only pocket balance and allowance. It omitted
checked gross/fee arithmetic and DAI-balance headroom. With zero fee, factor
10^12, pocket/allowance 5 and buffer MAX_UINT256−2×10^12, the helper attempted
output 5 and discarded it, although the valid maximum is 2. With 100% buy fee
and maximum pocket/allowance, endpoint fee overflow similarly hid valid smaller
trades. The corrected `max_buy_gem_output` searches the full exact replay
predicate; both cases now retain their positive feasible prefix.

Initially, selecting no trade returned a negative objective and nonzero endpoint
flows; ties also claimed endpoint replay without performing it. Corrected
selected zero/tie support has zero flow/objective. Separately named endpoint
fields preserve its values and actual replay evidence, including
`exact_replayed_tie_not_selected`. Negative endpoint value is no longer mistaken
for the support function, which contains the zero trade.

## Independent reference

Let U=2^256−1, W=10^18, F=conversion factor, B=DAI buffer, G=pocket GEM and
A=allowance. For active sell fee t<W, independent integer arithmetic gives:

```text
sell capacity = min(U//F, U-G, B*W//(F*(W-t)), U//(F*t) when t>0)
sell output(g) = F*g - floor(F*g*t/W)
buy capacity = min(G, A, U//F, U//(F*t) when t>0,
                   (W*(U-B+1)-1)//(F*(W+t)))
buy required DAI(g) = F*g + floor(F*g*t/W)
```

The buy headroom expression uses a strict inequality before flooring, avoiding
an off-by-one at `floor(cost) <= U-B`. These closed forms do not call either
helper or the adapter's quote routine to determine the expected capacity.
HALTED disables its direction; a 100% sell fee has no positive-output support,
while a 100% buy fee remains a finite double-cost direction subject to checks.

The independent checker passed **270 cases over 53 states**: 48 synthetic states
covering fees 0, 10%, 20%, W−1, W and HALTED; zero/small/extreme inventories;
allowance, checked-arithmetic and balance-headroom limits; plus the five
qualified historical pins. Each active direction was tested with favorable,
equal and unfavorable value slopes. Expected continuous values use Fraction
arithmetic; Decimal comparisons use reference precision 120 and relative
tolerance 10^−70. Every selected endpoint matched the source's integer formula,
tie intervals retained their correct variable units, and original states stayed
unchanged. The temporary command was:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python /tmp/litepsm-support-reference.py
independent capacity/rate/lattice/support cases 270 states 53
```

## Integration and remaining limits

`DualSolver` admits only the actual `LitePsmState` type through this new path.
It compares both directions and zero for one pool rather than summing independent
directional capacities. Output/input prices are scaled to raw-token units.
Continuous support enters the numerical objective, while exact lattice-compatible
endpoint amounts become recovery suggestions. Ties have zero local objective,
no arbitrarily selected action, and explicit interval diagnostics. Other source
families retain their model exclusions and remain available to funded search.

The buy variable is GEM output, not arbitrary DAI input; its tie interval and
capacity must keep that unit. Intermediate tied amounts are not recovered by an
LP or interpolation in this implementation. Repeated/reverse visits and aliases
sharing PSM inventory still require exact shared-state execution and do not gain
an aggregate containment proof. The combined numerical model also retains the
previously documented rounded V2 contribution and finite V3 coverage limits.

## Integration replay and tests

Independently reconstructed the uniquely specified one-hop trades in all three
`data/results/litepsm-dual-smoke/*.json` summaries: DAI→USDC at 23550060 and
25896003, and USDC→DAI at 25896003. These summaries save step counts rather than
full plans; each has one loaded PSM and one full-input step. Fresh evaluation
reproduced their 1000000 raw USDC or 1000000000000000000 raw DAI output.

The full all-family artifact
`data/results/psm-integration/all-family-dual-1weth-usdc-25896003.json` does save
ordered steps. Its complete 42-state universe matched independent loading. All
three saved winners passed fresh evaluation and separate funded inventory/state
threading, returning 2395804405 raw USDC for 1 WETH. The numerical model admits
exactly one LitePSM and retains both directional tie intervals with zero
continuous objective. The winning route does not establish that LitePSM improved
this request. No optimization was rerun during these six checks.

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest \
  tests/test_litepsm_support.py tests/test_v3_continuous.py \
  tests/test_general_solver.py tests/test_evaluator.py -q
45 passed in 3.67s
```

Observed SHA-256 identities at review:

| File | SHA-256 |
|---|---|
| canonical `DssLitePsm.sol` | `502eed38778ac29758959cadbb3d2f36aa3af21144e7374483795581a6279ce8` |
| `solver/litepsm_support.py` | `adf0e6a9a165676620a772912351cd1744df774ae66ee1a941938d8b022ce62c` |
| integrated `solver/dual.py` | `c33b9e6a0df0656a4c3d4c03ec70ad9ffe262a10bf73eb7b9b593906f5736f09` |
| `tests/test_litepsm_support.py` | `065a47738b3886c0d09de818781e22e37a59d33fd718910ff40e09fa8f0951ae` |

Accepted for this bounded helper/integration slice. The separate 15-file
continuous-V3-only benchmark predates LitePSM model admission and remains
identified by its earlier dual hash; its results are not reused as evidence for
this new local model.
