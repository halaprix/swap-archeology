# Phase 8 offline model sweep — 2026-09-08

Collection completed 13,509 unique planned blocks. All referenced artifacts exist.
Audit of 1,519,500 collected pool records found no report/block identity errors
or duplicate pool IDs (assets/phase8-collection-audit.json).

## Admission and limits

Normal CLI/app admission still requires exact-hash quote qualification. The new
explicit `--collection-model-only` runner path uses the successfully loaded pool
records saved by collection, including exact-block Curve/Origin metadata overlays.
These candidates have prior model qualification, but independent reference quote
checks were not repeated at every study hash. No inventory status or validated
hash list is changed. Every output records qualification_mode, a false aggregate
independently_qualified_at_block flag, source exclusions, frozen inventory identity,
collection digest, composite input identity and code identity.

Snapshots load directly without any RPC client. Contexts are reused within a block
and bounded across blocks. Resume identity covers collection report, archived
inventory, snapshot header/calls, canonical cached header, scenario settings and
code. Changed evidence cannot silently reuse an old result.

## Validation before launch

- Python: 244 passed, 2 skipped; targeted Ruff passes.
- Ten search quotes at five representative blocks: zero RPC, 1.3–4.3 seconds per
  scenario in serial. Calm USDC/USDT result equals the qualified control output.
- Actual runner: eight scenarios across early-stress and calm-window blocks,
  all completed. Separate integer balance replay matches saved final outputs.
- Identical rerun preserves all eight report mtimes: completed scenarios skipped.
- Cache regression checks snapshot/archived-inventory changes invalidate identity.

## Running scope

Eight disjoint processes, 88 scenarios per block, 1,188,792 total scenarios.
All six endpoints, 22 directed pairs and four input sizes from existing default
study config. Search settings unchanged: grid_parts=10, max_steps=8,
beam_width=128, max_expansions=5000, including the baseline incumbent.
Gzip reports, 10 GiB free-space guard, one writer per shard, niceness 5,
BLAS/OpenMP threads limited to one per worker.

Plan and exact restart commands/PIDs:
`data/results/study/phase8-model-sweep-plan-20260908.json`.
Runs: `phase8-model-20260908-w1` through `w8`; logs `/tmp/<run-id>.log`.
Progress helper: `python3 /tmp/phase8-model-progress.py`.
Before restarting, check recorded PIDs to avoid duplicate writers. Reuse exact
commands only with matching code/config; otherwise preserve results and use a
new run identity. Do not rerun the launcher over an existing plan.

This is a multi-day CPU workload at measured serial costs; parallel throughput
must be observed before a firm ETA. Acquisition is complete; routing sweep,
independent per-block quote validation and final analysis remain separate statuses.
Model-only reports have not been added to the qualified frontend catalog.
