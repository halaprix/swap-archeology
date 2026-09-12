# Combined CLI and saved-report explorer independent review

Review date: 2026-09-08. Reviewer owns this report only. No RPC, credential
resolution, evidence changes, or snapshot writes were performed. Existing
bounded protocol reviews remain the authority for source mathematics.

## Verdict

Accepted for bounded offline CLI integration and a saved-report explorer. The
initial explorer's reproduced failures below were corrected by its author and
independently retested. This review does not certify the complete historical
windows, settlement, global optimality, or the visual appearance of a rendered
browser page. The corrected historical matrix was still expanding at review
cutoff; its exact checked prefix is recorded below.

## Reproduced explorer findings, now corrected

1. **Incorrect gain:** 110 output against a 100 baseline renders `0.10%`, rather
   than `10.00%`. The ratio lacks the percentage factor. A 99 output against 100
   renders malformed `0.-1%` because negative ratio formatting pads the sign.
2. **CLI source subsets collapse:** JavaScript `subset()` ignores
   `selected_families` and identifies those reports as `all-supplied`, although
   the Python helper and surrounding description claim to recognize them.
3. **Actual dual reports cannot build:** building from either
   `data/results/five-family` or the then-current `data/results/six-family`
   raises `ValueError: report contains unsupported JSON value float`. The
   display projection retains full `search_info`, including numerical dual
   runtime and optimizer diagnostics, then passes it to integer-only conversion.
4. **Valid no-route results cannot build:** the CLI legitimately emits
   `best_split: null` with explicit exclusions when no selected route is feasible.
   `_validate` rejects that result, preventing an unavailable observation from
   appearing as a visible gap.
5. **Control reconciliation discards a user's choice:** with A→B, B→A, and C→D
   records, choosing C/D resets the pair to A/B because the pair option list is
   filtered against the stale direction. Same raw size across directions with
   different decimals takes its label from the first matching raw amount: 100
   raw units with six decimals is displayed as 100 instead of 0.0001.
6. **Coverage and chart gaps:** inventory `supported` alone is shown as usable
   support, even for `selected: false` or `usable_pools: 0`. A missing baseline is
   converted to a zero gain chart point. The trend connects sparsely supplied
   block numbers without expressing unobserved blocks.
7. **Flow semantics:** when no path allocation is recorded, the SVG puts every
   ordered step on one continuous lane. Independent branch spends and a later
   merge can therefore resemble serial pools. Step text omits input/output token
   identities. A step timeline must be labeled as such, or a flow graph must
   actually represent the funded token transfers.
8. **Input provenance ambiguity:** matching uses block number, symbols, amount,
   solver, and subset, ignoring chain/hash and conflicting duplicate reports.
   Older five-family and newer six-family outputs can share that key. Unmatched
   explicitly requested paths are silently ignored if another input matches.

Findings were sent to the lead and explorer author before any acceptance claim.

The final implementation formats the absolute integer ratio before restoring
its sign and applies the percentage factor; preserves recorded source subsets;
prunes unused dual diagnostics; accepts explicitly null winning plans; and
reconciles pair → direction → size → solver → subset in that order. Size labels
use the selected direction's decimals. Missing reports retain the selected
block label and show an explicit gap. Saved no-route observations show neither
a zero-output quote nor an invented price.

Coverage now requires a selected source and positive usable-pool count. Missing
baselines are excluded from the gain chart; the time chart uses separate points
without lines across unobserved blocks. Allocation lanes are used only when
recorded; otherwise the page directs the reader to the ordered step timeline.
Step text includes token identities and raw amounts. Lido/Origin steps add a
specific visible caveat about nominal stETH amounts versus recipient share
balance changes.

Conflicting duplicate scenarios, multiple chain/hash identities at one displayed
block, and ambiguous symbol-to-address mappings are rejected. Explicit missing
paths and malformed non-report objects are rejected. Raw input/output integers
exclude booleans, and decimal counts must be integer values in 0..255. The
`best_split` key is required while its value may be null. Both delivered HTML
artifacts were regenerated with these corrections.

## CLI checks already passed

Inspected `cli.py`, `universe.py`, and the offline guard in `rpc/client.py`.
Unknown source filters and invalid amounts are rejected before constructing the
RPC client. Selected families filter the acquired and loaded records, with
explicit exclusions retained. Source membership alone does not imply an
implemented or hash-qualified adapter. No-feasible results return exit status 1
with a JSON report. Exact input parsing uses the Decimal integer ratio rather
than context-rounded multiplication.

The following real cached commands were called through `cli.main`, with
`resolve_rpc_url`, `requests.sessions.Session.request`, and
`SnapshotStore._persist` replaced by functions that raise immediately. Every run
reported zero network requests and none reached those forbidden functions.

| Calm block 25896003 request | Source selection | Raw output | Exit |
|---|---|---:|---:|
| 1 WETH → USDC | default all | 2395752087 | 0 |
| 1 WETH → USDC | uniswap_v2 | 2393053525 | 0 |
| 100 DAI → USDC | maker_sky_psm | 100000000 | 0 |
| 1 wstETH → stETH | lido | 1243103211881836544 | 0 |
| 0.00001 WETH → stETH | origin_arm | 10000200004000 | 0 |
| 1 WETH → USDC | fluid_dex | no feasible result | 1 |

All used pool families were members of the selected subset. The full calm
universe contained 5 Curve NG, 1 Lido, 1 LitePSM, 1 Origin ARM, 11 V2 and 23 V3
states. Origin and Lido outputs remain nominal quote composition: stETH share
rounding can change a recipient's actual balance delta. These runs are not
atomic settlement checks.

Five independent `--offline`, default-family, baseline, grid-parts-2 reruns of
1 WETH → USDC produced:

| Block | Usable states | Raw USDC output |
|---:|---:|---:|
| 23549991 | 40 | 3397803529 |
| 23550060 | 40 | 3577784803 |
| 23728292 | 42 | 3145451947 |
| 24356381 | 43 | 2372717541 |
| 25896003 | 42 | 2395752087 |

The first 48 available winning plans from the expanding six-family matrix were
independently replayed through fresh Evaluator calls and a separate funded
inventory loop that threads immutable pool replacements. Every stored step
output, total output, zero intermediate/original-input residual, selected family,
and loaded-state count matched. This first replay covered only block 23549991;
it is not a completed matrix assertion.

After the intermediate correction below, the independent replay covered the
then-present **59 corrected reports, 184 saved winning plans, and 366 ordered
steps** under `data/results/six-family-full-intermediates`: 39 reports at block
23549991 and 20 at 23550060, comprising 20 baseline, 20 search and 19 dual
reports. All passed fresh evaluation and separate funded state/inventory
threading with the same forbidden-operation guards. A standalone explorer built
from precisely that captured file list was 402,537 bytes. Additional reports
written after this cutoff are not silently included in that acceptance count.

### Default intermediate correction

The lead subsequently found that the inherited phase-2 default intermediate
list omitted WETH, leaving the baseline unable to find the supported
wstETH→WETH→USDC path. The corrected `BaselineSolver` infers all token addresses
from the current admitted state set on every solve. Explicit intermediate
arguments, including an empty tuple, remain authoritative. Inspection confirms
that reusing a solver refreshes inferred tokens and that general/dual baseline
incumbents receive the same correction. The baseline remains bounded to two
hops/two paths; inference does not make it a general solver.

Independent five-pin, grid-parts-2, offline reruns of 1 wstETH→USDC now succeed
through WETH, producing raw USDC outputs 4092445735, 4285299214, 331450385,
2840803634, and 2977513557 in chronological pin order. The same credential,
transport, and snapshot-write guards remained enabled. The command
`UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest tests/test_solver.py
tests/test_general_solver.py tests/test_quote_cli.py -q` passed **29 tests in
2.10 seconds**, including inferred-intermediate refresh and explicit override
coverage. Earlier artifacts are preserved as results of their original search
scope; the corrected matrix belongs under
`data/results/six-family-full-intermediates`.

## Validation and remaining review boundary

`UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest
tests/test_explorer.py tests/test_quote_cli.py -q` initially passed **5 tests**.
Those tests did not detect the explorer failures above. Additional independent
checks executed the actual generated JavaScript in Node with a minimal DOM
model; this verifies the reported logic failures, not browser rendering.

Final independent command:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest \
  tests/test_explorer.py tests/test_quote_cli.py \
  tests/test_solver.py tests/test_general_solver.py -q
36 passed, 1 skipped in 2.13s
```

The skipped case is the opt-in browser test. Independently executing the final
generated JavaScript against a minimal DOM model checked pair switching,
reversed six-decimal sizing, selected-source changes with different saved
outputs, a missing-block gap, a saved no-route observation, exact formatting of
900719925474099312345 raw units, and gains `10.00`, `-1.00`, `-0.10`, and `-0.01`
percent. A second pass selected every one of the 59 real saved scenarios through
all controls, confirming the selected block hash, raw input/output, and rendered
amount matched its source report. No report was hidden by stale selections.

The size/price visualization remains a view of supplied discrete sizes. Its
plot coordinates use display precision while quote amounts remain exact
strings/BigInt. Allocation lanes plus the ordered step list are a limited flow
view, not a newly reconstructed general split/merge graph. These are explicit
presentation limits, not claims of full explorer or historical-study completion.

The browser skill was read and its runtime initialized. Browser selection
returned `No browser is available`; the documented discovery check returned an
empty list. No alternate browser was used and no visual acceptance is claimed.

Safe embedding currently converts integer amounts to strings and escapes `<`,
`>`, `&`, U+2028 and U+2029 inside the JSON script element. Dynamic report text is
placed using `textContent`; the one `innerHTML` expression contains only a local
step counter. This inspected path does not interpolate report text as HTML.
