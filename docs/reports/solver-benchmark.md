# Solver benchmark: cache-only 100-WETH slice

Run date: 2026-09-08. These are feasible-candidate measurements from a pinned
cache, not routing optimality results and not dual bounds. Every winner below
was re-evaluated by the integer evaluator. Gas has no converted output cost, so
`net_amount_out` is null and ranking is by gross output.

Both runs used WETH (`18` decimals) to USDC (`6` decimals),
`100000000000000000000` raw WETH (100 WETH), and the same budgets: six steps,
beam width 128, 5,000 expansions, grid 10, and eight V3 samples per finite
domain. The emitted JSON records the complete state-pool IDs, explicit load
failures, source inventory, solver configuration, convergence diagnostics, and
SHA-256 identities for `dual.py`, `search.py`, `baseline.py`, `evaluator.py`,
and `uv.lock`. The solver hashes were `2dedade81715…6ffa0` and
`730ce1741c6e…42959`, respectively.

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark \
  python scripts/benchmark_solvers.py \
  --block-hash <pinned-block-hash> --amount 100000000000000000000 \
  --max-steps 6 --beam-width 128 --max-expansions 5000 --grid-parts 10 \
  --dual-samples 8 --output /tmp/solver-100weth.json
```

| Snapshot | Admitted V2/V3 states | Solver | Gross USDC raw output | Runtime | Feasible candidates | Search budget result |
|---|---:|---|---:|---:|---:|---|
| block 25896003, `0xf2c9645a…ba5a5` | 11 / 23 (+1 LitePSM) | finite baseline | 239,279,717,900 | 7.669 s | 3 | baseline limits apply |
| same | same | stateful search | 239,279,717,900 | 8.507 s | 32 | 5,000 expansions; truncated; beam pruned |
| same | same | numerical dual + recovery | 239,279,717,900 | 15.298 s | 21 | dual converged; recovery truncated and beam pruned |
| block 24356381, `0x386830fe…18eeb` | 11 / 24 (+1 LitePSM) | finite baseline | 232,162,128,453 | 7.168 s | 3 | baseline limits apply |
| same | same | stateful search | 232,162,128,453 | 8.242 s | 11 | 5,000 expansions; truncated; beam pruned |
| same | same | numerical dual + recovery | 232,162,128,453 | 14.262 s | 11 | dual converged; recovery truncated and beam pruned |

The canonical all-family inventory was supplied to every solver. The calm run
admitted 35 states and recorded 615 explicit load exclusions; the stress run
admitted 36 and recorded 614. The difference from the V2/V3 counts in the table
is one qualified LitePSM state in each row. The artifact lists every excluded
record and the source inventory. The numerical dual excludes LitePSM from its
V2/V3 price model, while its recovery search retains that state. Consequently,
this is an all-state candidate run but not a heterogeneous dual model or a
heterogeneous-quality result.

The stateful search represents arbitrary topology within its stated step and
expansion budgets, carries each pool's updated state, permits funded
return-and-respend, and never creates unseeded cycle funding. Beam ranking uses
backward local output-value estimates only as a heuristic. It is not an upper
bound. Supplied incumbents can be retained across budget experiments; different
non-nested grids are not claimed to be monotone.

The numerical dual has continuous V2 stationary support constrained to the
adapter's uint112 post-swap domain. Its V3 support is a finite set of exact
loaded-tick quotes, including token-decimal-normalized request-neighbourhood
samples and observed accepted domain probes. It is explicitly a sampled model,
not continuous V3 tick support. Floating-point optimization, finite sampling,
and repeated/reverse-visit containment gaps mean its reported objective is a
numerical estimate, never a bound or proof.

Synthetic-only independent references exercise a three-branch downstream merge
with regular beam recovery, a four-hop-only route, shared pool updates, hard
directional caps, a three-token pool, dust, unsupported hooks, unseeded cycles,
and gross-versus-unknown-net gas handling. They verify those mechanisms, not
quality on the cached real universe. Terminal evaluator refusals, including an
unmodelled shared-capacity owner, and no-winner truncation are preserved in both
search and dual recovery diagnostics; neither occurred for the returned plans
in these two rows.

No RPC was used for this report. The truncated runs, missing continuous V3
support, absent containment proof, and lack of a heterogeneous real benchmark
leave phase-3 benchmark completeness unresolved.
