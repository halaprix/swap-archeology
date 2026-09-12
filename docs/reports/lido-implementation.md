# Lido implementation report

Implemented an offline, immutable Lido adapter limited to the curated mainnet
wstETH wrapper record. It reads the two global stETH rate values, validates
`wstETH.stETH()`, and carries `wstETH.totalSupply()` plus
`stETH.sharesOf(wstETH)` as finite backing state. Integer quote outputs follow
the public wstETH getters; wrap and unwrap state transitions use checked
uint256 arithmetic and the unwrap transfer's second share-floor.

`tests/test_lido.py` checks selector/read shape, protocol conformance, floor
rounding and round trips, finite supply/backing, zero/type/negative/domain
handling, immutability, record identity, and missing/failed/malformed ABI
results. It does not use RPC.

Run:

```
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run pytest tests/test_lido.py
```

The adapter is not qualified as supported by this work. It needs the lead's
five pinned snapshots and direct `getStETHByWstETH` /
`getWstETHByStETH` comparisons before inventory admission. Getter agreement
does not prove approvals, transfer pause state, recipient balance deltas, or
atomic settlement. `submit` and the withdrawal queue remain unsupported here.
