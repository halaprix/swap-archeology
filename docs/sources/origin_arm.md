# Origin ARM (and Ethena ARM)

**Deployments.**

| ARM | proxy | impl | 23549991 | 23550060 | 23728292 | 24356381 | 25896003 |
|---|---|---|---|---|---|---|---|
| Lido stETH/WETH | `0x85B78AcA6Deae198fBF201c82DAF6Ca21942acc6` | `0x850da2E2…` `LidoARM` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Ethena sUSDe/USDe | `0xCEDa2d856238aA0D12f6329de20B9115f07C366d` | `0xebB2B667…` `EthenaARM` | — | — | — | ✓ | ✓ |

**The Ethena ARM exists.** SOURCE_DISCOVERY.md left it unverified; it was deployed at block 23924639 (2025-12-01) and is confirmed on chain here — `liquidityAsset()` = USDe, `activeMarket()` = `0x0dc20109ea012f050beda184844c1ed5ec6da33a`. It contributes nothing at the first three pins.

Also on mainnet, unanalysed: WETH ARM `0x68025A46…`, USDC ARM `0x9E3A7026…`, ETH ARM `0xB9B85dFE…`, EtherFi ARM `0xfB0A3CF9…`.

Discovery is curated from the Origin ARM registry docs plus `arm-oeth/build/deployments-1.json`; there is no factory.

## One family, two incompatible ABI generations

The Lido ARM is the **old** generation: `token0`/`token1`/`traderate0`/`traderate1`. The Ethena ARM is the **new multi-asset** generation with `baseAssetConfigs`/`getBaseAssets` and **no traderate accessors at all**. Reading `arm-oeth` `main` to build a Lido ARM adapter produces a wrong integration — on `main`, `traderate0/1` are renamed `_deprecatedTraderate0/1`.

Worse, **the same Lido ARM proxy changed ABI mid-study**: `getReserves()` reverts at 23549991, 23550060 and 23728292 and succeeds at 24356381 and 25896003. The adapter must dispatch on the block, not on the address. `traderate0`/`traderate1` do work at all five pins.

## Quote semantics — Lido ARM

There is **no quoter**. The deployed ABI has no `getAmountOut`, `getAmountsOut` or `quote`. `PRICE_SCALE = 1e36`.

```
WETH -> stETH:  amountOut = amountIn * traderate0() / 1e36
stETH -> WETH:  amountOut = amountIn * traderate1() / 1e36
exact out:      amountIn  = amountOut * 1e36 / traderate + 3      (the +3 is the contract's own rounding guard)
```

`traderate0` across the pins: 1.000100010001, 1.000100010001, 1.000100010001, 1.0000400016000641, 1.000020000400008. `traderate1`: 0.9998, 0.9998, 0.9990, 0.9993598666265276, 0.9998305797950944. `crossPrice()` = 0.99996 at 25896003.

`fee()` = 2000 is the **performance fee on LP yield** (scale 1e5), *not* a swap fee. The swap spread lives entirely in the traderates relative to `crossPrice`.

Swap functions: `swapExactTokensForTokens(IERC20 inToken, IERC20 outToken, uint256 amountIn, uint256 amountOutMin, address to)` plus a V2-router-shaped `(uint256, uint256, address[] path, address to, uint256 deadline)` overload, and the two `swapTokensForExactTokens` mirrors.

## Capacity — the finding that matters

```
reserve0 = max(0, WETH.balanceOf(arm) - (withdrawsQueued - withdrawsClaimed))
reserve1 = stETH.balanceOf(arm)
```

`getReserves()` returns exactly this, ordered as (token0, token1), and is the single read to use where it exists. At 25896003:

| quantity | value |
|---|---|
| `WETH.balanceOf(arm)` | **19.293422946852522412** |
| `withdrawsQueued − withdrawsClaimed` | **19.290442054053785** |
| `getReserves().reserve0` (tradeable WETH) | **0.002980893066769446** |
| `getReserves().reserve1` (tradeable stETH) | 0.000313738966461309 |

**The raw WETH balance overstates tradeable depth by roughly 6500×.** Nearly all of it is reserved for the LP withdrawal queue. At 24356381 the tradeable reserve was 0.304 WETH. At pins 4 and 5 this venue offers effectively **zero** usable depth in either direction — a measurement, not an exclusion; re-read per block.

**A trader swap never pulls from the lending market or the Lido withdrawal queue.** `_transferAsset` checks only the ARM's own ERC20 balance; funds parked in Morpho (`activeMarket()` = `0xb7cefe4c…`) are moved by the separate governed `allocate()`, and market/queue withdrawals serve *redemptions*. Adding the Morpho position or queued Lido withdrawals to tradeable depth is wrong.

LP-share withdrawals (`requestRedeem` → `claimDelay` → `claimRedeem`, `claimable()`) are a different operation from a trader swap and must never count as swap depth. `capManager()` is `0x0` (caps disabled), so it is off the quoting path. `paused()` must be false.

Before the upgrade (pins 1–3) `getReserves()` is unavailable: compute the formula from `WETH.balanceOf`, `stETH.balanceOf`, `withdrawsQueued()` and `withdrawsClaimed()`.

## Quote semantics — Ethena ARM (unresolved)

```
buy:  amountOut = convertedAmountIn * config.buyPrice / 1e36
sell: amountOut = convertedAmountIn * 1e36 / config.sellPrice
```

with `config` from `baseAssetConfigs(address)`, reserves from `getReserves(address reserveBaseAsset)`. The `_convert()` hook is applied to the base-asset amount for appreciating base assets — **sUSDe appreciates**, so unlike LidoARM's stETH this is not the identity and must be modelled.

Status `unresolved`: the raw reads at 25896003 were decoded heuristically here (`getReserves(sUSDe)` gave two words ≈ 13.425 and 35.697; `baseAssetConfigs(sUSDe)` gave 8 words with word0 ≈ 0.9995147 and words 1/4 ≈ 0.99996 at scale 1e36), but the struct layouts are **not confirmed against the verified ABI**, so no quote can be trusted yet.

**Naming.** SOURCE_DISCOVERY.md's warning stands: this is one venue and it belongs here, not duplicated under `lido`.

**Open.** The exact block where the Lido proxy became `LidoARM` is unpinned (Oct 2024 window; use ~20970000 as an indexer start). There is at least one *further* upgrade between 23728292 and 24356381 that introduced `getReserves` — resolve both with `Upgraded(address indexed)` topic0 `0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b`. EthenaARM struct layouts need the verified ABI. Whether the other four mainnet ARMs are in scope is a question for the lead.
