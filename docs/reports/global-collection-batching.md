# Global state collection — 2026-09-08

## Parallel transport

A bounded collection run used four independent
block collectors share no mutable client/snapshot objects; the main thread owns
manifest/shard writes. Each worker captures an older read-plan seed and fetches
all returned values at its requested block hash. Scheduling is bounded (1–8
workers, configured at 4), checks disk before submission, records failures, and
resumes artifact-backed completed blocks. Unique temporary paths protect the
shared inventory archive from concurrent creation.

Transport benchmark used the same 2,105-read Multicall payload per block:

| Workload | Serial HTTP | Concurrent HTTP | One JSON-RPC batch |
|---|---:|---:|---:|
| 4 blocks | 5.79s | 1.70s, 4 workers | HTTP 413 |
| 2 blocks | 2.19s | 1.01s, 2 workers | 2.82s |

Raw aggregate results matched byte-for-byte for sequential/concurrent requests;
the two-block JSON-RPC batch also matched. The four-block batch exceeded a
provider payload limit. These are bounded measurements, not a service-wide
throughput guarantee. JSON batching does not turn four RPC operations into one;
it only combines their HTTP transport.

End-to-end fresh eight-block collector test: 27.65s serial versus 15.81s with
four workers; both completed with 18 requests. Replay of the parallel run added
zero attempts and zero requests. Evidence is in `data/results/study/`:
`rpc-transport-benchmark*.json.gz`, `rpc-parallel-parity.json`,
`collector-parallel-benchmark.json`, and `parallel-collector-check-4/`.
Validation: 219 passed, 2 skipped; changed-file Ruff passed.

The remainder records earlier implementations.

## Bulk tick correction

A corrected state-only collection run replaced the earlier implementation.
The earlier measurements below describe the superseded individual-tick collector.

V3 now uses canonical TickLens `getPopulatedTicksInWord`; V4 uses canonical
PoolManager `extsload(bytes32[])` for the tick liquidity storage words. This
changes transport, not the radius-8 bitmap coverage or quote arithmetic.
The collector regenerates next-block tick requests from the current bitmap,
discarding obsolete tick arrays and windows. Both paths retain individual-getter
compatibility; V3 rejects missing/duplicate/unexpected TickLens entries.

At fresh blocks 25896021 and 25896022, all eight adapters loaded 135 candidate
states using 2,105 subcalls in ONE Multicall plus ONE header request. Elapsed
times were 3.84 and 4.12 seconds. Startup at 25896020 needed three dependency
waves. These are measured examples; changed dependencies, failed subcalls or
provider batch limits can require additional requests.

Independent live parity at the same 25896020 hash compared bulk data against
the original individual getters with bulk responses removed from the reference
snapshot: all 7,076 ticks across 75 V3/V4 pools matched, as did 450 quote/refusal
comparisons. Evidence: `data/validation/bulk-tick-parity/` and
`data/results/study/bulk-tick-benchmark.json`. TickLens existence at 21895170
was verified by hash-pinned `eth_getCode` (1,385 bytes), saved under
`data/discovery-evidence/tick-lens/`.

Validation after correction: 217 passed, 2 skipped; changed-file Ruff passed.
The rerun is STATE COLLECTION ONLY. Discovery refresh and independent quote
qualification remain separate; collection never grants a new validated hash.

## Earlier batching work (superseded)

The former full sweep used twelve
sequential discovery/qualification stages per block. At block 21895171 these
used 233 network requests, including 108 V4 requests, 55 V3 requests and 55
Curve registry requests. Its measured sustained rate was about 28 seconds/block.

`historical_study.py --state-only --batch-size 4000` now collects known candidate
state through one global `universe.acquire` call. All eight adapters and the
Aave reference share each dependency wave. Curve uses targeted registry identity
checks; Origin resolves the ABI at the requested hash. Neither mutates inventory.

Consecutive blocks reuse request identities from the previous collection, never
returned values. Reads are still keyed by chain, block hash, target and calldata.
New tick/metadata dependencies trigger additional shared batches. Prefetch resets
when the inventory changes, blocks are nonconsecutive, or the prior request list
exceeds three configured batches; stale tick hints cannot grow without bound.

Fresh live measurements (all eight adapters, 135 loadable candidate states):

| Block | Prefetched reads | Multicall requests | Header requests | Seconds |
|---|---:|---:|---:|---:|
| 25896006 | 0 | 4 | 1 | 6.70 |
| 25896007 | 8937 | 3 | 1 | 6.78 |
| 25896008 | 8937 | 3 | 1 | 6.76 |

The 4,000-subcall limit worked. A 10,000 limit on 25896005 required automatic
shrinking of the 7,132-read tick wave and did not improve latency. A warm replay
of 21895300 used zero RPC requests. Reports are under `data/collection/1/`, keyed
by block hash; input inventories are archived once by digest. The actual CLI
preflight is `data/results/study/phase8-global-collection-preflight/`.

This is a state collection result, not an equal-workload speedup of the former
qualification sweep. Full registry discovery and independent Quoter checks are
excluded from this inner loop. `qualification_performed` is false; per-hash
`validated_block_hashes` are never added. The existing quote admission gate remains
strict. Reports retain unavailable/unresolved families and candidate exclusions.
Full-window discovery refresh, separate qualification and the offline quote sweep
remain unfinished. Do not resume the old process or claim that collection alone
completes phase 8.

Validation: 213 passed, 2 skipped; changed-file Ruff passed. Tests cover shared
cross-family batches, deduplication, chunk limits, dependency waves, fresh-hash
prefetch, zero-RPC resume, metadata overlays, and state-only orchestration without
invoking qualification or quotes.
