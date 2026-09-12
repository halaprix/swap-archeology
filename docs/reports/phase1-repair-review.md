# Independent phase 1 repair review

Reviewed 2026-09-07. **Pass for the bounded repairs and calm-block quote admission described below. No confirmed defect remains open in this review scope.** This is not a solver, settlement, or complete RPC-system acceptance verdict.

I read the project instructions, interfaces, original six findings and cached raw evidence before implementation conclusions. I wrote only this report; all reproduction mutations were in memory. No RPC calls, git operations, implementation edits, or evidence rewrites were performed by this reviewer.

## Original findings

| Original finding | Current result and evidence |
|---|---|
| Missing bitmap word interpreted as empty | Fixed at the shared constructor, `src/swaparch/adapters/uniswap_v3/state.py:60`. Removing word 76 from the block-23549991 fixture causes `Unsupported: incomplete tickBitmap coverage: missing words [76] in advertised range [68,84]`. `_word` at line 115 no longer supplies a zero default. |
| CSV labels overclaim original caller mode | Fixed. `scripts/uniswap_v3_csv_check.py:69` reports all matching models, while its legacy classifier explicitly retains first-match compatibility labels. `docs/adapters/uniswap_v3.md:133` states the missing caller information and pre-state assumption. Recomputed overlap sets: 3219 exact-in + observed-terminal; 899 exact-out + observed-terminal; 82 observed-terminal only; 3 exact-in only. These establish arithmetic consistency, not original caller modes. |
| Every discovered pool marked supported | Fixed at record construction, `scripts/uniswap_v3_discovery_run.py:106`, and admission, `src/swaparch/universe.py:103`. A record must be supported and contain the current snapshot hash in `validated_block_hashes`. The family-level status remains supported; it is not the per-pool admission gate. |
| Failed bitmap dependency crashes planning | Fixed at `src/swaparch/adapters/uniswap_v3/adapter.py:176` and `src/swaparch/universe.py:78`. The real failed-word fixture, passed through the actual adapter and `acquire`, produces a pool-specific `AcquireReport.unsupported` entry. Passing it to `load_states` excludes the pool with the same reason. This independent reproduction supplements the integration test's intentionally synthetic failing adapter. |
| Amount0 fallback checked-add omission | Fixed at `src/swaparch/adapters/uniswap_v3/math.py:234`. `(Q96, 1, UINT256_MAX, True)` now raises `EvmRevert`. The new upstream exact-output vector also detects removal of the output cap. |
| Online checker acquires by number without final hash check | Fixed in the reviewed path. `scripts/castlib.py:108` delegates to the existing hash-pinned client/Multicall implementation. `scripts/uniswap_v3_online_check.py:127,213` checks the block hash after acquisition and quote validation. `main` at line 283 treats missing results, output mismatch, and terminal-price mismatch as failure. Offline integration tests reject a changed hash and return exit status 1 for a quote mismatch. No fresh RPC acceptance was attempted by this reviewer. |

## New validator and raw evidence

`scripts/validate_v3_universe.py:35` excludes stETH, tests 1 and 100 token units in each direction, and at line 77 requires all four output/terminal-price comparisons before recording support. It records raw QuoterV2 call identities and responses, and updates the block-specific hash set at lines 92–99. Failure to quote within the loaded window remains an explicit exclusion; it does not invent capacity. The command's successful completion means an inventory classification was produced, not that every discovered pool passed.

Inspected evidence:

- `data/validation/uniswap_v3/0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5.json`.
- Stored snapshot and inventory for chain 1, block 25896003, that same hash.
- Both original radius-8 snapshots and raw QuoterV2 cross-check files under `data/adapters-evidence/uniswap_v3/`.

The current calm inventory admits **23 pools** and excludes **29**, including all **five stETH pools**. I decoded the raw return words independently of the adjacent JSON result fields and reran all **92 admitted-pool comparisons** against the stored local state: all output and terminal-price values match. I separately ABI-decoded all **106 saved QuoterV2 request payloads**, checking token direction, input amount, fee and zero price limit against the inventory/check identity. All agree. The 106 include checks for pools that failed another required size/direction and therefore remain excluded.

All **18 original single-pool raw QuoterV2 comparisons** also reproduce. These are direct comparisons with independent protocol responses, unlike wrapper-versus-callee consistency tests. I did not rerun the producer's network acquisition or independently measure its reported request count.

## Defects found during this review and fixed before final acceptance

1. **Medium: returned input could be called a full fill.** A request to trade 100 A for C, with steps A→B 100 then B→A 98 through the same test pool, returned `feasible=True`, output C=0, spent A=2, residual A=98. The allocation counter alone did not establish net input consumption. `src/swaparch/evaluator/evaluate.py:80` now rejects remaining input; the exact reproduction returns `input token remains after execution`.
2. **Medium: fractional raw-unit amounts were accepted.** Request and step amount 100.5 produced a feasible result with float output 99.0 and spent 100.5. Request/step integer guards at `evaluate.py:14,42` now reject fractional amounts, including a float step with an integer request. The output guard at line 61 and chain check at line 45 add boundary checks.
3. **Low: window analysis lost the constructor failure.** With failed bitmap word 76, every attempted constructor failed, and `minimal_radius` referenced uninitialized `last`, raising `UnboundLocalError`. `scripts/uniswap_v3_window_analysis.py:83` now retains the construction error. The exact reproduction returns radius/output `None` with the missing-word error.

These findings were sent to the implementer before fixes. I reran all three reproductions after the changes landed. The existing stranded-intermediate test also passes: leaving 1 B after A→B→C is infeasible.

## Commands and results

Working directory: `<checkout>`.

```bash
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest -q \
  tests/test_uniswap_v3_math.py tests/test_uniswap_v3_state.py \
  tests/test_csv_evidence.py tests/test_integration.py \
  tests/test_evaluator.py tests/test_multicall_encoding.py
```

Initial result: **55 passed in 1.87s**, despite the three additional reproductions above. Final result after their repairs: **57 passed in 2.44s**. This illustrates the scope of the original false-green result; the final acceptance also includes the independent reproductions.

Exact in-memory mutation command:

```bash
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python - <<'PY'
from pathlib import Path
import pytest
from swaparch.adapters.uniswap_v3 import math as m
source = Path(m.__file__).read_text()
needle = 'if (not exact_in) and amount_out > (-amount_remaining):'
assert needle in source
exec(compile(source.replace(needle, 'if False: # reviewer clamp mutation'),
             m.__file__, 'exec'), m.__dict__)
code = pytest.main(['-q', '-p', 'no:cacheprovider',
    'tests/test_uniswap_v3_math.py::test_compute_swap_step_caps_exact_output_upstream_vector'])
assert code == 1
print('PASS: clamp mutation killed by new upstream-vector test')
PY
```

Observed: targeted test fails at output index 2, **2 instead of 1**, and the outer mutation check exits 0. No source file was changed.

Exact final evaluator/window reproduction command:

```bash
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python - <<'PY'
import sys
sys.path[:0] = ['tests', 'scripts']
from test_evaluator import *
from test_uniswap_v3_state import load
import uniswap_v3_window_analysis as w
cases = [
    ('cycle', plan(100, (Step('ab', A.address, B.address, 100),
                        Step('ab', B.address, A.address, 98)))),
    ('float request', plan(100.5, (Step('ab', A.address, B.address, 100.5),), B)),
    ('float step', plan(100, (Step('ab', A.address, B.address, 100.0),), B)),
]
for name, p in cases:
    e = Evaluator().evaluate(p, (pool('ab', A, B),))
    assert not e.feasible
    print(name, e.reasons)
fix, _ = load(23549991)
for c in fix['calls']:
    if c['tag'] == 'univ3:tickBitmap:76':
        c['success'] = False
r = w.minimal_radius(fix, w.WETH, w.USDC, 10**18, None)
assert r['radius'] is None and 'missing words [76]' in r['error']
print('window', r)
PY
```

Observed: all assertions pass; cycle, float request and float step return the explicit rejection reasons described above, and window analysis returns the missing-word error.

## Limits

Four checks qualify integer pool quote math at one hash; they do not establish token transfers, atomic multi-hop execution, arbitrary-size coverage, complete historical source availability, or global optimization. The other 29 discovered pools remain excluded at this hash. The solver and phase 2 CLI were under construction and are outside this review. No broad review of unchanged RPC/cache behavior was performed.
