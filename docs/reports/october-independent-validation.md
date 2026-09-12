# October gap: independent contract and transaction checks

The tested **V3 implementation is correct at the suspicious blocks and sizes**.
V2 is easier to verify, but switching to V2 would not remove the price discrepancy.
The evidence now supports a real difference between these Ethereum pool prices
and the external reference market, rather than a V3 calculation error.
This does not establish why arbitrage did not close it.

## Independent checks

Blocks: **23550020, 23550044, 23550046**. These are historical read-only calls;
no keeper simulation, new adapter or full-sweep restart was performed.

- **V3:** fresh hash-pinned `slot0` and active liquidity match cached state for
  WETH/USDC `0x88e6…5640`, WETH/DAI `0x6059…a270`, and DAI/USDC `0x5777…2168`
  at all three blocks. Canonical QuoterV2 matches our output and resulting
  sqrtPriceX96 **exactly**, zero wei difference, for nine direct 1/10/100-WETH
  quotes and six legs of three sequential WETH→DAI→USDC quotes. The second
  leg uses the actual first-leg output. This is independent contract evidence,
  stronger than the earlier native/Python report parity.
- **V2:** fresh reserves and token order match the snapshots for WETH/USDC,
  WETH/USDT and WETH/DAI at all three blocks. **27 quotes** match exactly across
  our adapter, the independent integer reserve formula, and canonical Router02
  `getAmountsOut`. Formula: `997*amountIn*reserveOut //
  (1000*reserveIn + 997*amountIn)`, from
  [Uniswap's canonical library](https://raw.githubusercontent.com/Uniswap/v2-periphery/master/contracts/libraries/UniswapV2Library.sol).
- **External market:** downloaded Binance's official ETH/USDC and ETH/USDT
  one-minute spot archives for October 10 and verified their published SHA256
  checksums. Archive timestamps are microseconds in 2025; the parser converts
  them explicitly. These are trade candles, not executable order-book depth.
  [Binance archive specification](https://github.com/binance/binance-public-data).

## At 21:35:23 UTC, block 23550044

| Observation | USDC per WETH |
|---|---:|
| V3 spot, fresh `slot0` | 3375.80 |
| V3 canonical quote, sell 1 WETH | 3373.78 |
| V3 canonical quote, sell 100 WETH | 3340.82 |
| V2 spot, fresh reserves | 3340.95 |
| V2 canonical quote, sell 1 WETH | 3330.04 |
| V2 canonical quote, sell 100 WETH | 3244.87 |
| Existing aggregate model, sell 1 WETH | 3420.09 |
| Existing aggregate model, sell 100 WETH | 3372.35 |
| Standard Chainlink cross-rate | 3765.11 |
| Aave configured cross-rate | 3718.75 |
| Binance ETH/USDC entire 21:35 minute low–high | 3717.64–3774.81 |

The standard Chainlink cross lies inside Binance's traded minute range. Thus
calling it an erroneous ETH price solely because it exceeds an Ethereum pool
quote would be unjustified. A one-minute candle does not identify the exchange
price at the exact transaction instant, but even its low is materially above
these pool prices. ETH/USDT's same-minute range was 3726.69–3780.61 USDT/ETH;
that pair is recorded separately, without assuming USDT and USDC are identical.

## Intrablock liquidity hypothesis

Read all Swap/Mint/Burn events for the reference V3 pool in blocks
23550019–23550021 and 23550043–23550047. There are **12 swaps and no Mint or
Burn events** in those eight blocks. This provides no evidence of JIT liquidity
being added and withdrawn in that pool during these particular blocks.

At 23550044, all three emitted post-swap spot prices lie between **3372.20 and
3375.80**; the last equals the snapshot. At 23550020, the two post-swap prices
are **3342.74–3345.71**, also ending at the snapshot. Block 23550046 contains no
swap in this pool and preserves its previous price. Actual swap amounts and
transaction/log order are retained in the event artifact.

So the available events do not show a hidden recovery toward 3700 inside those
blocks. This is a bounded check of one pool, not proof that temporary liquidity
never existed elsewhere. Swap events show prices after each swap, not every
internal EVM state, and a complete transaction replay was not performed.

## Implication

**Keep V3, and use V2 as an independent control.** A single V3 pool is an
incomplete execution benchmark, but these checks do not support replacing
concentrated-liquidity math with V2 to fix a bug. V2 already participates in our
aggregate and is worse for the direct USDC quotes shown above.

The remaining research question is why these on-chain venues traded below the
external reference market. These observations do not isolate causes such as
flow imbalance, arbitrage inventory/funding constraints, transaction inclusion,
or missing venues/private liquidity. More adapters may improve aggregate
execution, but cannot change the verified historical quotes of existing pools.

## Reproduction and evidence

- `.venv/bin/python scripts/october_v3_check.py`:
  `outputs/october-validation/v3.json` and separate raw RPC cache.
- `.venv/bin/python scripts/october_v2_check.py`:
  `outputs/october-validation/v2.json` and separate raw RPC cache.
- `.venv/bin/python scripts/october_intrablock_check.py`:
  `outputs/october-validation/intrablock.json`, raw logs, hashes and event order.
- `.venv/bin/python scripts/october_external_candles.py`:
  `outputs/october-validation/external-candles.json`, original ZIP archives and
  published `.CHECKSUM` files. Run from the repository root.

No model correction was needed for the tested paths. Other source models,
full aggregate routes, token settlement, omitted pools and other blocks are
not newly qualified by these checks. The bounded aggregate solver is still
not a global-optimality proof. All evidence remains local and read-only.
