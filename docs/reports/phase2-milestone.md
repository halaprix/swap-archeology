# One-block routing milestone — 2026-09-07

The agreed immediate steps 1–5 are complete: phase-1 review repairs, per-pool
qualification, a finite baseline solver, shared-capacity checks, and a working
`swaparch quote` command. Independent review passes are in
[phase1-repair-review.md](phase1-repair-review.md) and [phase2-review.md](phase2-review.md).
This is a historical quote study; no transaction was submitted or simulated as
an atomic combined settlement.

## Reproduce

Run from the project root. The explicit uv cache location is needed in the current
sandbox because the home cache is read-only; `--offline` prevents uv dependency
downloads, not application RPC. The two quote commands below use already saved
discovery, validation, headers and snapshots and made **zero RPC requests**.

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest -q
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline swaparch quote --block 25896003 --in WETH --out USDC --amount 1 --output data/results/phase2/weth-usdc-1.json
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline swaparch quote --block 25896003 --in WETH --out USDC --amount 100 --output data/results/phase2/weth-usdc-100.json
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python scripts/check_selected_routes.py data/results/phase2/weth-usdc-100.json
```

`--amount` is an exact decimal in input-token units; JSON amounts are integers in
smallest units. The command returns nonzero for invalid requests or no feasible
route. JSON includes ordered allocations/steps, source and pool exclusions, failed
candidate diagnostics, block/hash identity, snapshot path and search limits.

## Observed results

Ethereum block **25896003**, hash
`0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`.
All output below is USDC, before gas, with pool fees already included.

| WETH input | Original 0.05% reference pool | Best direct pool | Best tested single path | Best tested split |
|---:|---:|---:|---:|---:|
| 1 | 2393.942838 | 2395.752087 | 2395.752087 | 2395.794890 |
| 100 | 239131.739028 | 239131.739028 | 239240.556009 | 239279.717900 |

The 100-WETH split allocates 30 WETH to the original direct pool and 70 WETH to
WETH→USDT→USDC. Its three pools are distinct. The 1-WETH split allocates 0.8 WETH
to a direct pool and 0.2 WETH to WETH→USDT→USDC, also using distinct pools.
Exact addresses, amounts and outputs are in the saved JSON reports above.

The universe has **23 admitted V3 pools**, each with four passing output and final
price comparisons against QuoterV2 (1 and 100 input-token units in both directions).
The other **29 discovered V3 pools** remain explicitly excluded, including all five
stETH pools pending transfer-semantics validation. A failed size or finite tick
window does not establish absent liquidity. All **15 source families** appear in
the report; the other 14 have no admitted adapter, and RFQ families stay unavailable.

## What changed

- V3 state now rejects holes in its advertised bitmap range; failed bitmap reads
  become classified acquisition failures; the amount0 fallback has checked uint256
  addition. Regression tests include the upstream exact-output clamp vector.
- CSV output reports overlapping matching arithmetic models. It cannot infer the
  original caller mode, price limit, or intervening liquidity events.
- Discovery emits unqualified records. `load_states` requires supported status and
  recorded validation at the exact snapshot hash. The new validator stores raw
  per-pool QuoterV2 evidence under `data/validation/uniswap_v3/`.
- The online checker reuses the project's hash-pinned RPC/Multicall implementation,
  retains actual response provenance, checks final hashes and exits nonzero on a
  mismatch. A window-analysis constructor failure now retains its reason.
- The evaluator rejects stranded intermediates, returned input left unspent,
  fractional/boolean amounts, invalid output amounts and wrong-chain pools.
- `solver/baseline.py` enumerates every available direct and configured two-hop
  route, every pair of routes and both execution orders. For input `N` and grid
  `G`, allocations use the nonzero deduplicated union of `floor(N*i/G)` and its
  complement `N-floor(N*i/G)`. The complement fixes inventory-order dependence
  when integer division rounds. Unsplit candidates remain available.
- `swaparch quote` acquires/loads admitted states, invokes the solver, and evaluates
  the final ordered plans. It includes the original reference pool separately from
  the best direct pool. Selected pool-disjoint paths have an independent checker.

No dependencies, Git repository, commits, pushes, Beads store or remote knowledge
submissions were added. All **56 copied files** still match `COPY_MANIFEST.json`.
Generated `data/adapters-evidence/uniswap_v3/window-analysis.json` and
`swap-event-replay.json` were regenerated; the former was an unintended worker
side effect, disclosed in `v3-fixes.md`. Neither is under the preserved `evidence/`.

## Validation actually run

| Check | Observed outcome |
|---|---|
| Full offline pytest suite | 69 passed, 1 RPC test skipped in the sandbox |
| RPC-marked pytest suite with network access | 1 passed, 69 deselected |
| Focused Ruff checks on integration, solver and checker changes | Passed |
| `uniswap_v3_online_check.py 25896003 23549991` | 18/18 outputs and final prices exact; 19 cast invocations plus RPC requests, mixing cached state and new reads |
| `validate_v3_universe.py 25896003` | 23 admitted / 29 excluded; 21 requests; 92/92 admitted comparisons; 106 raw Quoter requests retained including checks of excluded pools |
| `check_selected_routes.py` on both saved sizes | 5/5 path comparisons each; 1 new request each first run; cached reruns 0 requests |
| Independent cached CLI checks | Both sizes pass with `_rpc` and snapshot persistence patched to fail; 0 requests |
| Fresh Multicall/direct parity using separate caches | 8/8 identical successful reads; 3 batch-client requests and 10 direct-client requests including chain/header reads |
| Independent solver enumeration | Reviewer checked 200 seeded tiny direct/two-hop cases plus 200 with a shared first pool against separate integer reserve math |

The first online command failed at sandbox DNS; the same historical read-only
command succeeded with approved network access. No failed network attempt is
counted as a successful protocol comparison. Reviewers made no RPC calls.

The parity raw evidence is `data/validation/multicall-parity/result.json`, with
separate batch/direct raw caches beside it. It covers six static V3 reads, one
bitmap word and one initialized tick at the calm pin. The selected-route checker
records the SHA-256 of its input report; regenerate that check if the report changes.

The selected checker uses the protocol's
[QuoterV2.quoteExactInput](https://github.com/Uniswap/v3-periphery/blob/main/contracts/lens/QuoterV2.sol).
Because each internal simulated pool swap reverts, it **refuses repeated pools**;
it cannot independently validate updated shared-pool state. The real selected
plans here have distinct pools, while synthetic independent sequential checks
cover shared-state behavior. Neither result proves atomic settlement.

## Review findings resolved

The first independent context reproduced the six original findings and found
three additional boundaries: returned input counted as a full fill, fractional raw
amounts admitted, and an uninitialized window-analysis error. All were repaired
and independently rerun. The second context found the rounded-grid inventory-order
bug, then verified its fix and permutation regression. Neither review leaves an
open confirmed acceptance blocker for this milestone.

## Next work

1. Add the next adapter, preferably Uniswap V2, with discovery, exact reserves/math,
   pinned Router/library comparisons and admission evidence through the same evaluator.
2. Add Curve, LitePSM, wstETH conversion and Origin ARM independently. Verify the
   Lista `get_dy` and ARM usable-inventory claims before using their inventory notes
   as implementation facts; preserve aliases/shared capacities and withdrawal queues.
3. Expand V3 tick coverage where useful and qualify additional pools/blocks. The
   current application admission evidence covers only calm block 25896003; original
   one-pool evidence at 23549991 remains valid but is not a blanket admission for
   all pools or all historical pins.
4. Benchmark a more general solver against this baseline using the same states,
   then run the five pins/windows and build the explorer.

Current search ceiling: at most two paths, two hops per path, configured
USDT/DAI/USDe intermediates, and a finite grid (default 10 parts). No top-K pool
pruning is used, but this is still not the proposed unrestricted flow optimizer.
No global optimum, measured execution gas, atomic executable routing, other-source
integration, full historical sweep or explorer is claimed.
