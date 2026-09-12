# Phase 2 baseline solver

`BaselineSolver` implements the `Solver` protocol with integer-only candidate construction.
It enumerates every direct pool and every two-hop pool pair through configured intermediate
tokens (default Ethereum USDT, DAI, and USDe), then tests every pair of those routes at a
finite split grid. Each ordering is compiled against the updated immutable pool state. The
`Evaluator` replays and ranks candidates, rejects unmodelled shared capacity and stranded
balances, and determines the reported output.

The solver retains the best feasible candidate of each kind: `direct`, `path`, and `split`.
`search_info` contains `kind`, each route's pool-id path and input allocation, plus
`path_count`, `max_paths: 2`, `max_hops: 2`, and `grid_parts`. If no two-route candidate
beats an unsplit route, the `split` result is that one-route fallback with `path_count: 1`
and `fallback_kind`; its allocation remains truthful. `unsupported` is reset for every
`solve()` call and holds JSON-safe dictionaries with `path`, `pool`, `input`, and `reason`.

This is deliberately not a global optimum: it considers at most two routes, at most two hops,
and only the configured finite integer grid. It does not establish atomic execution.

For input `N` and `grid_parts` `G`, split candidates use every non-zero allocation in
`{floor(N*i/G), N-floor(N*i/G) | 1 <= i < G}`. Each allocation is assigned to either route and
tested in both execution orders, so rounded grid points do not depend on pool enumeration order.

Validation command:

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest tests/test_solver.py
```

The solver rejects non-integer (including boolean) request amounts and grid counts. The
focused checks use an independent `Fraction` constant-product reference over every integer
allocation including endpoints, plus odd-grid, capacity, shared-state, and later-candidate
coverage cases.
