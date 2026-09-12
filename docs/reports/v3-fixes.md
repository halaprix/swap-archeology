# Uniswap V3 bounded fixes

## Changes

- `UniV3State` rejects an advertised bitmap range with missing words, so public
  construction cannot turn an unavailable read into empty liquidity.
- `dependent_requests` raises `Unsupported` for a failed bitmap result instead
  of decoding `None`; acquisition can classify the pool as unsupported.
- The amount0 overflow fallback now rejects the checked uint256 addition that
  the Solidity reference rejects.

## Regression checks

- An in-range missing word (76) is rejected at the shared state boundary.
- A failed word-76 result causes explicit `Unsupported` during dependent reads.
- The checked-add overflow input raises `EvmRevert`.
- The upstream exact-output-cap vector returns
  `(417332158212080721273783715441581, 1, 1, 1)`.

Ran:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest -q -p no:cacheprovider tests/test_uniswap_v3_state.py tests/test_uniswap_v3_math.py
39 passed in 1.59s
```

## Limit

These offline fixes do not provide a fresh RPC or Multicall validation.
