# Offline quote performance evaluation — 2026-09-09

The eight-case benchmark improves **7.22× overall**, below the requested **10×** aggregate target. The historical sweep remains stopped. These changes are scoped benchmark experiments, not enabled in `historical_study.py` or the app.

Three repeats, alternating reference/optimized order: 48 timed quotes. Sum of eight per-case medians: **18.686s → 2.588s**. Every full JSON report matched, including routes, integer quantities, exclusions and diagnostics. Independent selected-plan balance replay passed. All runs were offline with zero RPC.

| Block | Case | Reference | Optimized | Speedup |
|---|---|---:|---:|---:|
| 21895170 | WETH → USDC 100 | 2.028s | 0.257s | 7.88× |
| 21895170 | USDC → USDT 1000 | 1.254s | 0.225s | 5.57× |
| 21895170 | sUSDe → USDC 1000 | 0.678s | 0.151s | 4.49× |
| 21895170 | USDC → WETH 100000 | 1.615s | 0.250s | 6.47× |
| 25760917 | WETH → USDC 100 | 3.854s | 0.323s | 11.93× |
| 25760917 | USDC → USDT 1000 | 3.096s | 0.483s | 6.41× |
| 25760917 | sUSDe → USDC 1000 | 2.902s | 0.406s | 7.15× |
| 25760917 | USDC → WETH 100000 | 3.260s | 0.493s | 6.61× |

## Scope and controls

Both variants use the same prepared collection state, all supported sources, and grid=10, max_steps=8, beam_width=128, max_expansions=5000 plus the baseline incumbent. Caches begin empty for each timed quote. Context acquisition, native compilation/module loading, patch setup, report parsing and independent replay are outside the timed interval; quote generation and JSON serialization are inside. This measures serial quote CPU/runtime, not worker startup or full-sweep wall time. Source/block identities and exact build/harness hashes are in the JSON artifact.

The reference is the existing unmodified Python routing path. The optimized scope combines exact immutable successor-state memoization, immutable tick-map reuse, an indexed independent-route quote table, overlapping-route prefix reuse, and skipping intermediate evaluator calls when input necessarily remains. Candidate visits, final-depth rejection checks, ties and shared-state behavior remain unchanged. Cython compiles the existing math and hot methods while retaining Python arbitrary-precision integers and original exception classes; C-callable math helpers reduce internal call overhead. Internal concentrated-liquidity successor constructors reuse already validated immutable bitmap coverage; unknown maps or ranges still use the original constructor.

Full-plan evaluator memoization, static search metadata caching, extra pure-math caches and an overlapping-route suffix cache did not provide repeatable gains and are excluded from the retained stack. No Rust implementation was benchmarked. Existing profiles identify CPU-bound simulation and solver work, rather than RPC or output I/O, as the bottleneck.

## Reproduce

Optional build-only dependencies: Cython 3.3.0 and setuptools; a C compiler and headers matching the active Python are required. No new application dependency was added.

```bash
uv run --with 'Cython==3.3.0' --with setuptools python scripts/build_perf_native.py
.venv/bin/python scripts/perf_native.py --repeats 3 --output /tmp/native-performance-eval.json
.venv/bin/pytest -q tests/test_perf_native.py tests/test_perf_state_memo.py tests/test_perf_solver.py
```

The builder writes generated files to ignored `.cache/perf-native/`. The evaluator rejects missing native extensions and changed compiled input sources. Run a fresh process after rebuilding. Scoped patches restore on exit and are intended for single-threaded offline benchmark processes.

Validation: 79 existing math, V3/V4/Curve adapter, evaluator and solver tests passed with the compiled methods and successor constructors active. A separate regression checks unchanged-map reuse, frozen successors, new-range rejection and external mutable-map isolation. Full-report parity is evidence for these eight cases, not universal protocol qualification or a guaranteed full-study speedup.

Detailed results: [native-performance-eval.json](assets/phase8-model/native-performance-eval.json). Initial four-variant experiment: [quick-quote-performance-eval.md](quick-quote-performance-eval.md).

## Larger machine and the hypothetical 1,000× target

The current host reports a Ryzen 7 7840HS with 8 physical cores and about 30 GiB RAM. Across the 48 measured runs, CPU time / wall time is 0.999 for reference and 1.000 for optimized quotes. The isolated hot path is CPU-bound. A short live vmstat sample showed negligible I/O wait; allocated swap alone does not establish paging as this benchmark's bottleneck.

The user's other machine has a Ryzen 9 7950X3D, 128 GB RAM and RTX 5070 Ti. AMD specifies 16 CPU cores, 32 threads and 128 MB L3 cache; NVIDIA specifies 16 GB GDDR7 for the GPU. [AMD specifications](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7950x3d.html), [NVIDIA specifications](https://www.nvidia.com/en-au/geforce/graphics-cards/50-series/rtx-5070-family/).

Doubling physical cores provides approximately twice the worker capacity before scaling losses or per-core differences. Multiplying that idealized factor by the measured 7.22× software improvement gives about 14.4× whole-study throughput relative to the original implementation on this host. This is an arithmetic scenario, not a measured machine comparison; the next hardware test is identical offline workloads with 8, 16, 24 and 32 workers and RSS/CPU/I/O measurements. Extra RAM mainly provides process and retained-state headroom. The app and sweep currently have no GPU execution path.

A 1,000× per-quote improvement would turn a 2–4 second quote into 2–4 milliseconds. The current finite search still performs up to 5,000 expansions plus baseline route-pair simulations. Pursuing that target requires evaluating far fewer candidates, reusing work across amounts/blocks with explicit state invalidation, and/or a compact native batch implementation. Candidate shortcuts need route-quality evaluation; exact integer replay of the winners remains necessary and alone does not establish that discarded alternatives were worse. The existing numerical dual guide only admits V2/V3/LitePSM models and still uses general search for recovery, so it is not a ready all-source replacement.

GPU use would require an explicit batch representation and kernels for branching simulations and wide-integer arithmetic. Broad screening followed by exact CPU evaluation is a possible experiment, with separately measured quality loss or proven pruning bounds. It is not a 1,000× performance claim. NVIDIA documents that divergent control flow can reduce GPU throughput. [CUDA best practices](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#control-flow).

## Actual full-block workload

All 88 scenarios from `historical_study.default_config()` at block 25760917 were compared with empty per-case caches and the same study search settings. One repetition: **213.560s reference → 32.822s optimized = 6.51×**. All 88 full reports matched, and exit codes matched. This is broader workload coverage than the eight-case microbenchmark, but only one block and one repetition.

The run used 14 non-overlapping, corrected-timer chunks. The aggregator verifies exactly 88 unique scenarios and matching settings, block/source identities and build manifests. It excludes the initial timing-invalid run (which included JSON parsing/replay) and two overlapping oversized chunk artifacts. The retained timing boundary matches the eight-case benchmark. Raw per-case timings and chunk/source hashes are in [full-block-performance-eval.json](assets/phase8-model/full-block-performance-eval.json); the final original chunk harness is archived as [full-block-eval-source.py.txt](assets/phase8-model/full-block-eval-source.py.txt).

The durable evaluator now also supports this workload:

```bash
.venv/bin/python scripts/perf_native.py --study-scenarios --blocks 25760917 --repeats 1 --output /tmp/full-block-performance-eval.json
```

The 10× same-host aggregate target remains unmet. The observed 6.51× full-block improvement combined with ideal 2× worker capacity on the proposed 16-core host gives an unverified ~13× whole-study throughput scenario. No transfer or benchmark on that host, GPU implementation, sweep restart, commit, push or deployment was performed.

Final validation: full normal suite 257 passed / 2 skipped; 12 focused cache/loader/solver tests passed. A final rebuilt-loader quote smoke check matched its full reference report. Native math/adapter/evaluator/solver subset: 79 passed with compiled code and optimized internal successors active. Ruff passed. The loader checks manifest consistency after extension imports and rejects generated Python fallbacks before executing them; incomplete builds have no completion manifest.
