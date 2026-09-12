# Loaded-tick V3 continuous support

Implemented 2026-09-08. `DualSolver` now replaces its finite V3 quote sample
grid with a continuous one-direction support calculation over intervals proved
by an immutable `UniV3State`'s loaded bitmap words and `liquidityNet` map.
This is a numerical candidate guide only. It is not a global V3 domain,
containment proof, executable quote, or dual bound.

For an interval from square-root price `sa` to `sb`, liquidity `L`, and
`gamma = (1_000_000 - fee) / 1_000_000`, the model uses the source-derived
constant-liquidity relations:

| Direction | Net input | Output | Price-stationary point |
|---|---|---|---|
| token0 to token1, downward | `L * (1/sb - 1/sa)` | `L * (sa - sb)` | `sqrt(p0 / (gamma * p1))` |
| token1 to token0, upward | `L * (sb - sa)` | `L * (1/sa - 1/sb)` | `sqrt(gamma * p0 / p1)` |

`p0` and `p1` are value per raw token unit. The implementation uses `Decimal`
for the stationary price and cumulative input/output, applies the fee through
gross input, and stops at marginal equality or the last contiguous loaded
boundary. It follows the same direction-specific sign change used by the
adapter when crossing an initialized tick. A zero-liquidity span advances only
to a loaded initialized tick with zero input and output, then applies its
liquidity-net update. A required unloaded word or missing initialized-tick
liquidity net stops the support; neither is treated as empty liquidity.

The continuous amount is floored to an integer seed without a float round trip,
clamped to an existing exact local quote probe, and requoted through
`UniV3State.quote_exact_in`. Only that integer quote is suggested to funded
stateful recovery and then freshly evaluated. Diagnostics retain the continuous
objective, raw Decimal amounts, selected boundary, loaded-domain stop reason,
integer seed, and accepted-cap status. A rejected upper probe gives an exact
accepted-prefix cap; exhausting the fixed doubling budget gives only a verified
lower bound, which safely clamps recovery without claiming a domain edge. The
optimizer uses the continuous V3 objective even when the floored integer amount
has zero output or is rejected;
only a positive exact quote becomes a recovery suggestion. V2 still contributes
its rounded integer proposal to the outer objective, so the combined model is
not a fully continuous heterogeneous relaxation. Diagnostics identify V3 as
`continuous loaded-tick intervals; no global containment or bound`.
The optimizer cache keys exact log-price probe floats; it does not round prices,
which would collapse finite-difference probes at small token values.

Offline checks in `tests/test_v3_continuous.py` cover an analytical within-tick
stationary point, a zero-liquidity gap with exact bidirectional replay, an
unloaded-word edge, protocol price limits, Decimal-context restoration after an
exception, fractional continuous-objective retention, and the qualified
cached 0x88e6 USDC/WETH state at all five canonical pins. The detailed boundary
replay uses block
`0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`.
The cached test verifies an initialized boundary is present, replays the
exact cap quote, and confirms cap plus one fails because it needs an unloaded
word. It also confirms the continuous proposal's integer amount exactly matches
the adapter quote.

The focused checks passed offline:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark \
  pytest -q tests/test_v3_continuous.py tests/test_general_solver.py tests/test_evaluator.py
# 38 passed
```

Remaining work includes a heterogeneous continuous model for non-V2/V3 sources,
continuous-flow recovery across multiple venues, a proof or containment regime,
and real benchmark quality sweeps. LitePSM and the other admitted families stay
outside this V3 support model and remain available to the exact fallback search.
