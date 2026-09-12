# Continuous V3 benchmark independent acceptance

2026-09-08. Reviewed the completed 15 files matching
`data/results/continuous-v3-benchmark/*WETH*.json`, without rerunning optimization
or making RPC calls. **All 45 winners and 132 ordered steps replayed exactly.**
All three solvers returned identical outputs in every case, also matching their
old, identically configured six-family matrix results. The new model showed no
exact-output improvement in this experiment and increased numerical-model time.

## Matched experiment and replay

Every file records steps=6, beam=32, expansions=500, grid=4 and dual_samples=24.
Baseline remains its two-hop/two-path incumbent with grid=4. Independent loading
matched the complete ordered state-id list and every load-failure reason. State
counts were 40/40/42/43/42 across the five chronological pins. Snapshot chain,
hash, number and timestamp, token addresses, raw amounts and decimal scales
matched. Every solver receives the same immutable state tuple in the runner.

All six recorded hashes matched the reviewed files: continuous V3
`e4e0c3d666d5ae11f75e959a60c861efb9bf1ac57bacb35524b3e38a8c233310`, dual
`856dae27ba31fe150ba25681b22a674abc2682b43edfcbc9dd75270634763190`, plus search,
baseline, evaluator and uv.lock. This is a recorded subset of code identity;
the benchmark file does not hash every adapter, inventory, runner or raw
snapshot payload. Python version and block-hash snapshot identity are recorded.

Each winner passed a fresh Evaluator call and a separate held-inventory loop
threading immutable pool replacements. Both reproduced every step output, total
output, input spent, zero original/intermediate residuals and gas estimate. Net
output and output-denominated gas cost remain null. Transport, credential lookup,
snapshot persistence and solver entrypoints were replaced with failing guards
during replay. No optimizer or acquisition was invoked.

The old comparison used the matching files under
`data/results/six-family-full-intermediates`. Its dual-model admitted ids plus
explicit model exclusions exactly reconstructed the same full state universe;
request, block hash and configured budgets matched. The old matrix's frozen
code manifest predates this continuous extension, as documented separately.

## Exact outcomes

Each table entry is raw six-decimal USDC output, equal for baseline, search,
continuous-guide recovery and the corresponding old matrix result.

| Block | 1 WETH | 100 WETH | 1000 WETH |
|---:|---:|---:|---:|
| 23549991 | 3397803529 | 333396738584 | 3190663874300 |
| 23550060 | 3577784803 | 348279610808 | 3326073013454 |
| 23728292 | 3146011442 | 313530458104 | 3098757621514 |
| 24356381 | 2372717541 | 232864893805 | 2178297873808 |
| 25896003 | 2395804405 | 239277467909 | 2387402343714 |

| Solver wall time | Minimum | Median | Maximum |
|---|---:|---:|---:|
| Baseline | 1.253 s | 2.587 s | 6.496 s |
| Stateful search | 1.346 s | 2.628 s | 7.510 s |
| Numerical guide plus search | 15.593 s | 21.382 s | 33.405 s |

Old/new model-only medians were 8.992/19.126 seconds. These are single-run local
observations, not controlled performance estimates. All 15 search runs and all
15 dual recovery searches were truncated. The numerical optimizer reports
failure for 23549991/1000 WETH, 24356381/1 WETH, 24356381/100 WETH and
25896003/100 WETH; the incumbent still supplies a feasible exact winner. The
other numerical success flags do not establish optimality or bounds.

## Reproduction and limits

`UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python
/tmp/continuous-v3-benchmark-review.py` produced 15 reviewed reports, 45 exact
winners and 132 replayed steps. The temporary checker also compared all recorded
hashes, budgets, complete state identities, exclusions and old exact outcomes;
its summary is `/tmp/continuous-v3-benchmark-review.json`.

Accepted as a bounded five-pin/three-size comparison. It supports retaining the
baseline incumbent and does not demonstrate a quality advantage for the new
guide. It does not complete the historical windows, a budget sensitivity sweep,
heterogeneous continuous models, continuous flow recovery, net-of-gas ranking,
settlement validation or certified upper bounds.
