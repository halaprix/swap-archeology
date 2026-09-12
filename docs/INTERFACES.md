# Shared interfaces and workspace contract

## Layout

```
pyproject.toml            uv project; run everything with `uv run ...`
src/swaparch/core/        types.py, protocols.py
src/swaparch/rpc/         JSON-RPC client, raw cache, Multicall3, headers, logs
src/swaparch/snapshot/    per-block snapshot store built from CallResults
src/swaparch/discovery/   inventory schema + runners per family
src/swaparch/adapters/    one module per family: reads, state, exact quote math
src/swaparch/solver/      solvers behind core.protocols.Solver
src/swaparch/evaluator/   ordered, integer-exact plan execution
src/swaparch/cli.py       `uv run swaparch ...` entry points
data/discovery/<chain>/<family>.json     inventories (identity + coverage), committed to disk
data/snapshots/<chain>/<blockhash>/      per-block CallResults (json.gz) + header
data/rpc-cache/                          raw request/response pairs, keyed by chain+blockhash+call
docs/                     design notes, source status table, validation reports
tests/                    pytest; offline by default (no RPC unless RPC_MAINNET set and -m rpc)
evidence/                 read-only copied provenance; never modified
```

## Credentials

The archive RPC URL is resolved in this order: environment variable `ETH_RPC_URL`, environment variable `RPC_MAINNET`, then the project-root `.env` (`ETH_RPC_URL`, then `RPC_MAINNET`), then the dotenv file named by `SWAPARCH_ENV_FILE` (explicit opt-in only, key `RPC_MAINNET`). The project `.env` is git-ignored; load only the configured RPC keys from it. The URL is never printed, logged, cached or written into any artifact. Error messages must redact it. For ad-hoc `cast` reads: `set -a; . ./.env; set +a; cast ... --rpc-url "$ETH_RPC_URL"` with stderr piped through `sed -E 's#https?://[^ "]+#<rpc-url>#g'`.

## Cache keys

- eth_call: `sha256(chain, block_hash, to, data)`. Never key by block number alone.
- eth_getLogs: `sha256(chain, address, topics, from_block, to_block)` plus the stored hash of `to_block` so a reorg can be detected on reuse.
- Block headers: by number, storing hash; consumers compare hash when they hold one.
- `latest` is never cached.

## Multicall

Multicall3 `0xcA11bde05977b3631167028862bE2a173976CA11` (verified present at block 23549991). Use `aggregate3` with `allowFailure=true` per subcall; keep each subcall's success flag and raw bytes. Chunk adaptively on gas/size errors; every chunk is pinned to the same block hash (eth_call by hash, not number, where the provider supports it; otherwise by number with a header hash check before and after). Fall back to individual `eth_call` for subcalls that fail inside the batch, and record `via` on the result. Acceptance: batched vs individual reads are byte-identical for every adapter before that adapter is trusted.

## Discovery inventory file (`data/discovery/<chain>/<family>.json`)

```json
{
  "chain": 1,
  "family": "uniswap_v3",
  "schema": 1,
  "status": "supported | discovered_unsupported | unresolved | unavailable",
  "deployments": [{"address": "0x...", "version": "...", "evidence": ["url or file"]}],
  "coverage": [{"method": "logs:PoolCreated", "deployment": "0x...", "filter": {"tokens": ["0x..."]},
                "from_block": 0, "to_block": 25896003, "to_block_hash": "0x...", "scanned_utc": "..."}],
  "pools": [ PoolRecord as JSON ],
  "unresolved": [{"what": "...", "why": "...", "next_step": "..."}],
  "notes": "free text"
}
```

A negative lookup is complete only for its explicit `coverage` entry. A pair-filtered scan does not cover a later-added token: backfill the new filter from the deployment's creation block.

## Quote semantics

- All amounts are integers in smallest units. No floats in adapters, solver plans or evaluator.
- `PoolState` is immutable; `swap()` returns the new state. Shared resources are declared through `capacity_ids()`.
- Adapters must implement exact-input quoting against local state with the protocol's own math (ported integer arithmetic), and must be cross-checked against the protocol's on-chain quoter or an independent reference at the same block before their status becomes `supported`. The cross-check evidence lives under `docs/adapters/<family>.md` with the raw responses under `data/`.
- The evaluator applies steps in order, threads intermediate balances, starts with exactly `request.amount_in`, refuses any spend exceeding currently held inventory, and requires zero original-input and intermediate residuals for a full fill. Funded cycles may return and re-spend the input token; gross input turnover is not fresh funding.

## Reporting rules

Final report must contain: files changed, exact commands run with their output (trimmed), what was verified against what independent evidence, and explicit limitations. Never claim `supported` without the cross-check. Never modify `evidence/`.

## Phase 2 admission and acquisition failures

`load_states` admits only supported records with the snapshot hash in
`config.validated_block_hashes`. Quote checks qualify integer pool math at that
hash; they do not prove token transfers or atomic settlement. Discovery emits
`discovered_unsupported`; `scripts/validate_v3_universe.py BLOCK` qualifies V3
pools and records raw QuoterV2 responses under `data/validation/uniswap_v3/`.

`AcquireReport.unsupported` maps pool ids to dependency-planning failures. Pass it
to `load_states(..., acquisition_failures=...)` to retain explicit exclusions.
The evaluator rejects nonzero balances of intermediate tokens after execution.

For curated or registry-discovered venues without an exact creation block,
`discovered_by.deployed_by_block` records a conservative activation upper bound
with raw code/registry evidence in `discovered_by.evidence`. `created_block`
remains null. Earlier blocks are unresolved until observed; the upper bound is
never evidence of prior absence. Quote qualification still requires its exact
snapshot block hash.
