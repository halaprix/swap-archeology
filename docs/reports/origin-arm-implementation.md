# Origin Lido ARM implementation report

Implemented the bounded historical Origin Lido ARM adapter and offline tests.
It supports only the canonical WETH/stETH proxy and the five hash-pinned study
snapshots. The adapter rejects Ethena and all other Origin ARMs, unknown block
hashes, historical pause ABI mismatch, mismatched immutable tokens, malformed calls, zero
traderates, invalid queue accounting, and mismatched share/balance/reserve
observations.

The source quote uses the historical ARM's `amountIn * traderate / 1e36`
integer formula. It applies finite WETH and stETH output reserves, subtracting
the outstanding LP withdrawal queue from WETH. Its stETH inventory is carried
as Lido shares and re-derived after every swap, which is necessary for
repeated-state correctness with a rebasing token.

`getReserves()` is read only on the exact hashes whose record observations and
raw files prove it exists. Earlier hashes use the same source formula from
balances and withdrawal getters. `paused()` is requested only for hashes whose
observation proves that getter exists; a true value is conservatively rejected,
not treated as proof that the historical swap entrypoint was gated. Every load
also verifies the proxy's public `implementation()` result against the record's
exact historical mapping; the adapter does not extrapolate that capability to
an ABI interval.

Validation run:

```
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run pytest tests/test_origin_arm.py
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run ruff check src/swaparch/adapters/origin_arm.py tests/test_origin_arm.py
```

The adapter loaded all five cached historical snapshots against the discovery
record and its implementation observations. Reconstructed `(reserve0,
reserve1)` values were:

| block | WETH reserve0 | stETH reserve1 |
|---|---:|---:|
| 23549991 | 623917687646278 | 341418453374818574541 |
| 23550060 | 623917687646278 | 341418453374818574541 |
| 23728292 | 7223463112265426923 | 389874671015945906382 |
| 24356381 | 304139522905295164 | 1 |
| 25896003 | 2980893066769446 | 313738966461309 |

The tests cover source read shapes at both ABI layouts, immutable identity,
pause handling, integer quote arithmetic, queue-adjusted WETH capacity,
stETH share-state changes, failure paths, malformed/inconsistent reads, and
protocol conformance. No node RPC or fork was used.

The stETH nominal balance cap is conservative at a share-rounding boundary:
with pooled ether 121, total shares 100, and two ARM shares, source may accept
a requested nominal transfer of three because it moves only two shares. The
model does not route that fractional excess. The output remains a source-
requested amount and is not an atomic recipient-balance claim; this settlement
boundary does not block the five-hash source quote qualification.
