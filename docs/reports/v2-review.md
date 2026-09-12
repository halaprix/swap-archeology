# Independent V2 acceptance review

2026-09-07. The review was independent of implementation and its execution was offline. Review outputs are this report and `data/results/v2-integration/`. No node RPC, source/test edits, changes to existing phase-2 results, commits, or external writes were performed.

**Verdict: accept the bounded canonical-mainnet V2 reserve-quote adapter and its block-specific admission evidence.** This is conditional on the explicitly curated standard-transfer assumption, with raw stETH and reserve/balance mismatches excluded. It establishes neither account permissions nor successful token transfer or atomic settlement. The combined solver remains the documented finite baseline.

## Findings and resolution

1. **Discovery coverage hash was ignored on warm reuse — fixed and independently rechecked.** Initially, supplying block 25896003 with a different hash silently reused all 56 coverage records. `scripts/uniswap_v2_run.py::discover` now compares each coverage anchor with the supplied block or the cached anchor header and raises `ValueError` on disagreement. An unchanged warm run still makes zero client calls. A mocked ninth token, represented in the V3 metadata inventory, produces all 16 new ordered-pair filters over the entire 10000835–25896003 range. No writes or RPC were used in the initial mocked probes. The lead subsequently added `tests/test_v2_discovery_resume.py`; the reviewer inspected and ran that persistent warm/backfill/changed-anchor regression successfully. Header-cache reuse does not independently detect a live network reorg without a refreshed header; this is a historical cached-data check.
2. **V2 batch/direct read parity evidence was initially missing — closed.** The lead added `data/validation/multicall-parity/uniswap_v2/result.json` plus separate batch/direct caches. The reviewer independently decoded the actual `aggregate3` calldata and response, matched all five requests/results to five separate hash-pinned direct calls, and matched both to the saved calm-block snapshot. This covers all adapter read methods on canonical WETH/USDC at block 25896003; it does not claim every pair/block was separately fetched twice.
3. **Documentation was stale at initial review.** `docs/sources/uniswap_v2.md` said discovery had not run; `docs/adapters/uniswap_v2.md` said Router checks had not run and implied independent token-semantics attestation. The lead updated both documents; the reviewer reread the changes and confirmed the discovery/qualification counts and curated-assumption wording now match the evidence.

The USDe intermediate correction is correct: `DEFAULT_INTERMEDIATES` now uses `0x4c9edd5852cd905f086c759e8383e09bff1e68b3`, matching the discovery token constant and cached pool metadata. Its regression check passes.

## Independent evidence checks

All five files in `data/protocol-sources/uniswap_v2/manifest.json` match their recorded SHA-256. The pinned Pair contract applies the 3/1000 input fee in the balance-based invariant and requires final balances to fit uint112. The pinned Library uses checked uint256 arithmetic and floors its quotient. The adapter retains the full input in post-swap reserves, reproduces the exact floor, rejects arithmetic/uint112 overflow and rejects positive inputs rounding to zero. Zero input is an explicit local no-op, not an executable zero-output V2 swap.

All 14 discovered pair addresses independently match the Library's CREATE2 derivation using the canonical factory and init-code hash. Each has one matching saved factory creation log with the recorded creation number, block hash and transaction hash. All 56 coverage artifacts match their filters and anchor hash. No fabricated creation block was observed.

For every admitted pool/block, raw reserves and balance responses agree. Each saved check's request calldata was decoded to verify input, directional reserves, Router address, selector and two-token path. Both raw Router responses were decoded independently, without using the adapter's quote helper. In addition to integer arithmetic equality, the Pair invariant was checked at output `q` and `q + 1`: `q` passes and `q + 1` fails.

| Block | Live V2 pairs | Admitted | Qualified size/direction quotes | Raw Router responses |
| --- | ---: | ---: | ---: | ---: |
| 23549991 | 12 | 10 | 40 | 80 |
| 23550060 | 12 | 10 | 40 | 80 |
| 23728292 | 12 | 10 | 40 | 80 |
| 24356381 | 14 | 11 | 44 | 88 |
| 25896003 | 14 | 11 | 44 | 88 |
| Total | | | 208 | 416 |

Every admitted record's `validated_block_hashes` agrees exactly with the corresponding validation rows. Missing, failed and malformed responses were independently injected at each of the five read positions: all 15 cases raise `Unsupported`. Six additional type/uint256 boundary cases were rejected. Existing tests cover reserve/balance divergence, empty reserves, unqualified semantics, raw stETH, zero/dust input, wrong token direction, immutability and uint112 overflow.

A real evaluator plan consuming the same V2 pair twice matched an independent two-step reserve calculation and produced less output than two quotes from untouched reserves. The original state stayed unchanged. Solver/evaluator code threads the same pool id through later steps; the canonical discovery id and CREATE2 checks prevent the inspected inventories from counting the same V2 liquidity as separate pools.

After V3 qualification finished, the reviewer completed all five combined CLI runs with a client stub that permits only loading the pinned cached header and rejects other RPC operations. Socket connection attempts were also forbidden. Each run uses 100 WETH and the default grid of 10 parts, includes both source families in one solver call, retains all 15 source statuses and uses zero RPC. All four selected plans per pin (direct, single path, split, saved reference) were replayed with explicit ordered token balances and copied pool state, then checked again with the evaluator. All 20 plans match their saved step outputs, spend exactly 100 WETH and leave no input or intermediate residual.

| Block | V2 / V3 admitted | Best direct USDC units | Best single path USDC units | Best split USDC units | Families in winning split |
| --- | ---: | ---: | ---: | ---: | --- |
| 23549991 | 10 / 23 | 332132193469 | 332132193469 | 333465450885 | V2 + V3 |
| 23550060 | 10 / 23 | 343808420918 | 345744686486 | 348314021370 | V3 |
| 23728292 | 10 / 24 | 312479015959 | 313489450570 | 313537905419 | V3 |
| 24356381 | 11 / 24 | 219199643138 | 227638586207 | 232162128453 | V2 + V3 |
| 25896003 | 11 / 23 | 239131739028 | 239240556009 | 239279717900 | V3 |

Amounts above are USDC smallest units (six decimals), before gas. Each best split is at least as good as the selected single-path/direct alternatives. Full reports are `data/results/v2-integration/weth-usdc-100-<block>.json`. `acceptance.json` records their SHA-256 hashes and preserves the before/after hashes of both `data/results/phase2/` reports; those existing artifacts were unchanged. These are finite-baseline historical compositions, not global optima or atomic execution results.

## Commands and reproduction

Targeted suite:

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest tests/test_uniswap_v2.py tests/test_solver.py tests/test_evaluator.py tests/test_quote_cli.py -q
# 31 passed in 0.21s

UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest tests/test_v2_discovery_resume.py -q
# 1 passed in 0.19s
```

The following compact offline command reproduces the essential raw-byte arithmetic and independent invariant checks. The fuller reviewer execution additionally checked calldata, CREATE2 identities, snapshot balances, coverage/log provenance, every source hash, malformed-state cases, mocked discovery and actual parity-cache envelopes as described above.

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python - <<'PY'
import json
from pathlib import Path
words = lambda raw: [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]
count = 0
for path in Path('data/validation/uniswap_v2').glob('*.json'):
    data = json.loads(path.read_text())
    for row in data['rows'].values():
        if not row['supported']:
            continue
        for check in row['checks']:
            pure, routed = check['getAmountOut'], check['getAmountsOut']
            amount, x, y = words('0x' + pure['data'][10:])
            assert amount == check['amount_in']
            q = amount * 997 * y // (x * 1000 + amount * 997)
            assert pure['success'] and routed['success']
            assert words(pure['result']) == [q]
            assert words(routed['result']) == [32, 2, amount, q]
            assert q == check['local_out']
            adjusted = (x + amount) * 1000 - amount * 3
            assert adjusted * (y - q) * 1000 >= x * y * 1000**2
            assert adjusted * (y - q - 1) * 1000 < x * y * 1000**2
            count += 1
assert count == 208
print(count, 'quotes;', count * 2, 'raw Router responses matched')
PY
```

Limits: discovery currently derives its token universe from tokens already represented in V3 pool metadata; it is not an independent universal token registry. The new-token backfill proof applies when that metadata includes the new token. The `standard` mapping is a curated quote assumption, automatically assigned by this runner to known non-stETH tokens; it is not a generic token-behavior audit. Qualification uses two sizes in each direction and validates local arithmetic at a hash, not arbitrary token settlement. No global optimization, full historical sweep, live reorg detection, or execution guarantee is accepted here.
