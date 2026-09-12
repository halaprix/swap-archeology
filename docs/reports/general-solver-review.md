# Independent general-solver review

2026-09-08. Bounded review of `solver/search.py`, `solver/dual.py`, `scripts/benchmark_solvers.py`, the focused tests, and the lead's funded-cycle evaluator correction. Requirements were read from `solver-benchmark-design.md` before implementation conclusions. Review writes are limited to this report; offline experiments used `/tmp`. No RPC or external write was performed.

**Verdict: the solver milestone remains partial.** The tested candidates obey funded execution and exact shared-pool state transitions. Unpruned tiny search and the repaired regular beam can express a four-hop route and a three-branch merge. The numerical solver is a sampled candidate guide with a separate exact search fallback. Neither the full acceptance matrix nor the planned continuous/hybrid comparison is complete, and measured real runs did not improve the retained baseline. No global optimum or numerical upper bound is accepted.

## Substantive findings

### Repaired during independent review

- **V3 domain discovery discarded valid directions when the initial seed was too large.** On calm WETH/USDC `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`, one USDC produced `417298668890995` WETH wei, yet the original domain helper returned zero using the request's raw WETH amount as a USDC seed. The repair searches below a failed initial probe and scales each seed to the input token's decimals. Independently rechecked USDC capacity is `33039737212565`, returning `10321043513825874319744` WETH wei; capacity plus one rejects missing bitmap word 68, with loaded words 69–85. This is a verified local quote domain, not a settlement capacity.
- **V2 support discarded the whole direction when the unconstrained stationary point exceeded the uint112 reserve domain.** A state with reserves `(UINT112_MAX - 1000, UINT112_MAX)` accepted a profitable small quote but returned no support action. The repair clamps input to the remaining uint112 reserve room. With both reserves bounded by uint112, resulting V2 multiplication intermediates also remain inside uint256. The targeted boundary regression passes.
- **`exhaustive=True` still applied beam pruning.** The repair bypasses trimming in exhaustive mode and exposes expansion truncation. Exhaustiveness is only over every positive integer action within the configured step count, and only when the expansion limit was not reached. It is not an unrestricted optimum claim.
- **Generic state deduplication assumed `repr` contained the complete state.** The implementation now avoids that assumption for arbitrary `PoolState` implementations. The V2/V3 dataclass key retains balances, venue state and depth.
- **A larger amount grid discarded useful incumbent allocations.** Symmetric V2 reserves 10000/20000 with input 1000 gave 1898 at grid 2 but 1889 at grid 3. A preliminary dyadic fix still lost the 1/3 allocation: capped rate-3 inventory of 333 plus a rate-1 fallback gave 1666 at grid 3 versus 1500 at grid 4. The implementation now accepts explicit incumbent plans and freshly reevaluates them when changing budgets, and proposes an observed finite-input boundary. A fresh run with changed beam/grid budgets is not inherently monotonic; pass prior incumbents to preserve their output.
- **Funded cycles returning to the original input were rejected as excess gross turnover.** The lead removed gross turnover accounting from evaluator and search. The evaluator still begins with only the requested holdings, forbids spending unheld inventory and requires zero original/intermediate residual. The funded `100 A -> 98 B -> 98 A -> 96 C` regression passes along with the existing partial-fill, unheld-token, residual and shared-owner rejection tests.

- **Beam ranking missed a valid three-branch merge and compared raw token quantities.** The original width-512 beam returned no plan for the supplied three-branch fixture despite an unpruned optimum of 24. Backward local quote values now guide ranking in normalized token units, with rational arithmetic preserving exact ties for tiny 18-decimal quantities. The reviewer reran the original fixture with both solvers: each now returns 24 through three upstream branches and a final downstream input of 24. This fixes the concrete miss; the heuristic remains a finite beam, not a bound or exhaustive search.
- **Recovery refusals disappeared after candidate filtering.** Search now retains terminal evaluator refusals and exposes expansion/beam diagnostics even with no winner. DualSolver propagates those diagnostics from both modeled recovery and its no-model fallback. Independent duplicated-owner A→B/B→C cases now expose `shared capacity not modelled` in both branches; a no-winner, one-expansion run explicitly records truncation. `recovery_errors=[]` before reaching terminal depth still does not establish successful recovery; read the truncation flag too.
- **Sparse V3 sampling omitted useful request-sized amounts.** The implementation now includes the per-token scaled request seed and geometric samples below/above it, as well as the finite uniform grid and accepted domain edge. Pool/direction identities are retained in domain profiles. This improves candidate coverage but remains a sampled exact-quote model.

### Remaining acceptance gaps

1. **The implementation is not the planned continuous V3/heterogeneous dual.** V3 uses finite exact-quote samples; V2 uses a continuous stationary-point proposal followed by an integer quote. Finite PSM/wrapper/custom states are explicitly excluded from the dual model and retained for fallback search. No tied-interval recovery LP, continuous V3 tick support, or recovered continuous feasible-flow calculation exists. The current `numerical_dual_estimate_not_a_bound` label is appropriate.
2. **The full comparison matrix remains incomplete.** Missing work includes finite fixture fees and exact cap −1/cap/cap +1 comparisons, wrapper output-floor/queued-direction cases, complete local V3 tick-boundary comparisons, both-solver matrix coverage, explicit net/gross incumbent selection and the published real budget sweep. This does not require the pending historical LitePSM adapter: synthetic fixtures are sufficient for these solver checks. Gas remains separate; existing tests show differing integer action costs can change ranking and unknown gas remains unknown, but do not constitute the planned gas-aware solver comparison. The inspected benchmark deliberately loads V2/V3; it cannot establish an all-source result once other source adapters are admitted.
3. **Benchmark evidence is still too narrow to select the continuing solver.** The refreshed stress manifest now includes core solver/evaluator/lock hashes, Python identity, endpoint scales, exact plans, explicit null net output, and recovery/truncation diagnostics. Both new methods remain truncated and match the incumbent on the measured real case. Full dependency versions, all intermediate scales, adapter code identity and the full configuration sweep are not yet recorded as the design requires. SciPy reports relative-objective-reduction termination in the final stress run with raw gradient infinity norm about 25581.29; that optimizer success flag is not proof of a stationary solution, accurate continuous relaxation or executable recovery quality.

## Independent checks

The following checks were run without invoking the solver's quote helper as the reference:

| Check | Observation |
| --- | --- |
| Two V2 direct pools, input 1–20 | Both solvers matched exhaustive integer allocation using `floor(997*x*Rout/(1000*Rin+997*x))`; 40 cases with 18/18 decimals and another 40 with 6/18 decimals. |
| Three-pool triangular V2 graph, input 1–6, at most three steps | An independent DFS carried integer balances and updated reserve tuples over every positive integer action. Search matched optima 4, 8, 11, 13, 14, 15; reference enumeration visited 11, 69, 232, 580, 1112, 1919 nodes respectively. Beam width 1 did not prune exhaustive mode. |
| Three branches and merge | Unpruned search achieves 24 with three upstream swaps followed by one downstream swap of all 24 intermediate units. Both repaired beam-mode solvers independently recover the same 24 at four steps, width 512, 50000 expansions and grid 32. |
| Only four-hop route | Both beam-mode solvers recover 48 from input 3 across four successive rate-2 conversions. |
| Finite input cap plus fallback | Both beam-mode solvers recover 12 from input 7, using the cap-5/rate-2 source plus rate-1 fallback. |
| Three-token inventory owner | Focused tests verify sequential owner state changes and refuse a duplicate owner split across distinct pool ids. |
| Funding and unsupported hook | Focused tests reject unfunded circulation and hook-bearing states without fabricating AMM math. Funded cycles are now accepted only from actual prior holdings. |
| Real V3 domain | Capacity and capacity-plus-one tested independently against cached exact state as described above. |

Synthetic pools are solver fixtures, not historical source support. Every accepted solver plan is freshly evaluated; the dual objective alone is never used as an executable output.

## Cached real experiments

Both solvers received the same admitted states as the baseline. The benchmark uses only the snapshot store and adapter loading; it has no acquisition/RPC path. The first experiment exercises the full calm universe even though its winner is direct. The second exercises a mixed V2/V3 stress universe with a four-step winner.

| Pin / input / budgets | Admitted states | Baseline | Stateful search | Numerical guide + search |
| --- | ---: | --- | --- | --- |
| 25896003 / 1 WETH / steps 4, beam 32, expansions 500, grid 2 | 34 | 2395752087 USDC units; 0.384 s | Same output; 0.895 s; truncated | Same output; 3.880 s; truncated |
| 24356381 / 100 WETH / steps 8, beam 128, expansions 5000, grid 8 | 35 | 232060259418 USDC units; 5.383 s | Same output; 11.722 s; truncated | Same output; 15.774 s; truncated |

These are gross outputs in six-decimal USDC smallest units. Runtime is local wall-clock observation, not a performance guarantee. These bounded runs do not establish the new methods' superiority; retaining the incumbent prevented a lower selected result. The calm row is an earlier review experiment. The stress row was rerun after the final fixes; all five recorded solver/evaluator/lock hashes independently matched the current files. Its SciPy objective is 1143226.6171888164 output-token units, recorded separately from exact recovery; it is not a certified upper bound. This limited evidence is not a complete benchmark release.

Exact commands:

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark pytest tests/test_general_solver.py tests/test_evaluator.py -q
# 24 passed in 4.93s on the final repaired slice.

UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark python scripts/benchmark_solvers.py \
  --block-hash 0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5 \
  --amount 1000000000000000000 --max-steps 4 --beam-width 32 --max-expansions 500 --grid-parts 2 \
  --output /tmp/general-solver-review-bench-500.json > /tmp/general-solver-review-bench-500.stdout

UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark python scripts/benchmark_solvers.py \
  --block-hash 0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb \
  --amount 100000000000000000000 --max-steps 8 --beam-width 128 --max-expansions 5000 --grid-parts 8 \
  --output /tmp/general-solver-review-final-stress.json > /tmp/general-solver-review-final-stress.stdout
```

The next reviewable result is a complete frozen manifest and both-solver acceptance table with budget truncation and measured quality gaps visible, followed by the missing synthetic/domain cases. Until those exist, retain the finite baseline incumbent and describe the new code as a candidate-search prototype.
