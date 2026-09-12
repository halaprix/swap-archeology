# Phase 3: the smallest useful solver comparison

2026-09-07. Design only: no solver benchmark, dependency installation, node call, or adapter qualification was performed for this report. The recommendation is an implementation task, not a numerical result. This preserves the full routing scope in `ROUTING_ALGORITHM.md`.

Implement a Python price-dual candidate generator and an unrestricted-by-topology stateful beam search behind the existing `Solver.solve(request, states)` interface. Compare their **integer-evaluated plans on the identical admitted states**, retain the current baseline as an incumbent, and report the continuous objective separately. First run the existing qualified V3 snapshot; add V2 when independently qualified, and synthetic finite PSM/wrapper/multi-token fixtures immediately. Synthetic fixtures test the optimizer, never establish historical source support.

## Current implementation constraints

- `solver/baseline.py` is a finite grid over at most two paths of at most two hops. `_compile` threads repeated pool state, but executes whole paths serially; it cannot propose a merge followed by one combined downstream swap. Its ceiling must remain a named baseline configuration, never the final universe definition.
- `Evaluator.evaluate` starts with only the requested input, checks every step's held balance, threads `swap()` state by pool id, and rejects stranded intermediate assets. Keep these checks authoritative. It currently sums **every** spend of the original input token, so a funded route which returns to that token and spends it again can fail despite valid balances. Either label that additional restriction explicitly for both solvers or have the lead separate initial allocation accounting from token turnover before testing such cycles.
- `_shared_capacity_reason` rejects two distinct pool ids declaring the same capacity id. This is safe refusal, not implemented shared-vault consumption. One multi-token state serving several pairs can share state today; two independent adapter states drawing one vault require a coordinated capacity owner before admission. Do not remove the refusal to make a benchmark pass.
- `UniV3State` rejects missing bitmap words, missing initialized-tick liquidity, and unconsumed input at the protocol price limit. The continuous model must preserve those finite domains; `slot0` plus current liquidity is insufficient.
- The lead identified and is repairing the old baseline's incorrect default USDe address. Older phase-2 path coverage does not establish actual USDe-intermediate enumeration. Record the final code/input identity when rerunning.

## One experiment, three result classes

Persist one benchmark manifest per run: block number/hash, snapshot identity, request in raw units, complete admitted pool ids, exclusions with reasons, per-venue direction/domain limits, solver configuration, code identity, dependency versions, and deterministic seed. Both solvers receive the same immutable `tuple[PoolState, ...]`; neither performs RPC. A missing optimization model must be reported by venue and handled in the hybrid search, rather than silently dropped from an all-source result.

1. **Exact executable candidate:** final `Plan` and its fresh evaluator result; full allocation and no intermediate inventory. This is local replay feasibility, not settlement evidence.
2. **Continuous model estimate:** a floating-point model objective, numerical convergence information, and model coverage. It may concern a subset or a restricted ordering class.
3. **Certified upper bound:** permitted only when every feasible plan in the stated comparison class maps into the relaxation and every local support-function value is an upper enclosure. Solver `success=True` alone establishes neither condition.

Run gross output first. Gas-aware experiments use the same candidate plans, separately recording estimated gas units, historical gas price and an explicit ETH/output conversion. Missing gas or conversion inputs mean `net_output=null`, not zero cost. The existing V3 flat 120,000-gas estimate is a ranking assumption, not measured execution gas.

## Price dual to implement

The decomposition reference supplies venue arbitrage subproblems and a price-space objective; its benefit is that numerical dimension follows tokens, rather than the number of pool allocation variables. It assumes closed convex trading sets containing zero. Its L-BFGS-B approach requires no generic on-chain pricing function inside the numerical solver. [Diamandis et al., sections 1–2](https://arxiv.org/pdf/2302.04938).

The following is the proposed project formulation. Work in explicitly scaled token units **only inside the numerical model**: one scale per token, recorded in the manifest. Never round-trip a raw integer through a float to build a `Step`.

Let `b = Q e_s` be the trader's initial holdings, `t` the output token, `z_i` the trader's net receipt vector from venue i, and `A_i` its local-to-global token map. A deliberately relaxed primal is

```text
maximise       y_t
subject to     y = b + Σ_i A_i z_i ≥ 0
               z_i ∈ T_i
```

It permits residual assets, ignores execution ordering and does not require spending all input. Those relaxations make it useful as a possible bound on full-fill ordered plans; they do not turn its answer into a valid route. With `c=e_t`, the dual is

```text
minimise       D(p) = p·b + Σ_i h_i(A_iᵀp),       p ≥ c
where          h_i(v) = sup { v·z : z ∈ T_i }.
subgradient    b + Σ_i A_i z_i*(p).
```

For any represented feasible holdings, `y_t ≤ p·y ≤ D(p)`. Thus a **correctly evaluated** dual value bounds this relaxation even before optimizer convergence. The input balance appears in the objective, not as invented intermediate endowments. A custom fixed-full-fill equality formulation is possible later; using this simpler bound keeps the first experiment small.

A local model returns `(support_value, net_trade, domain_description, certificate_status)`. This can be an internal function/dataclass in the numerical module; it does not require extending `PoolState` or adding a plugin framework. A model cannot be synthesized merely from the presence of `quote_exact_in`.

Use positive numerical price bounds where division requires them, documenting that restriction. Prices set to zero require analytic limiting cases. Artificial upper bounds on prices can weaken a dual result, but must not create a false convergence claim. Report primal balances, projected gradient, termination reason, iterations, runtime, and recovery losses separately.

### Venue subproblems

**V2.** For raw input x, reserves `(R_in,R_out)` and retained-input fraction γ, the continuous directional output and stationary point are

```text
f(x) = R_out γx / (R_in + γx)
x*   = max(0, (sqrt(v_out R_out γ R_in / v_in) - R_in) / γ).
```

Clamp to the verified input domain; compare both directions and zero. Exact evaluation uses integer division and adds the full input to reserves. This follows the canonical 997/1000 exact-input formula, while the real adapter must separately qualify token transfer behavior and historical pool identity. [Uniswap V2 library](https://raw.githubusercontent.com/Uniswap/v2-periphery/master/contracts/libraries/UniswapV2Library.sol).

**V3.** Reconstruct contiguous sqrt-price intervals from the loaded bitmap and `liquidityNet`, starting with the current active L. For a downward segment from `s_a` to `s_b` with constant L:

```text
net token0 input = L(1/s_b - 1/s_a)
token1 output   = L(s_a - s_b)
gross input     = net input / γ.
```

For an upward segment, net token1 input is `L(s_b-s_a)` and token0 output is `L(1/s_a-1/s_b)`. Traverse adjacent intervals in price order, updating L at initialized ticks; a zero-liquidity interval cannot invent liquidity. Given prices, stop where the marginal output value equals marginal input cost, at an interval boundary, or at the verified domain edge. Evaluate both directions and the no-trade fee band. This is a continuous candidate model; integer per-step fees and sqrt-price rounding remain the evaluator's job. [V3 whitepaper, section 6](https://app.uniswap.org/whitepaper-v3.pdf), [canonical swap loop](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/UniswapV3Pool.sol), [per-step fee arithmetic](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/libraries/SwapMath.sol).

Finite bitmap coverage does **not** justify extending L to price zero/infinity. Preserve exact endpoint semantics: a move landing on a known boundary can be feasible while the next positive increment requires an unloaded word. Save continuous domain edges and the maximum independently replayable integer sizes in both directions. Check just below, at and above each loaded boundary and initialized tick; do not assume the continuous limit itself is an accepted integer input. All pieces belong to one pool, not independently funded directional or tick pools.

**Finite PSM / fixed-rate wrapper fixture.** With certified constant rate r and finite input limit C, use `0≤x≤C`, `0≤y≤rx`; its support optimum is x=C if `v_out r>v_in`, x=0 if the inequality reverses, and any x in `[0,C]` at equality. Endpoint-only oracle choices at this kink can leave a flow imbalance even at optimal prices. Preserve the tied interval for primal recovery. Model directional enabled flags, fees and inventory separately. Do not generalize this fixture to arbitrary ERC-4626 implementations: virtual shares, fees, previews, owner limits, supply changes and cooldowns require their own verified model. Shared redemption/PSM inventory is constrained once through its owner; queued exits are unavailable as immediate edges.

**Multi-token or coupled resources.** The local subproblem maximizes one joint `v·z` over the owner's trade set. Pairwise views may be used to enumerate actions, but never independently duplicate reserve/capacity bounds. Until an owner model exists, the shared-id refusal remains visible. A synthetic three-token finite-inventory state is enough to test this plumbing; it does not qualify a Balancer or Curve deployment.

## Integer recovery and stateful search

Do not round each dual venue trade and call the resulting list a plan. Recovery is a separate fallible stage:

1. Recover continuous feasible flows by combining saved local maximizers near the best dual iterate. Include tied PSM/wrapper intervals; solve a small flow LP over convex combinations if needed. This is an inner candidate approximation, not a new upper bound. Recheck the continuous model after recovery.
2. For an acyclic directed candidate, process tokens topologically. Execute funded upstream actions first, merge their **actual integer outputs**, and distribute held balances across downstream actions. Floor proposed shares with integer/rational arithmetic, assign the integer remainder to a feasible child, and try alternative remainder assignments near tight capacities. Requote every action against the current shared state. Aggregate compatible same-pool same-direction actions when their funding is available together; compare against serial execution where fees/limits make those different.
3. For a cyclic candidate, first try actions whose entire input is already held. Backtrack or split actions within the recorded recovery budget. A funded cycle can be feasible; a graph cycle alone is not rejection. If no remaining positive action can be funded, record `cyclic_or_unfunded_recovery`, retain the incumbent, and never add a flash loan or intermediate seed. This failure does not prove that every ordering is impossible.
4. Accept only a fresh evaluator full-fill result. Missing ticks, integer leftovers, shared-capacity rejection, or unsupported directions are explicit failures. Report continuous objective, recovered output, amount spent and recovery loss independently.

The stateful baseline advances `(balances, current venue states, ordered steps)` by positive integer exact-input actions from **held** tokens. This naturally supports split, merge and interleaving, including a shared downstream pool. Generate actions from a recorded amount grid, full held balances, capacity boundaries, and dual suggestions; retain baseline completions. Bound expansions/beam width/step count, not a permanent number of paths. State deduplication must include venue state and balances; equal token balances with different pool reserves are different nodes. Avoid speculative dominance pruning without proof.

Use initial step budgets 4/8/12, beam widths 32/128 and grids 8/32 on the real replay. They are experimental budgets, not coverage claims. An exhaustive tiny run enumerates every positive integer action up to its published maximum step count, without beam pruning. A seeded improvement graph with three branches and a route longer than two hops must remain expressible; the old two-path/two-hop result stays as a baseline only.

## Bound validity and model mismatch

A price-dual optimum on only V2/V3 is **not** an upper bound for a universe also containing supported PSM/wrapper/custom venues. Nor is independently quoting several paths through one untouched pool a relaxation proof. A subset's exact feasible candidate is a lower bound on achievable output, while that subset's continuous estimate need not bound the full universe in either direction.

For a valid bound, establish that each admissible ordered plan's **aggregate net change per capacity owner** lies in `T_i`. A one-shot fee-charging trade model does not establish this for arbitrary repeated/reverse visits or a replenishable PSM. Start by labeling these results `continuous_model_estimate`; upgrade only after a venue-specific aggregate containment argument covers the stated action class. A fee-free invariant relaxation can be a conservative alternative for V2 repeated trades, but keep its looser value and proof distinct from the fee-aware candidate model.

Floating-point support evaluation is another independent condition. An approximate local maximum can understate h_i and invalidate an upper bound. For the first artifact report `numerical_dual_estimate`, unless using independently checked rational/Decimal outward enclosures or a proven tangent-envelope bound with accumulated error. Agreement on synthetic examples is evidence, not a universal numerical certificate. Real V3 integer crossing/fee behavior especially needs boundary checks before promoting the continuous curve to a bound.

## Concrete acceptance matrix

Every row runs both solvers on identical states and compares **fresh exact evaluator** output. Every fixture reports all budgets; exhaustive optimality is only over its enumerated integer action set and maximum step count.

| Fixture | Independent oracle / required observation |
|---|---|
| Two direct constant-product pools, input 1..20 | Enumerate all integer allocations with the canonical rational formula; retain unsplit winner; decimals 6/18 remain correct. |
| Split then merge | Three upstream branches feed one B→C pool. Enumerate branch allocations and legal orderings; downstream receives combined actual B. Include a case where all three branches improve output. |
| Longer route | A→B→C→D→output is the only feasible full-fill route; result cannot depend on the old two-hop cap. |
| Shared pool repeated | Enumerate sequences against changing reserves; compare combined versus sequential swaps; independently quoting the initial pool twice must fail the expected-output check. |
| Finite PSM | Capacity C−1/C/C+1, nonzero fee and disabled reverse direction. Two routes sharing the owner cannot each claim C. |
| Wrapper limit | Exact input cap, output-floor rounding, disabled/queued redeem. Insufficient cap forces another branch; fixture rate is never called historical ERC-4626 support. |
| Three-token venue | A→B and A→C use the same inventory owner; subsequent B→C sees updated inventory. A duplicated-pair-state control must be rejected. |
| Cyclic funding | Net-balanced B↔C circulation with no B/C holdings cannot execute; a separately seeded cycle is tested only with holdings genuinely obtained from the request. |
| Unsupported hook | Discovered hook-bearing pool is retained in exclusions, neither solver invokes a fabricated constant-product quote. |
| Gas | Higher gross split loses after a fixed extra action cost; unknown gas stays unknown. Use explicit integer output-unit cost, test gross and net incumbents separately. |
| Real qualified V3 | Reproduce saved QuoterV2 anchors, both directions, multi-tick crossing, loaded-word boundary and exhaustion, plus a same-pool multi-action plan. Compare continuous predictions with exact results without treating approximation as adapter qualification. |
| Real V2 after admission | Same-block qualified reserves and canonical exact quote; joint V2+V3 requests in one universe. No historical V2 label before qualification. |

Synthetic complete enumeration should match an unpruned search configured for the same domain. Heuristic runs may miss it: report the exact output gap instead of disguising a miss as a failed adapter. Recovery must never invent inventory or report an infeasible candidate as the winner. Enabling extra budget/candidates retains the previous incumbent, so exact selected output cannot deteriorate on the same objective.

## Dependency and reference decision

Current source inspection confirms CFMMRouter.jl provides `ProductTwoCoin`, `GeometricMeanTwoCoin`, `UniV3`, and a `Swap` objective. Its router uses LBFGSB, exposes continuous trades/net flows, and does not compile this project's ordered integer plans. The `UniV3` constructor's liquidity vector is the bounded-product invariant k=L²; its final lower-price helper returns zero. Porting a partial tick window without explicit terminal bounds is unsafe. References were read from upstream `main` on 2026-09-07; a numerical reference run must pin a commit/package version first. [Venue implementation](https://raw.githubusercontent.com/bcc-research/CFMMRouter.jl/main/src/cfmms.jl), [router](https://raw.githubusercontent.com/bcc-research/CFMMRouter.jl/main/src/router.jl), [objectives](https://raw.githubusercontent.com/bcc-research/CFMMRouter.jl/main/src/objectives.jl).

Observed locally:

```text
.venv/bin/python: Python 3.14.4; scipy=False, numpy=False, cvxpy=False
python3:          Python 3.14.4; scipy=False, numpy=True, cvxpy=False
command -v julia: no executable found
```

A pre-existing uv cached environment successfully ran a standalone L-BFGS-B smoke check:

```text
<benchmark-environment>/bin/python
Python 3.11.15 / SciPy 1.17.1 / NumPy 2.4.6
minimise (x-2)^2, x>=0, initial x=0: success=True, x=[2.000000001052712]
```

This supports a temporary standalone numerical experiment without installation. It is not a project-compatible dependency: this project requires Python >=3.12, and inspected cached SciPy wheels target CPython 3.10/3.11. Do not graft them into the 3.14 environment or silently run the project under 3.11.

For the integrated benchmark, a declared, locked optional SciPy dependency compatible with the project interpreter is justified: reuse `optimize.minimize(method='L-BFGS-B')` and `linprog` for candidate flow recovery rather than implementing another optimizer. Report optimizer tolerances and failure reasons. PSM and wrapper support functions are nonsmooth; L-BFGS-B is an experiment there, and a failure must remain visible rather than be called convergence. [SciPy optimizer reference](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-lbfgsb.html).

## Bounded implementation assignment

Own `solver/dual.py`, `solver/search.py`, one offline benchmark script and one focused test module; coordinate any core-interface/evaluator fix with the lead. Reuse `Plan`, `Step`, existing `swap()` and `Evaluator`. Implement V2 and loaded-domain V3 local numerical subproblems, finite fixture models, funded recovery, then run the acceptance matrix and saved V3 replay. The script emits comparative JSON plus a short report with quality/runtime/coverage/recovery failures. Do not build a transaction encoder, acquisition path or new adapter framework for this experiment.

Choose the continuing solver from measured exact candidate quality and runtime. If dual recovery loses often on heterogeneous venues, keep it as a candidate guide/conditional bound and use stateful search for executable winners. This decision does not remove any requested source family, split/merge capability, historical sweep or explorer deliverable.
