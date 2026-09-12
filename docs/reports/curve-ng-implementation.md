# Curve NG plain-pool implementation report

Implemented `src/swaparch/adapters/curve_ng.py`: a local, immutable,
exact-input StableSwap-NG plain-pool state model.  The model consumes the
factory/registry identity and ten cached source reads, supports two to eight
standard non-rebasing type-0 coins, and retains all coins in one shared state.

The source arithmetic follows the pinned Curve NG implementation: `A_precise`
in `get_D`/`get_y`, source-order floors, off-peg dynamic fee, and the admin-fee
conversion floor.  A state transition adds full input and removes both user
output and the separately floored admin output claim.  Checked uint256 helpers
turn unsafe/out-of-domain inputs into `Unsupported`.

Offline validation:

```
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run pytest tests/test_curve_ng.py -q
5 passed
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run ruff check src/swaparch/adapters/curve_ng.py tests/test_curve_ng.py
All checks passed
```

The tests compare a three-coin quote against a separate literal Vyper-formula
translation, prove sequential multi-token state consumption, and cover empty,
dust, overflow, rounded-`A`, type-1 asset, raw-stETH, and unqualified-token
rejections.  The cached calm snapshot also loaded the USDe/USDC type-0 pool
`0x02950460e2b9529d0e00284a5fa2d7bdf3fa4d72` with an injected fixture-only
standard-transfer declaration and produced `999486` USDC units for `1e18` USDe
input.

This is not a support verdict.  The root qualifier must compare each candidate
against the pool's own `get_dy` at the same block hash and record that evidence;
oracle-rate NG pools and the three `stored_rates` read failures remain excluded.
