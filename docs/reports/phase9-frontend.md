# Phase 9 — local frontend acceptance, 2026-09-08

Implemented with Next.js 16.3.4, React 19.2.8, CSS design tokens and reusable
primitives. Integration review corrected responsive layouts and reused the shared
filter helper; tests import actual source helpers. No deployment, commits or pushes.

## Delivered

- Historical explorer: 268 real saved reports at five pins, exact pair/amount/
  solver filtering (including saved dual reports), source coverage, explicit
  no-route, discrete historical observations, ordered split/merge flow and ledger.
- Quote Lab: token in/out, decimal amount, block number or cached hash, bounded
  baseline/search, optional sources. Results persist and can be reopened.
- Design system: CSS variables, shared UI primitives, `/design-system` gallery,
  frontend/DESIGN_SYSTEM.md and bundled Next.js agent documentation.
- Production frontend packages its catalog. No Python process, workstation mount,
  archive RPC or credentials are needed for historical browsing.

## Verified

- Python suite: 241 passed, 2 skipped. Web API/export tests cover malformed inputs,
  missing/unqualified state, cache behavior and conflicting pinned identities.
- Frontend actual-source tests pass in all three test files. Graph inventory
  replay covers all 266 feasible catalog reports: no input shortfall and exact
  terminal output equals recorded best_split. Two no-route reports remain null.
- TypeScript, ESLint and Next.js production build pass.
- Real HTTP baseline quote at block 25896003: 1000 USDC gives 1000.305244 USDT;
  identical replay returns the cached result. Missing block 1 and unqualified
  block 24356377 return explicit 409 errors.
- Real browser form: 1000.123456 USDC gives 1000.428737 USDT at block 25896003.
  Exact raw output 1000428737 displayed and restored by reopening after reload.
  100 WETH amount filter excludes other sizes; incompatible pair yields a
  disabled empty report selector; saved dual solver is selectable.
- Desktop 1440px and mobile 390px inspected. Mobile navigation and both quote
  columns stay inside the viewport; result follows form vertically.
- Production server without SWAPARCH_API_URL: all three pages and catalog serve;
  health and quote endpoints explicitly return 503. Browser has no runtime errors.

Evidence: assets/phase9/ contains browser/API/production check JSON and desktop/
mobile screenshots. Temporary local smoke scripts are under /tmp/phase9-*.mjs;
repeatable helper tests are under frontend/tests/.

## Limits and next deployment step

Quote Lab currently uses qualified **cached** state only. An arbitrary uncached
block needs a separate acquisition/qualification step. Phase-8 collection remains
in progress and its offline scenario sweep has not run. Existing reports show
actual source coverage, including exclusions; no all-source or global-optimality
claim is implied. Historical observations match pair, size, solver and sources,
but old report projections do not preserve all search-budget metadata.

For later Vercel deployment, select frontend as root with the Next.js preset.
Saved browsing is self-contained. Ad hoc computation needs a separately hosted
Python API with snapshots and appropriate access controls; configure its URL as
server-only SWAPARCH_API_URL. Localhost is only for local development.
No hosted end-to-end deployment or formal accessibility certification was done.

## Route visual revision

User reference applied: dark dotted panel, thick venue-colored curved bands,
input left, intermediate operations in dependency columns, output right.
Three-step split regression checks columns [1,1,2]; actual browser verifies all
five connections advance right and the indirect branch retains 75% input share.
Frontend tests/typecheck/lint pass; desktop rendering and mobile scroll checked.
