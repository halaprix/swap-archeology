# Historical discovery efficiency review

Review date: 2026-09-08. Scope: read-only inspection of the historical runner,
Curve discovery/probe stages, local canonical sources, and saved discovery
responses before the 13,508-block study. No implementation changes or reviewer
RPC requests occurred. This report is the reviewer's sole assigned write.

## Recommendation

The smallest safe immediate change is testing a larger Curve Multicall chunk
size. Cross-block registry membership or metadata reuse is not yet justified by
the locally available source evidence. Introduce incremental reuse only for
concrete handlers/factories whose ordering and mutation rules have been reviewed,
with full discovery as the fallback for other handlers.

## Observed repeated work

At block **25760917**, hash
`0x4716e026c29768f740e47b68fe6fceb7070f128cb088b09f2b60d15bd4548499`,
the saved files under `data/discovery-evidence/curve/<hash>/` contain:

| File / operation | Contract reads | Batches at 100 | Batches at 200 |
|---|---:|---:|---:|
| counts.json | 2 | 1 | 1 |
| handlers.json | 8 | 1 | 1 |
| base-registries.json | 8 | 1 | 1 |
| pool-list.json | 2,418 | 25 | 13 |
| coins.json | 4,826 | 49 | 25 |
| selected-metadata.json | 1,263 | 13 | 7 |
| Total | **8,525** | **90** | **48** |

The pool list contains 2,413 distinct nonzero addresses. Each receives coin and
underlying-coin reads; 421 selected pools receive three enrichment reads.
`scripts/curve_discovery_run.py` repeats this full scan at each new hash.

The 90-request count agrees with the saved stage result in
`data/results/study/calm-start-smoke/manifest.json`. **90 → 48 is an ideal batch
count calculation, not a measured provider improvement.** Larger calls may hit
gas, response-size, or provider limits. `Multicall3.call` already halves failing
chunks and falls back to direct calls; retain that behavior and measure a fresh
authorized acquisition before asserting an actual saving.

Same-hash calls already reuse the RPC cache by target/calldata and block hash.
Snapshot extension also requests only missing calls. Warm same-hash success does
not establish safe reuse of values at a different hash.

## Canonical source and unsafe reuse shortcuts

The inspected local source is:

`data/protocol-sources/curve/metaregistry/contracts/MetaRegistry.vy`

Its identity, recorded in `data/protocol-sources/curve/manifest.json` and checked
against the file bytes, is:

- Repository: `curvefi/metaregistry`.
- Commit: `b18e83e08bee8660a5d59f53757fae85b6d25599`.
- SHA256: `0b1eb456beacb80af4761c885856c33a6d997d6d40210b0339e280a89e4abf05`.

This is pinned source evidence; the review did not establish a historical
compiled-bytecode match or acquire additional source over the network.

The source establishes the following behavior:

- `pool_count()` at line 529 sums the current handler counts. `pool_list()` at
  line 545 concatenates those current lists. If an earlier handler grows, the
  global indexes of later handlers shift. Reading only the global tail can miss
  new pools and reread unrelated old pools.
- `_update_single_registry()` at line 76 and the public add/update functions at
  lines 130/141 allow the administrator to append or replace handlers. Replacing
  a handler does not change registry length. Neither operation emits a registry
  mutation event; the declared events concern ownership.
- `_get_registry_handlers_from_pool()` at line 100 appends matching handlers in
  current index order. Default metadata methods use `_handler_id=0`, including
  `get_coins()` at line 296 and `get_underlying_coins()` at line 465. Although the
  internal comment says to prefer the last registry, the implemented default
  selects the first matching array entry. An unordered handler set is therefore
  insufficient as a metadata identity.
- `get_base_registry()` delegates to the handler. MetaRegistry's interface does
  not prove that a handler's underlying membership, ordering, or metadata is
  append-only or immutable.

Consequently, unchanged total pool count, unchanged registry length, unchanged
handler addresses, or endpoint-only scans do not by themselves prove unchanged
pool membership and metadata throughout a historical interval. Do not copy a
prior hash's registry observations into a later snapshot as fresh evidence.

## Smallest defensible incremental design

1. Maintain a catalog per chain, MetaRegistry, ordered handler identity, and base
   registry, retaining observation hashes and explicit discovery coverage.
2. Recheck the current handler ordering and base-registry identities. Permit
   handler-local tail discovery only for an explicitly reviewed append-only
   implementation. Unknown handlers, changed identities, and mutation patterns
   outside that proof use full discovery.
3. Reuse pool metadata only where the concrete implementation establishes its
   immutability. Preserve the original observation/source provenance separately
   from current-hash membership and state. Continue current-hash implementation,
   mutable-state, and quote-qualification checks before admission.
4. Store all discovered identities and coin sets, including pools excluded by
   the current token filter. Version the filter and backfill newly relevant
   pools and historical coverage when the token set expands.

The existing unfiltered `coins.json` files already retain coin and underlying
sets for every enumerated address, not just the 421 selected pools. At an
already observed hash, a changed token filter can replay those saved responses
offline. Newly selected pools may still need missing enrichment reads; there
is no claim that all metadata for every excluded pool has already been cached.
Caching only the selected inventory would lose this backfill capability.

The first useful incremental eligibility target is the plain StableSwap-NG
factory `0x6a8cbed756804b16e05e741edabd5cb544ae21bf`, observed behind handler
`0xe06eba9cea16cc71d4498cdba7240bb20d475890` at the calm block. The copied NG pool
source declares coins and coin count immutable, but that does not prove the
factory's registry ordering or handler metadata behavior. The canonical handler
and factory sources establishing append/removal/reordering and mutation rules
were not found locally. Obtaining and reviewing those concrete sources is the
remaining prerequisite before enabling that reuse branch.

## Other stages

V2 discovery already tracks incremental token-pair log coverage and preserves
creation identities; historical requests within its covered range reuse that
discovery. V3 qualification loads the existing inventory rather than enumerating
the factory each block. Pool identity reads still repeat alongside state reads,
but their batching cost is much smaller than the Curve scan.

Do not conflate repeated discovery with mutable historical checks. ARM proxy
implementation/pause behavior and Aave's provider/oracle path can change. Their
current-hash checks remain necessary. Reusing proven immutable identities would
also require explicit provenance support rather than inserting old responses
into a new hash's raw-call cache.
