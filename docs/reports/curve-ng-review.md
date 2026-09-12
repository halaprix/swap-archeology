# Independent Curve NG and snapshot-index review

2026-09-08. The reviewer owned only this report. All checks used copied sources, saved responses and local execution; no RPC, transaction, source/test edit or external write was performed.

**Accept the bounded plain StableSwap-NG quote adapter at the individually qualified hashes, and accept the snapshot lookup index change.** No remaining defect was found within this reviewed scope. This is historical quote-model acceptance under the curated standard-transfer assumption. It does not establish token permissions, successful transfers, oracle upkeep, atomic settlement, or a compiled-bytecode match to the copied Vyper sources.

## Scope and corrected finding

The model requires mainnet pool/token/snapshot identities; a same-hash registry observation with matching coins/decimals, zero base pool and the NG factory among its base registries; a factory-reported allowed implementation address; and exclusively asset type 0 with explicit standard-transfer assumptions. Raw stETH, oracle, rebasing, ERC-4626 and metapool paths remain excluded.

**Chain identity was missing and is now fixed.** Initially, changing only the record chain, only one token's chain, or only the snapshot chain to 2 still loaded state. The lead added the mainnet check in `_record_identity` plus a regression test. All three independent reproductions now reject with `Unsupported`.

The two allowed implementation addresses are observed factory responses, not proof that deployed runtime bytecode was produced from the source copies. The four files in `data/protocol-sources/curve/manifest.json` match their recorded SHA-256. Math sources are pinned to Curve commit `911b5b45e4edafda96a74ec5f464a673c380456c`; the MetaRegistry source has its separately recorded commit.

## Historical arithmetic and identity evidence

All 92 saved `get_dy(int128,int128,uint256)` calls were independently decoded, including pool address, selector, indexes, amount and raw result. Each agrees with the adapter and an independent source-order invariant calculation plus a closed-form integer quadratic solution for `y`. The quadratic calculation uses `isqrt(b*b + 4*c)` rather than the adapter's iterative `get_y` implementation.

| Block | Observed selected Curve records | Qualified plain NG pools | Exact size/direction matches |
| --- | ---: | ---: | ---: |
| 23549991 | 280 | 4 | 16 / 16 |
| 23550060 | 280 | 4 | 16 / 16 |
| 23728292 | 284 | 5 | 20 / 20 |
| 24356381 | 290 | 5 | 20 / 20 |
| 25896003 | 432 | 5 | 20 / 20 |
| Total | | 23 pool/block observations | 92 / 92 |

The admitted historical pools are two-coin pools, each checked at 1 and 100 input-token units in both directions. Generic two-through-eight-coin arithmetic is separately tested below; this does not qualify historical multi-coin pools whose tokens or asset types remain unresolved.

For all 23 admitted pool/block observations, raw MetaRegistry responses independently match the stored coin identities, decimals, zero base pool, registry handlers and handler-to-base-registry mapping. The qualifier's raw state calls match the snapshot, and `load_states` admits exactly the pool ids marked qualified at that hash. Inventory presence is evidence of deployment by an observed block; no exact creation block was invented.

Evidence is retained in `data/validation/curve-ng/0x*.json`, `data/discovery-evidence/curve-ng/0x*.json`, and the per-hash `data/discovery-evidence/curve/` directories. The broader raw probes include unsupported asset types and three failed `stored_rates()` calls per pin. Qualification keeps failures and exclusions explicit; it does not assign zero liquidity to unreadable state.

## Fee, invariant and shared-state checks

The port preserves the pinned source's operation order:

- `xp[k] = floor(rate[k] * balance[k] / 1e18)`.
- `get_D` applies each product/division in source order and divides the product term by `n**n` after the per-coin loop. Balanced invariants equal `n * balance` for every coin count 2–8.
- `get_y` solves the same coefficients and applies the source's one-unit output subtraction. The model uses `A_precise`; blocks where `A() * 100` differs are conservatively excluded because the pinned view computes with rounded `A()`.
- Dynamic fees use the floored midpoint balances and the correct `1e10` fee denominator, including the base-fee branch when the off-peg multiplier is at most the denominator.
- The input LP balance increases by the full standard-token input. The output LP balance decreases by user output plus `floor(floor(fee_xp * admin_fee / 1e10) * 1e18 / rate_out)`. This matches `_balances`, `_transfer_in`, `_transfer_out` and the separate `admin_balances` claim in the pinned non-rebasing pool.

A seeded independent experiment covered 1008 directed swaps across 2–8 coins, mixed 6/18 decimals, varied amplification, fixed/dynamic fees and imbalanced reserves. Each was followed by another swap through the same immutable multi-coin state: all 1008 follow-up outputs and balance vectors also matched the reference. The original balance tuples remained unchanged. The shared capacity id is the whole pool id; coin directions do not create separate copies of liquidity.

A separate artificial-rate fixture distinguishes the admin rounding order: input 7318 produces `fee_xp = 3`; the correct admin output is 1, while converting the fee to token units before applying the admin share gives 2. The adapter returns the correct updated balance. This is an arithmetic witness, not an observed historical token-rate claim.

Checked arithmetic rejects overflow, underflow, zero divisors and nonconvergence outside the modeled uint256 domain. The adapter deliberately rejects empty/dust states and unqualified semantics. Zero input is a local no-op; these local conventions are not assertions that the contract accepts a zero-input transaction or rejects every positive zero-output transaction.

Thirty independent mutations covered every one of the ten reads being missing, unsuccessful, or malformed. All raised `Unsupported`. Existing tests additionally cover empty balances, dust, overflow, precise-A disagreement, nonzero asset types, raw stETH and immutability.

## Fresh batch/direct parity

`data/validation/multicall-parity/curve-ng/result.json` covers all ten state-read methods on the calm USDe/USDC pool `0x02950460e2b9529d0e00284a5fa2d7bdf3fa4d72`. The reviewer decoded the actual `aggregate3` request/result in its separate batch cache, matched each subcall against ten individual hash-pinned `eth_call` responses, and checked the report's raw bytes. All ten methods match. The artifact records three batch-client requests and twelve direct-client requests; these acquisition counts belong to the lead's run, not reviewer RPC activity. This parity check is one representative pool/block, not a claim that every state was independently acquired twice.

## Snapshot lookup index

`StoredSnapshot` now builds a read-only `MappingProxyType` index keyed by `(to, data)`. `setdefault` retains the original first-result behavior for duplicate identities. Tags remain irrelevant to lookup; missing keys retain `KeyError`; persisted call rows and order are unchanged. The private index is excluded from dataclass comparison and cannot be mutated through the mapping. Inspected callers construct tuple-backed snapshots and serialize call records explicitly; no caller relies on pickling or `asdict` of the snapshot itself.

The reviewer compared indexed lookup against the previous linear expression for every one of 8039 calls in the then-current calm snapshot. All returned the identical `CallResult` object. One pass took approximately 0.000677 seconds indexed versus 2.107254 seconds linear on this run. This independently confirms both preserved results and removal of the per-lookup linear scan; wall-clock figures are local measurements. The snapshot had five more calls than the lead's earlier 8034-call measurement.

## Validation commands

```sh
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest \
  tests/test_curve_ng.py tests/test_snapshot_store.py tests/test_integration.py -q
# 14 passed in 0.47s
```

The independent raw replay additionally used `uv run --offline python -` to decode the saved calldata/results and calculate the source-order `D`, then:

```python
# c and b retain the pinned get_y coefficient floors; b includes subtraction of D.
y = (isqrt(b * b + 4 * c) - b) // 2
raw_out = xp[j] - y - 1
fee_xp = raw_out * dynamic_fee // 10**10
amount_out = (raw_out - fee_xp) * 10**18 // rates[j]
admin_out = (fee_xp * admin_fee // 10**10) * 10**18 // rates[j]
```

Observed outputs were `92` historical matches, `1008` synthetic directions plus `1008` shared-state follow-ups, `7` balanced-invariant checks, `30` malformed/missing/failed-read rejections, and successful rejection of all `3` reported chain mismatches. These independent checks supplement the persisted tests and canonical raw-response qualification; they do not broaden admission to excluded Curve families or tokens.
