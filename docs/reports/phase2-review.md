# Phase 2 independent acceptance review

2026-09-07. The review was independent of the solver/CLI implementation. Scope: the finite baseline at block 25896003, evaluator integration, source admission/reporting, and selected-route evidence. No reviewer RPC calls, source/data/evidence edits, git operations, or remote writes were performed.

**Verdict: PASS for the stated phase 2 baseline after one confirmed rounding defect was fixed.** No open acceptance blocker remains in the reviewed slice. This does not approve other source adapters, a global optimizer, or atomic execution.

## Confirmed defect, repaired and retested

The original split grid depended on pool enumeration order when the input was not divisible by `grid_parts`. For input 5 and grid 3, a pool with inventory 4 and output rate 2 plus a pool with inventory 5 and rate 1 returned 8 in `(high, low)` order and 9 in `(low, high)` order. Reversing execution order retained the same allocation-to-pool assignment; it did not include the rounded complementary allocation.

The implementation now uses the symmetric allocation set `{floor(N*i/G), N-floor(N*i/G)}`, removes zero/endpoints/duplicates, and evaluates both execution orders. I reran the reproducer against the changed implementation: output 9 in both pool orders. `test_rounded_grid_assigns_complements_to_each_route_regardless_of_state_order` retains this boundary as a regression test.

## Acceptance evidence

Commands run from the project root:

```text
UV_CACHE_DIR=/tmp/swaparch-review-uv-cache uv run --no-sync pytest -q
68 passed, 1 skipped in 2.39s                 # before the rounding regression landed

UV_CACHE_DIR=/tmp/swaparch-review-uv-cache uv run --no-sync pytest -q -m 'not rpc'
69 passed, 1 deselected in 2.38s              # after the fix
```

I also executed independent inline Python checks with `.venv/bin/python` and `PYTHONPATH=tests .venv/bin/python`:

- 200 seeded exhaustive tiny constant-product cases compared all direct paths, two-hop paths, pair allocations, and execution orders against handwritten reserve arithmetic, without using the adapter or evaluator for expected outputs: all matched. A second 200-case run included two paths sharing their first pool and independently updated that reserve state: all matched. Input sizes were 1–14; grid size equaled input size, making these checks exhaustive over integer allocations within the declared path scope.
- Replayed the actual 1 and 100 WETH CLI quotes with `RpcClient._rpc` and `SnapshotStore._persist` patched to raise. Both returned exit 0, zero network requests, and the saved outputs below. All four reported options conserved the full input, had zero input residual, and their allocations summed exactly to the request.
- Injecting an empty `load_states` result returned exit 1 and null direct/single/split options. Seven invalid amounts (`0`, `-1`, `NaN`, `Infinity`, `junk`, an unrepresentable fractional wei, and `1e100`), an unknown token, identical input/output tokens, and a zero grid were all rejected before `RpcClient` construction.
- The suite exercises repeated-pool state updates, a fixed inventory limit, rejection of independently represented shared capacity, stranded intermediate balances, unheld-token spending, integer-only requests, and retained unsplit candidates. The independent exhaustive reference adds shared-pool ordering checks beyond those unit assertions.
- Source inspection confirms all supplied states participate in direct and permitted two-hop enumeration; there is no first-N pool truncation. Default intermediates are USDT, DAI, and USDe. Admission requires both supported status and the exact snapshot hash in `validated_block_hashes`; the integration tests reject unvalidated and other-hash records.

Both actual reports identify all 15 source families, admit 23 V3 pools, and expose 29 excluded V3 pools plus 14 unavailable/unimplemented family rows. These are explicit coverage limits, not zero-liquidity claims. The CLI currently registers only V3, the only implemented supported adapter.

| Input WETH | Saved reference USDC | Best direct USDC | Best single path USDC | Best split USDC |
|---:|---:|---:|---:|---:|
| 1 | 2393.942838 | 2395.752087 | 2395.752087 | 2395.794890 |
| 100 | 239131.739028 | 239131.739028 | 239240.556009 | 239279.717900 |

All amounts above are before gas at chain 1, block 25896003, hash `0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`. Raw integer values are in `data/results/phase2/weth-usdc-{1,100}.json`.

## Independent protocol evidence inspection

The integrator owns online acquisition. I inspected `scripts/check_selected_routes.py` and independently decoded the existing raw calldata and results in `data/validation/routes/weth-usdc-{1,100}.json` offline.

For each report, all five recorded `QuoterV2.quoteExactInput(bytes,uint256)` results match the expected terminal outputs exactly. I checked the function selector, ABI input amount, packed token/fee path against the current pool inventory, final endpoint, chain/block/hash identity, and SHA-256 linking the validation file to the exact report bytes. Both validation files record zero network requests for their cached rerun. I did not independently rerun their original online acquisition.

The checker verifies full input consumption, per-allocation sums, token/amount continuity, pool token membership, and reported totals before making calls. It refuses repeated pools because QuoterV2's reverted simulations cannot validate sequential changes to shared liquidity. The selected real plans are pool-disjoint; their raw path checks therefore cover the selected allocations' terminal quotes. Stateful shared-pool behavior is independently checked synthetically, not claimed as on-chain execution evidence.

## Reproduce the independent shared-pool reference

This uses the existing pool constructor only for the implementation under test. Expected outputs come from separate reserve arrays and the 0.3% constant-product equation.

```bash
PYTHONPATH=tests .venv/bin/python - <<'PY'
import itertools
import random
from test_solver import A, B, C, pool
from swaparch.core.types import TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver

rng = random.Random(907)
paths = (
    (("ab1", A.address, B.address),),
    (("ab2", A.address, B.address),),
    (("ac", A.address, C.address), ("cb1", C.address, B.address)),
    (("ac", A.address, C.address), ("cb2", C.address, B.address)),
)
pairs = {"ab1": (A, B), "ab2": (A, B), "ac": (A, C),
         "cb1": (C, B), "cb2": (C, B)}

def reference(routes, amounts, reserves):
    current = {name: list(value) for name, value in reserves.items()}
    total = 0
    for route, amount in zip(routes, amounts):
        for name, source, destination in route:
            i = 0 if source == pairs[name][0].address else 1
            j = 1 - i
            rin, rout = current[name][i], current[name][j]
            received = 997 * amount * rout // (1000 * rin + 997 * amount)
            current[name][i] += amount
            current[name][j] -= received
            amount = received
        total += amount
    return total

for case in range(200):
    amount = rng.randrange(1, 15)
    reserves = {name: (rng.randrange(10, 100), rng.randrange(10, 100))
                for name in pairs}
    states = tuple(pool(name, *tokens, *reserves[name])
                   for name, tokens in pairs.items())
    expected = max(
        [reference((path,), (amount,), reserves) for path in paths]
        + [reference(pair, (x, amount - x), reserves)
           for pair in itertools.permutations(paths, 2)
           for x in range(1, amount)]
    )
    plans = BaselineSolver(intermediates=(C,), grid_parts=amount).solve(
        TradeRequest(A, B, amount), states)
    actual = max(Evaluator().evaluate(plan, states).amount_out for plan in plans)
    assert actual == expected, (case, amount, actual, expected)
print("200 independent exhaustive shared-pool cases passed")
PY
```

Remaining limits are correctly disclosed: at most two distinct paths with at most two hops, a finite allocation grid, only hash-qualified V3 pool math, constant-model gas estimates, and historical quote composition without transfer or settlement validation. Further adapters and the broader solver benchmark remain subsequent milestones.
