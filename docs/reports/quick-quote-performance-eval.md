# Quick quote performance evaluation — 2026-09-08

Eight cases: two collected blocks (21895170 and25760917), each with WETH→USDC100, USDC→USDT1000, sUSDe→USDC1000 and USDC→WETH100000. Four variants, two repeats, 64 measured runs. Caches start empty for every variant/case/repeat; repeat order rotates. Context preparation is excluded. Only quote generation/serialization is timed; JSON parsing and independent balance replay run afterward.

All full reports matched exactly, including routes, integer amounts, exclusions and search diagnostics. All runs used zero RPC and the same source states and search budget (grid10, steps8, beam128, expansions5000 plus baseline).

| Variant | Sum of eight per-case median times | Speedup |
|---|---:|---:|
| reference | 18.21s | 1.00× |
| tick_lru | 15.88s | 1.15× |
| initial_swap_memo | 10.85s | 1.68× |
| combined | 9.75s | 1.87× |

The combined variant reduces measured CPU/runtime by about46%. On the later WETH→USDC case, runtime falls from3.74s to1.41s (2.64×). This is a small serial benchmark, not a full-sweep throughput guarantee or a Rust comparison.

`tick_lru` caches the pure tick-to-square-root-price function (8192entries). `initial_swap_memo` caches successful V3/V4 swaps only on the exact original immutable state object, keyed by direction and integer input amount. Post-swap states bypass this cache; no pool identity or block state is conflated. Both wrappers restore the original functions in finally blocks. Production solver/adapter files are unchanged. Sweep workers remain stopped.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/eval_quote_performance.py --repeats 2 --output /tmp/quote-perf.json
.venv/bin/pytest -q tests/test_eval_quote_performance.py
```

Detailed timings/cache counters: assets/phase8-model/quick-performance-eval.json. Input block hashes, collection/snapshot identities and code fingerprint: assets/phase8-model/quick-performance-eval-inputs.json. The focused identity/restoration test and Ruff passed.
