# Routing algorithm proposal

2026-09-07. All available sources are considered together in a modular design. This supersedes the earlier three-disjoint-route restriction. No solver or adapter implementation is claimed here; algorithm/library selection remains a proposal.

## Recommendation

Use a pool-level flow optimizer, with a convex CFMM solver for source models that meet its assumptions, and bounded search for discrete/custom behavior. Keep a stateful route-search solver behind the same interface as a baseline and a candidate generator for sources without a valid convex model. Score every final candidate through the same ordered, integer-accurate state evaluator. This is an experimental hybrid router, not a claim of globally optimal routing across arbitrary contracts.

All available, supported sources participate in one candidate universe. The optimizer selects allocations; it does not need to spend through every source. Missing historical RFQ archives, unresolved deployments and unsupported source semantics remain explicit coverage gaps rather than fabricated liquidity.

The objective for a fixed-input WETH-to-USDC trade is to maximize final USDC while spending no more than the specified WETH, using no unprovided intermediate inventory and respecting each source's actual fees, capacity and trade rules. Track residual input explicitly; label partial allocation rather than compare it as a full fill. Report gross output first and gas-adjusted output separately. Preserve other requested tokens, directions and connector candidates.

## Why pool-level optimization

A single best-path algorithm with fixed edge weights misses the way price changes with trade size. Enumerating token paths is useful for candidates, but independently scoring each path also misses shared state.

For example, part of a WETH sale can go straight to USDC while another part goes through USDT. For wstETH, several first legs can merge into WETH before the combined WETH output goes through a common downstream pool. That pool must see the combined trade, or an explicitly ordered sequence of trades against updated state. It must not be quoted twice against its untouched reserves.

Treat each multi-token pool as one venue with coupled trade constraints, not independent pairwise liquidity copies. Shared vault/buffer/withdrawal resources also need one capacity owner. Price, capacity and fees are functions of the pinned historical state.

## What the mathematics supports

For constant-function market makers under the appropriate concavity assumptions, routing without fixed activation costs can be formulated as convex optimization. Fixed execution costs introduce discrete choices and a mixed-integer problem. The 2023 decomposition algorithm separates the global token-flow problem into venue subproblems; this is a useful modular design reference, particularly for networks with many pools and relatively few tokens. [Foundational paper](https://web.stanford.edu/~boyd/papers/cfmm_routing.html), [decomposition algorithm](https://arxiv.org/abs/2302.04938).

The authors provide [CFMMRouter.jl](https://github.com/bcc-research/CFMMRouter.jl). Evaluate it as a solver/reference before writing the numerical core. Its existence does not mean it implements current Curve variants, Fluid, Ekubo extensions, Balancer hooks, every ERC-4626 restriction or RFQ validity. Each proposed model needs source-specific verification. Language choice is not settled; historical acquisition and charting can reuse existing Python independently of the solver.

Use a bounded active-set/beam search to propose combinations involving fixed costs or sources that cannot be represented by the convex model. Re-optimize supported flow allocations for each applicable candidate. A quote-only venue needs a state/capacity-aware candidate evaluator before it can interact safely with shared liquidity. Sampling its quote curve alone does not make it convex or exact. When those models cannot be supplied, report the source as discovered-but-unsupported rather than quietly excluding it from an “all-source” result.

A heuristic result is the best validated candidate found under the recorded search budget. Only claim a convex optimum for a fully supported problem with checked solver convergence and feasibility. It is not an optimum for omitted sources. If using a relaxation as an upper bound, prove it includes all feasible plans in that stated universe; the optimum of a smaller source subset is not an upper bound on the full problem.

## Modular boundaries

- Source adapters own discovery, historical read specifications/decoding, exact quote/state-transition semantics, capacity/resource identity and optional optimization models. Mutable state is owned by the block snapshot/evaluator, not hidden in a global adapter singleton.
- The snapshot store owns immutable source data, block/hash identity and raw evidence. Multicall batches compatible read requests from every adapter; the optimizer does no RPC.
- Solvers consume the same snapshot and trade request and return allocations/candidate plans, search settings and quality information. Swapping a solver does not require changing discovery or charts.
- The evaluator compiles allocation into an ordered plan, applies integer rounding and state changes to shared resources, checks balances/limits, and produces output plus per-step evidence. A separate route validator and the charts consume that result.

Discovery and a source's pricing behavior are separate capabilities. A discovered pool with an unknown hook is visible even if it cannot yet be priced. Pure quote composition is labeled as such; exact local replay is not itself proof of a funded on-chain transaction.

## Execution and validation limits

Network-flow feasibility does not automatically provide an executable ordering. Plans requiring unprovided intermediate inventory or cyclic funding are rejected/classified; do not introduce new flash funding merely to make the optimizer's answer executable. Candidate enumeration starts from the user's short routes but does not permanently restrict the solver to those templates or to three splits. Any practical hop/complexity limit is recorded and sensitivity-tested.

Use the earlier single-pool observation and a best-single-route solver as baselines. Retain baseline candidates so a reported chosen plan cannot be worse on the same objective after exact evaluation. Compare small synthetic instances against exhaustive allocation enumeration. Include a shared-pool case, a hard-capacity PSM case, a multi-token pool case, a wrapper limit, an unsupported hook and a gas-cost case. Check input conservation, output units, infeasible routes, rounding and batched/read parity. Protocol-specific quote evidence remains necessary; solver unit tests do not validate source adapters.

The research interface should let the user scrub through blocks, change size, toggle available sources, switch solvers, and see split/merge flows with before-gas output, gas assumptions and coverage. This is the useful experiment: how routing quality and route composition change through a crash at identical source state.
