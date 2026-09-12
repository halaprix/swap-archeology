# Independent LitePSM review

2026-09-07. Reviewer context did not implement the adapter or qualifier. Scope:
`src/swaparch/adapters/litepsm.py`, `scripts/litepsm_run.py`, activation admission
in `universe.py` and `cli.py`, tests, and the five saved historical read bundles.
Only this report was edited by the reviewer; no RPC, transactions, commits,
pushes, remote knowledge writes, or `evidence/` changes were performed.

Verdict: PASS for the declared source-derived quote model at the five qualified
hashes, with the token-implementation and settlement limits below. Arithmetic,
capacity, state updates, five-pin snapshot evidence, fresh nine-method read
parity, and activation checks passed independently. No settlement verdict.

## Source and behavior

Canonical local `dss-lite-psm` commit
`dbf0022225f645f5697e5517d0cf00810471bccf` and the copied
`data/protocol-sources/litepsm/DssLitePsm.sol` match SHA-256
`502eed38778ac29758959cadbb3d2f36aa3af21144e7374483795581a6279ce8`.
The reviewer read the complete canonical contract, including its public and
permissioned entrypoints and bookkeeping methods.

- `_sellGem` returns `gross - floor(gross*tin/1e18)` and spends the current DAI
  ERC-20 inventory. Fees already in that balance remain spendable. No `fill()`,
  debt-ceiling headroom, or future keeper replenishment is invented.
- `_buyGem` requires `gross + floor(gross*tout/1e18)` DAI for its exact gem
  output. The adapter's monotone integer preimage search rejects an unattainable
  DAI input instead of hiding its residual. Both gross and fee multiplication
  and the DAI addition retain Solidity's checked `uint256` limits.
- A buy is capped by both pocket balance and pocket-to-LitePSM allowance. Each
  accepted trade creates a new state with both inventory changes; buys also
  consume allowance. Reusing the updated state cannot reuse spent liquidity.
- `tin == HALTED` and `tout == HALTED` stop only the corresponding direction,
  including the zero-amount shortcut. `bud` permissioned fee-free calls are
  excluded. `vat.live()` does not gate these public swap functions in source.
- Six getter calls identify gem, DAI, pocket, conversion factor and fees. Three
  dependent calls read balances and allowance at those decoded identities.
  Missing, failed and malformed reads raise `Unsupported`. Token membership,
  snapshot token chain, conversion factor, fee range, model discriminator and
  integer amount/state checks prevent the reviewed misclassification cases.
  The final adapter also rejects any family, chain, deployment or pool other
  than the qualified Ethereum singleton, and checks decoded gem, DAI and pocket
  against its canonical addresses before loading balances.
- `activation_reason` accepts a known creation block or an observed
  `deployed_by_block` upper bound. Before an upper bound it reports activation
  unresolved, not prior absence; `created_block` remains null. CLI admission
  preserves that distinction, and support still requires the requested hash.

## Independent validation

Commands run from the project root:

```text
git -C <external-repos>/dss-lite-psm rev-parse HEAD
dbf0022225f645f5697e5517d0cf00810471bccf

sha256sum <external-repos>/dss-lite-psm/src/DssLitePsm.sol
502eed38778ac29758959cadbb3d2f36aa3af21144e7374483795581a6279ce8

UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q tests/test_litepsm.py tests/test_integration.py
16 passed in 0.41s
```

The initial unmodified `uv run` could not create a file in the read-only default
UV cache. The explicit temporary cache above resolved that environment issue.

A separate `uv run python` check used `random.Random(20260907)` and `Fraction`
to sample 2,000 fee values from `[0,1e18]` and gem amounts from `[1,5,000,000]`.
For each it checked the source sell expression, the forward buy expression
inverted by the adapter, the exact immutable inventory/allowance deltas, and
rejection of `required_dai - 1`. Output: `6000 independent Fraction
amount/state/residual checks; shared inventory and 4 overflow boundaries
passed`. The four overflow cases covered gross multiplication, fee
multiplication, pocket balance addition and DAI balance addition.

The reviewer also decoded every raw bundle directly into a fresh
`StoredSnapshot`, matched its nine calls against `SnapshotStore`, checked the
canonical source digest, and accepted both exact maximum feasible directional
boundaries. All five bundles contain nine successful raw calls and nine
passing quote/boundary checks:

| Block | Exact snapshot hash |
| --- | --- |
| 23549991 | `0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623` |
| 23550060 | `0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a` |
| 23728292 | `0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc` |
| 24356381 | `0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb` |
| 25896003 | `0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5` |

Raw artifacts: `data/validation/litepsm/<hash>.json`. All five observed fees
are zero. The qualifier explicitly rejects nonzero historical fees before
using its simple inverse expression; this keeps its historical comparison
correctly bounded. The separate randomized review checks cover floor rounding
with nonzero fees. The source-reference approach satisfies the interface's
independent-reference option; no native LitePSM view quoter exists in the
reviewed contract.

Fresh parity is recorded in
`data/validation/multicall-parity/litepsm/result.json`. The reviewer separately
decoded the raw `aggregate3` request's nine call identities and its nine return
tuples, and compared every successful byte string against the corresponding
individual `eth_call` raw cache file. All nine matched at the calm block hash;
all raw requests explicitly use that same `blockHash`. Recorded acquisition
counts are three batch-client and eleven individual-client network requests.
This check did not rely on the artifact's `matched` booleans or a shared
individual-call cache standing in for a batch.

The three copied Circle files match every SHA-256 in
`data/protocol-sources/circle/manifest.json`, pinned to
`fc85788bc7c23cefe3df1a757133048bfddadeaa`. `FiatTokenV1.transferFrom`, lines
258–278, checks allowance then unconditionally subtracts the transferred
amount, including when allowance starts at `uint256.max`. This confirms the
adapter's allowance transition against that source expression. The copied
proxy source identifies its implementation storage slot; a repository commit
alone does not establish the implementation behind USDC at each historical
hash. This review does not claim historical proxy/bytecode matching or an
independent token-transfer simulation.

## Findings and limits

The discovery inventory's original sell formula multiplied by `(WAD-tin)`
before flooring. That differs by one wei when the fee fraction is nonintegral.
The adapter was correct; the lead corrected the inventory formula during
qualification. No adapter arithmetic blocker was found.

The qualifier's saved over-limit rejections were checked to have the expected
buffer, pocket and residual reasons. Its source-expression comparison is a
quote-model qualification, not a historical transaction replay. The lead also
added allowance-delta checks and expected rejection-reason matching to the
qualifier after review feedback. Token pause,
blacklist, caller approval, recipient restrictions, gas and atomic settlement
are outside the admitted model. Zero input is a model no-op with a halt check,
not a claim that all corresponding zero-value token calls would succeed.

Capacity identity is valid for the single admitted LitePSM. The legacy PSM,
USDS converter and wrapper are separate unsupported models; future wrappers
must share and update this same underlying state rather than introducing a
second pocket copy. The family being supported does not qualify those models.

## Combined CLI integration acceptance — 2026-09-08

PASS for the bounded V2 + V3 + LitePSM integration. The adapter registry now
includes exactly those three implemented families. All 15 requested CLI cases
completed from cached snapshots at grid 10, with exactly one admitted LitePSM
state and zero network requests in every case.

```text
UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q tests/test_integration.py tests/test_quote_cli.py
9 passed in 0.49s

UV_CACHE_DIR=/tmp/swaparch-review-uv uv run python data/results/litepsm-integration/check.py
15 cases completed; exit 0; zero network requests
```

The saved runnable check blocks `RpcClient._rpc`, HTTP requests, socket
connections and snapshot persistence. It invokes the real `quote_command`,
captures actual admitted states, then independently reloads states from the
saved snapshot. Every reported winner (direct baseline, best single path,
saved reference where present, and best split) was reconstructed from its
serialized steps and reevaluated. A separate loop also threads token balances
and immutable pool states without calling `Evaluator`, checking every output,
available input and absence of residuals. The best tested split never falls
below the included single-path candidates.

Outputs below are token units before gas, for the best tested grid candidate:

| Block | Admitted pools | 1,000 USDC → DAI | 1,000 DAI → USDC | 100 WETH → USDC |
| --- | ---: | ---: | ---: | ---: |
| 23549991 | 34 | 1000 | 1000 | 333465.450885 |
| 23550060 | 34 | 1000.323247060089707173 | 1000 | 348314.021370 |
| 23728292 | 35 | 1000 | 1000 | 313537.905419 |
| 24356381 | 36 | 1025.827625420452464858 | 1000 | 232162.128453 |
| 25896003 | 35 | 1000 | 1000 | 239279.717900 |

LitePSM wins all five DAI→USDC cases and three USDC→DAI cases. It is available
but not selected by the winning WETH routes. This is a finite candidate search,
not evidence that a globally optimal router could never use LitePSM there.

The legacy PSM, USDS wrapper and converter remain explicit per-pool exclusions.
Spark has zero usable pools and an unsupported family row in every CLI report;
the CLI currently reports unimplemented families at family granularity rather
than listing their individual alias rows. The review's `summary.json` records
all three excluded Spark aliases and their canonical targets from the
inventory. No alias or wrapper is admitted as another LitePSM inventory.

The small acquisition change correctly captures an `Unsupported` initial read
plan as a per-pool failure and skips its dependent reads. Beyond the integration
unit test, a separate cached mixed-record check submitted an invalid wrapper
record beside a valid LitePSM record: only the invalid record was excluded,
the valid record remained admitted, and network requests stayed zero.

New review-owned artifacts are the runnable check, 15 complete CLI JSON
reports, and `summary.json` under `data/results/litepsm-integration/`, plus this
report update. No adapter, acquisition, inventory, snapshot or source files
were edited by this integration review. The prior quote-only limitations,
including unverified token settlement and historical USDC implementation
matching, remain unchanged.
