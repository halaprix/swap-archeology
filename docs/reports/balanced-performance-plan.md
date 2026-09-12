# Minimal-change performance recommendation — 2026-09-09

Recommend the pure-Python solver improvements with a bounded cache keyed by the actual immutable state object, direction and input amount. Cached entries hold strong references to the input state, preventing identity reuse, and retain the immutable successor. Each quote receives a fresh cache. Do not key just by pool address, and do not use process-global class patching in the eventual production integration.

On the original eight-case workload, two repeats and full-report parity:

| Stack | Speedup |
|---|---:|
| Original-state-only V3/V4 cache plus Python solver shortcuts | 3.17× |
| All-state identity cache plus Python solver shortcuts | 5.35× |
| Semantic state-key cache plus Python solver shortcuts | 5.46–5.64× |
| Compiled prototype | 7.26× |

The identity/full-semantic comparison is paired within one 48-run experiment; the original-state/native rows come from a separate 64-run comparison of the same workload. Ratios use sums of eight per-case medians. All variants retain the search settings and full JSON outputs. These are eight-case results; the identity variant has not yet received the full 88-scenario acceptance run.

The proposed integration consists of (1) a quote-local immutable-state swap cache, wired without global monkey-patching, (2) bounded pure tick-math caching, (3) indexed baseline route quotes plus reused first-route state, and (4) skipping intermediate evaluator calls when input necessarily remains, while preserving final-depth refusal checks. Keep existing adapters, search budget, shared-capacity checks and exact integer evaluator. Check duplicate-route diagnostics and cache isolation when moving the experiments into source.

The simpler cache is about 5.5% slower than the fuller Python stack in their paired comparison. It avoids per-adapter semantic keys and canonical tick-map interning. Native compilation is an optional later increment; it requires a build pipeline and extra validation. The recommendation does not achieve the requested 10× same-host target.

No production integration or sweep restart was performed. Before enabling it, run the 88-scenario full-block parity/quality check and cache lifetime/failure/shared-state regressions on the actual implementation.

Evidence: [paired identity comparison](assets/phase8-model/identity-performance-eval.json), [broader stack comparison](assets/phase8-model/balanced-performance-eval.json), [identity cache source](assets/phase8-model/identity-memo-source.py.txt), [paired benchmark source](assets/phase8-model/identity-eval-source.py.txt).
