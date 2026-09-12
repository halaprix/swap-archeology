# Independent historical runner review

Review date: 2026-09-08. Scope: `scripts/historical_study.py`, its CLI and
qualification call paths, saved smoke artifacts, and offline regression tests.
The reviewer made no RPC requests and changed no implementation, inventory,
snapshot, or copied evidence. The integrator explicitly authorized the additional
test file `tests/test_historical_review_regressions.py`.

## Verdict

**PASS for bounded orchestration and the recorded warm/resume run.** The reviewed
runner SHA256 is
`c7db91db43be922436a263ae76b209cb22e6448e30bf8083cfeed5c1b168a329`.
Concurrent application-source edits changed the aggregate code identity after
the warm run; this verdict does not qualify those edits. This is not acceptance
of the complete historical study, every source family, a globally optimal route,
or atomic settlement.

## Findings resolved during review

All findings below were reported to and fixed by the integrator; the reviewer
did not implement the fixes.

1. **Stale admission and coverage on resume.** The inventory identity omitted
   pool status/notes and family status/unresolved metadata consumed by the CLI.
   They now invalidate reuse. Observations and qualification at unrelated hashes
   remain excluded from the current hash's identity.
2. **Stale coverage attached to a reused quote.** A successful acquisition retry
   now refreshes coverage even when the quote itself can be reused. Missing
   stages, failed stages, and stages with stale code/hash/inventory identities
   remain explicit, including when resuming without `--acquire`.
3. **Lost acquisition work on retry or interruption.** Each stage has distinct
   attempt metadata and log filenames. Totals include previous attempts; a
   failed attempt with unknown request count cannot disappear after a successful
   zero-request retry. A running attempt is checkpointed with an unknown count
   before invoking the stage, so an interruption also survives resume.
4. **Partial progress understated the planned study.** Every checkpoint now
   counts the full planned block/scenario/solver product, including blocks not
   materialized yet. `planned_total` and remaining work survive interruption.
5. **Duplicate jobs inflated completion.** Repeated semantic scenario amounts
   and solver names now fail before work starts, rather than producing one
   report while counting it more than once.

Previously repaired branches were independently checked: CLI budgets reach the
runner; code identity includes application source, qualification scripts and
dependency lock; code/configuration/block-selection drift rejects reuse of the
same run ID; quote reuse verifies report digest and cached block header; offline
cache misses remain failed jobs rather than successful no-route results.

## Independent checks

`UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q
tests/test_historical_study.py tests/test_historical_review_regressions.py`
passed **11 tests**. Ruff passed for the added regression file. Fixtures invoke
injected stages and quote writers against temporary directories; they perform
no network acquisition.

The additional cases exercise admission/coverage identity changes and unrelated
hash stability; report-byte tampering; header changes; code/block/budget drift;
failed-stage retries with preserved counts and logs; interruption followed by
resume; missing and stale stage coverage; duplicate jobs; exact amounts beyond
the default Decimal precision; nonpositive, nonfinite and subunit rejection;
and forwarding all four CLI solver budgets.

The real CLI call path constructs `RpcClient(offline=True)` for quote work.
Cache misses raise before the transport request. Source discovery/qualification
remains serial and precedes offline optimization. Source stage success records
completion of its command: individual pools may still be unsupported or fail
qualification, as retained in per-source reports and quote coverage.

The V2 stage discovers incrementally by pair filter, verifies coverage anchor
hashes, merges new records, and preserves existing pool records. Qualification
removes/replaces only the requested hash in `validated_block_hashes`; it does
not erase other historical qualifications. Global status/notes changes can
legitimately invalidate earlier coverage metadata.

## Saved integration evidence

The earlier `data/results/study/calm-start-smoke/manifest.json` records block
**25760917**, hash
`0x4716e026c29768f740e47b68fe6fceb7070f128cb088b09f2b60d15bd4548499`.
The reviewer independently checked both quote-file SHA256 digests against the
manifest and the block identity against the raw cached header. Inputs were
exactly **1 WETH** and **1000 USDC** in opposite directions, using search with
grid parts 2, maximum steps 4, beam width 16, and maximum expansions 100. Both
reports record `offline: true` and `network_requests: 0`.

All ten acquisition stages recorded completion; their request counts sum to
**170** (57 + 2 + 90 + 11 + 1 + 2 + 2 + 1 + 1 + 3). These are saved observations
from the integrator's acquisition, not fresh reviewer RPC. This smoke predates
the final resume/accounting fixes and therefore does not establish final-code
warm-resume acceptance.

The final-run artifact `data/results/study/calm-start-review/manifest.json`
records aggregate code identity
`f3b3a842f2e809e09f4ae77ddcb49bdafca18d98f7fcad68defd940be8ece954`.
Independent artifact inspection verified:

- Ten current, completed stages, each with one attempt, zero reported requests,
  a retained log, and matching recorded code and block hash. Request totals are
  zero known and zero unknown. Current effective inventory identities match.
- Two completed quote reports with valid SHA256 digests, exact inputs and block
  hash, `offline: true`, zero requests, and the small solver budgets above.
- Outputs of **1,881.794071 USDC** and **0.531271726805167039 WETH**,
  respectively, both feasible with no residual. Both reports select all 15
  source families and admit 42 states across six implemented families; they
  retain 474 unsupported entries.

The integrator ran acquisition with credentials stubbed and RPC/HTTP transport
forbidden, then resumed with every stage and quote callback forbidden; it
reported no callback invocation and byte-identical manifests. Independently,
the reviewer copied the manifest to a temporary directory and confirmed that
the changed current aggregate code identity rejects resume. Explicitly supplying
the recorded code identity then allowed resume with every stage/quote callback
forbidden and byte-identical manifest output. That controlled identity
substitution tests saved-run reuse; it does not qualify the concurrent source
changes or bypass the production identity guard.

## Remaining scope and limitations

- The five inclusive configured windows contain **13,508 distinct blocks**.
  Default configuration contains **88 scenarios**, giving **1,188,704 jobs per
  solver** across those windows. The two-quote smoke is not full-window coverage.
  Four bounds match preserved `evidence/oracle_vs_dex/windows.json`; the sUSDe
  range remains the documented 21895170–21896367, whose original dense-header
  provenance confirmation is still pending in `docs/COMPLETION.md`.
- Stage coverage means current required acquisition commands completed. It does
  not mean all requested protocol families or pool models are supported, nor
  does a completed no-route report prove no route exists outside the admitted
  models and finite solver budgets.
- Quote report bytes and header identity are checked on reuse. Stage reuse does
  not independently hash or verify the existence of every raw evidence/cache
  artifact or log. Some stage stdout omits its evidence path, so the manifest's
  `raw_paths` list is not a complete provenance index. Keep the underlying
  validation/cache directories when retaining a study.
- Request totals use stage-reported counts. Unknown interrupted/failed attempts
  remain explicit; they are not treated as zero. Gas remains a model estimate,
  and source quote composition retains each adapter's transfer/settlement
  limitations.
