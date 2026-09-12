# Infrastructure phase 1 report

## Status

The bounded implementation is complete. Offline behavior, ABI encoding, cache sharing,
snapshot persistence, evaluator semantics, CLI registration, endpoint redaction, and lint all
pass. Live archive-RPC parity is not verified because this execution sandbox cannot resolve the
configured RPC host. No live responses or cache hits were fabricated.

## Files changed

- `src/swaparch/rpc/client.py`
- `src/swaparch/rpc/multicall.py`
- `src/swaparch/rpc/headers.py`
- `src/swaparch/snapshot/store.py`
- `src/swaparch/evaluator/evaluate.py`
- `src/swaparch/evaluator/fakes.py`
- `src/swaparch/cli.py`
- `pyproject.toml` (`[project.scripts]` only; required dependencies were already present)
- `tests/test_evaluator.py`
- `tests/test_multicall_encoding.py`
- `tests/test_pins_rpc.py`
- `docs/reports/infra-phase1.md`

No git command, initialization, commit, or push was performed. Evidence and shared interfaces
were not edited.

## Implemented

- `RpcClient` uses `requests.Session`, loads `RPC_MAINNET` from the environment or the configured
  dotenv file, retries retryable transport/HTTP/RPC failures with integer exponential backoff,
  redacts endpoint components, counts HTTP attempts, caches block-hash-pinned calls, resolves and
  caches headers, supports EIP-1898 block-hash calls with verified number fallback, and chunks logs
  with range-error halving and end-block hash validation.
- `Multicall3` encodes/decodes `aggregate3((address,bool,bytes)[])`, defaults to 200 calls, halves
  failed chunks, retries failed subcalls directly, records `via`, and writes each subcall through
  the direct-call cache key.
- `SnapshotStore` writes `<root>/<chain>/<blockhash>/header.json` and `calls.json.gz`, provides
  zero-RPC loading and `(to, data)` lookup, and extends only missing identities.
- `Evaluator` executes immutable pool states in order, threads balances, rejects unheld inventory,
  reuses updated state for repeated pool IDs, refuses overlapping capacity IDs across distinct
  pools, reports residual input, and sums gas only when every executed step supplies it.
- The test-only constant-product state uses integer floor arithmetic and a 30 bps fee.
- The CLI exposes `header`, `pins-check`, and `snapshot-build` through the `swaparch` script.

## Acceptance commands and output

The commands below were entered exactly as specified. The shell first set
`UV_CACHE_DIR=/tmp/swaparch-uv-shared` and `UV_OFFLINE=1` because the sandbox's default uv cache is
read-only and package-network access is disabled. The project environment selected by bare
`uv run` is CPython 3.14.4; an isolated CPython 3.12.3 sync was attempted but the locked CPython
3.12 wheel set was not fully cached and cannot be downloaded in this sandbox.

### `uv run pytest -q`

```text
.........s...................................                            [100%]
44 passed, 1 skipped, 1 warning in 1.56s
```

The warning is `PytestUnknownMarkWarning` for `rpc`. Registering it in pytest configuration would
require a disallowed non-script edit to `pyproject.toml`.

### `uv run pytest -q -m rpc`

```text
s                                                                        [100%]
1 skipped, 44 deselected, 1 warning in 0.51s
```

Skip reason: `requires -m rpc and a resolvable RPC_MAINNET`. The dotenv value was found, but DNS
resolution is unavailable in the sandbox.

### `uv run swaparch pins-check` (first run)

```text
swaparch: RPC_MAINNET endpoint is not reachable
```

Exit status 1; 0 HTTP requests after the DNS preflight.

### `uv run swaparch pins-check` (second run)

```text
swaparch: RPC_MAINNET endpoint is not reachable
```

Exit status 1; 0 HTTP requests after the DNS preflight. The required successful first population
and zero-request cached replay therefore remain unverified.

An earlier live attempt, before adding the DNS preflight, made 6 retry attempts. All failed during
name resolution before receiving an RPC response. Total attempted HTTP requests for this task: 6,
well below the limit of 300.

## Supplemental checks

```text
$ uv run ruff check <owned source and test paths>
All checks passed!

$ uv run python <cache-only snapshot check>
snapshot build/load/extend: ok

$ uv run python <integer AST, multicall fallback/cache, endpoint artifact check>
integer-only AST, multicall fallback/cache, endpoint artifact scan: ok

$ uv run swaparch --help
usage: swaparch [-h] {header,pins-check,snapshot-build} ...
```

The Multicall check independently exercised one successful batch result, one failed subcall with
direct fallback, shared per-subcall cache entries, and a second pass with no additional fake
network calls. The snapshot check built, loaded without a client, performed tag-independent lookup,
and extended one missing call. An AST check found no float constants in RPC, snapshot, or evaluator
modules. The endpoint-artifact scan found no configured URL in owned source, tests, or RPC cache.

## Independent evidence

- `tests/test_multicall_encoding.py` compares one WETH/USDC pool `slot0()` subcall against literal,
  hand-computed `aggregate3` calldata and a literal encoded return value.
- `tests/test_evaluator.py` checks known integer outcomes and behavioral inequalities through the
  public evaluator and immutable fake-pool interfaces, including worse output after repeated use of
  the same pool state.
- `evidence/crash-rescue-simulation/standing-prices.json` supplies five independent block hashes and
  seven saved call responses per block. The RPC test and CLI consume those values, but byte-for-byte
  live parity could not run without DNS access.

## Limitations and proposed interface changes

- Live block-hash, direct-call, Multicall3, and second-run cache parity must be rerun in an environment
  that can resolve and reach the configured archive endpoint.
- Phase 1 intentionally refuses distinct pool IDs that share a capacity ID; phase 2 must model that
  shared state before such plans can be feasible.
- `PoolState.swap()` does not expose a fee amount, so evaluator `StepResult.fee_paid_in` is `0`, as
  permitted for venues that do not expose it. If reports require exact per-step fees, the lead-owned
  interface should add fee metadata to the swap result rather than infer it in the evaluator.
- No shared-interface edit is otherwise required.

## Independent verification (2026-09-07)

`resolve_rpc_url` was updated to the documented order (ETH_RPC_URL env, RPC_MAINNET env, project `.env`, SWAPARCH_ENV_FILE) and the `rpc` pytest marker was registered before these checks:

```
uv run pytest -q -m rpc            -> 1 passed, 44 deselected
uv run swaparch pins-check         -> all 5 pins: header ok, multicall ok, direct ok; network requests: 11
uv run swaparch pins-check (again) -> network requests: 0
```

No RPC URL or provider hostname appears in data/rpc-cache. Live acceptance is therefore verified by the lead, not by the author.
