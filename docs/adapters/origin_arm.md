# Origin Lido ARM adapter

`src/swaparch/adapters/origin_arm.py` is a bounded, offline exact-input model
for only Origin's historical Lido stETH/WETH ARM proxy:
`0x85b78aca6deae198fbf201c82daf6ca21942acc6`.

It is deliberately not an adapter for Ethena ARM or the other Origin ARMs.
Those use later, incompatible multi-asset interfaces and remain unresolved.

## Historical identity and ABI boundary

The adapter admits only exact Ethereum hashes described by mandatory
`config.historical_observations[blockHash] = {number, get_reserves,
paused_getter, implementation}`. It reads the proxy's public `implementation()`
getter and requires an exact match. It validates proxy identity, `token0 == WETH`,
`token1 == stETH`, 18-decimal token records, the old `traderate (old) ABI` record
generation, and a false `paused()` result when that source version exposes the
getter. An unknown hash is unqualified;
the implementation does not infer an ABI interval from a block number.

`getReserves()` is read at pins 24356381 and 25896003 only. At pins 23549991,
23550060, and 23728292 it is known to revert, so the adapter uses the source
formula from independent balance and queue reads. The source checkout is
`<external-repos>/arm-oeth`:

- `4d7dc50f7e661d4d9e40900c3e0e782e96396775`, `src/contracts/AbstractARM.sol`:
  legacy `traderate0/1` exact-input swap and the liquidity-withdrawal gate.
- `0a4b1769e43c1c3bf0c4b129a373295e4a10fb11`, `src/contracts/AbstractARM.sol`:
  added `getReserves()` with the same formula and token ordering.

Raw files under `data/discovery-evidence/origin-arm-layout/` prove both the
getter boundary and proxy implementation mapping at the five pins. Add the
same exact per-hash observations to the discovery record before extending this
adapter to other historical blocks.

## Reads and quote model

Each snapshot reads `token0`, `token1`, `traderate0`, `traderate1`,
`withdrawsQueued`, `withdrawsClaimed`, WETH/stETH `balanceOf(ARM)`, stETH
`sharesOf(ARM)`, `getTotalPooledEther`, and `getTotalShares`; the exact
observation adds `paused()` and/or `getReserves()` only where that version
exposes the getter.

All arithmetic is checked `uint256` integer arithmetic. Source output is:

```
WETH -> stETH: amountIn * traderate0 / 1e36
stETH -> WETH: amountIn * traderate1 / 1e36
```

`fee()` is intentionally absent: it is an LP performance fee, not a trader
swap fee. The first four observed implementations have no pause getter. The
adapter rejects a true value where the fifth pin exposes `paused()` as a
conservative model restriction; it is not a proven historical swap gate.

The usable directional reserves are:

```
reserve0 = max(0, WETH.balanceOf(ARM) - (withdrawsQueued - withdrawsClaimed))
reserve1 = stETH.balanceOf(ARM)
```

The model never adds Morpho balances, Lido queue assets, or LP redemption
claims. For newer pins it rejects a `getReserves()` value that disagrees with
this independently reconstructed formula.

The stETH nominal reserve is a deliberately conservative output cap. For
example, at total pooled ether 121 and total shares 100, an ARM with two shares
has nominal balance two, while a requested transfer of three converts to two
shares and can succeed in the source token. The model declines that fractional
rounding excess as routable liquidity.

stETH is share based. The state retains `sharesOf(ARM)` and applies
`floor(amount * totalShares / totalPooledEther)` to each incoming or outgoing
stETH transfer, then derives the next nominal stETH balance from shares. This
prevents repeat swaps from spending inventory twice after rebasing rounding.

The quoted stETH output is the ARM source's requested transfer amount. A
recipient's observable stETH balance delta can differ because Lido transfers
shares and depends on that recipient's existing shares. This adapter has no
recipient state or atomic transaction evidence, so its quote is not a
settlement or recipient-balance guarantee.

At block 25896003, the checked formula gives 19.293422946852522412 raw WETH,
19.290442053785752966 outstanding queued withdrawals, and only
0.002980893066769446 usable WETH; stETH reserve is
0.000313738966461309. The raw WETH balance must not be used as trade depth.

The inventory marks this Lido ARM record `supported` for the five qualified
hashes. Gas is unknown. Recipient-balance settlement is outside this bounded
source quote admission.
