# V2 implementation handoff

Implemented offline V2 discovery and exact pair state in
`src/swaparch/discovery/uniswap_v2.py` and
`src/swaparch/adapters/uniswap_v2.py`.

The source trace is pinned locally by
`data/protocol-sources/uniswap_v2/manifest.json`: V2 core commit
`4dd59067c76dea4a0e8e4bfdda41877a6b16dedc` and periphery commit
`ed24991304291297c3b4a52818d02f46a17aa9a2`. The five exact paths and SHA-256
values are listed in [the adapter note](../adapters/uniswap_v2.md).

The bounded model has no RPC code and does not admit a pool merely because its
creation log decoded. It requires explicit standard-transfer attestations,
rejects raw stETH, empty reserves, failed/malformed reads, token identity
disagreement, reserve/balance mismatch, `uint256` arithmetic overflow, and a
post-swap `uint112` reserve overflow. `swap` returns a separate immutable state;
`quote_exact_in` delegates to the same validation path, so it cannot report an
amount that a state transition would reject.

Offline validation command:

```
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest tests/test_uniswap_v2.py -q
```

The test covers the independent rational constant-product expression, state
consumption across sequential swaps, selectors/read plan, all listed rejection
paths, `PairCreated` decoding, ordered pair-filter coverage, and creation block
hash capture. It does not provide Router02 or live-pair agreement. The lead's
serialized acquisition/cross-check remains required before an implementation
acceptance or `supported` claim.

Result: `9 passed`. A subsequent offline full suite run completed with
`79 passed, 1 skipped`.
