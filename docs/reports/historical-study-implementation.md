# Historical study runner

`scripts/historical_study.py` materializes a restartable manifest under
`data/results/study/<run-id>/`. Each block/scenario is `planned`, `completed`,
or `failed`; a completed no-route report is recorded as `outcome:
no_tested_route`, while a missing offline cache is a failed acquisition/input
condition. The manifest stores the exact quote block hash, scenario/solver
configuration hash, runner hash, inventory hash, captured stage logs, evidence
paths, and stage-reported network request counts. Its code identity covers the
runner, qualification scripts, all `swaparch` Python source, and the dependency
lock; its per-block inventory identity retains only that block's admission,
registry, and ARM observation facts. Later validations at other hashes therefore
do not invalidate an already matching quote.

Quotes call the existing `swaparch.cli.quote_command` with `offline=True`.
They therefore cannot resolve an RPC URL or make network requests. `--acquire`
runs the existing V2/V3, Curve registry/NG, LitePSM, Lido, Origin ARM, and
Aave-reference
qualification functions serially once per block before quotes. Curve discovery
continues to record its registry observations at the exact block hash, and ARM
probing precedes ARM qualification; the runner never projects either backwards.

The default scenario configuration contains WETH, wstETH, sUSDe, USDC, USDT,
and DAI; useful pair directions; and exact human/raw input ladders. Search is
the default solver and keeps its existing baseline incumbent. `--solvers
baseline,search,dual` exposes all current solvers without imposing the old
two-hop baseline on search or dual.

Offline smoke run at an existing pin:

```bash
uv run python scripts/historical_study.py --blocks 25896003 --run-id pin-offline
```

Acquire a new block, then quote it from the resulting cache:

```bash
uv run python scripts/historical_study.py --blocks 25896004 --acquire --run-id block-25896004
```

The five pins are the default block set. Window runs are explicit, for example
`--window all` covers the five inclusive ranges in `docs/COMPLETION.md`
(13,508 blocks). A partial manifest remains partial; it is never a full-window
claim. A run ID is a simple directory name, and an existing ID accepts only the
same planned block set and scenario/solver configuration.
