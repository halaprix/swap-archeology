# Uniswap V3 adapter — Ethereum mainnet

Status: **supported within recorded block-specific quote validation**. The original
WETH/USDC 500-pip pool has checks at two pins and nine saved funded-swap comparisons.
At block 25896003, 23 of 52 discovered pools pass four individual QuoterV2 size/direction
checks; 29 remain excluded. See `data/validation/uniswap_v3/` and
`docs/reports/phase1-repair-review.md`. Quote validation does not prove settlement.

Research-only. Nothing here executes a transaction.

## 1. Identity

| item | value | evidence |
|---|---|---|
| Factory | `0x1F98431c8aD98523631AE4a59f267346ea31F984` | `data/adapters-evidence/uniswap_v3/factory-creation.json` |
| Factory creation block | 12369621 | `eth_getCode` = 0 bytes at 12369620, 24 535 bytes at 12369621 |
| `PoolCreated` topic0 | `0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118` | recomputed from the signature at import time |
| `FeeAmountEnabled` topic0 | `0xc66a3fdf07232cdd185febcc6579d408c241b47ae2f9907d84be655141eeaecc` | same |
| QuoterV2 (cross-check only) | `0x61fFE014bA17989E743c5F6cB21bF9697530B21e` | used as the independent reference, never by the adapter |
| Multicall3 (acquisition only) | `0xcA11bde05977b3631167028862bE2a173976CA11` | `docs/INTERFACES.md` |

Fee tiers enabled on the factory over its whole life (enumerated, not assumed):

| fee (pips) | tickSpacing | enabled at block |
|---:|---:|---:|
| 500 | 10 | 12369621 (constructor) |
| 3000 | 60 | 12369621 (constructor) |
| 10000 | 200 | 12369621 (constructor) |
| 100 | 1 | 13604706 |

No other tier has ever been enabled on mainnet, so `100/500/3000/10000` is a *measured*
result here, not an assumption. The inventory records both the enabled list and the tiers
actually observed in the discovered pools.

## 2. Files

```
src/swaparch/adapters/uniswap_v3/math.py     FullMath, TickMath, SqrtPriceMath, SwapMath,
                                             LiquidityMath, TickBitmap (exact integer ports)
src/swaparch/adapters/uniswap_v3/state.py    UniV3State (core.protocols.PoolState)
src/swaparch/adapters/uniswap_v3/adapter.py  UniswapV3Adapter (core.protocols.SourceAdapter)
src/swaparch/discovery/uniswap_v3.py         PoolCreated / FeeAmountEnabled filters + decoders
data/discovery/1/uniswap_v3.json             inventory (52 pools, 57 coverage entries)
data/adapters-evidence/uniswap_v3/           every raw response this document relies on
tests/test_uniswap_v3_math.py                offline math + saved-swap arithmetic check
tests/test_uniswap_v3_state.py               offline state/adapter, from recorded snapshots
scripts/uniswap_v3_*.py, scripts/castlib.py  one-shot drivers that produced the evidence
```

## 3. ABI surface

The adapter decodes raw hex with `eth_abi`; it never uses a node-side ABI decoder. Every
selector is recomputed from its signature at import time and asserted against the literal.

| call | selector | return types |
|---|---|---|
| `slot0()` | `0x3850c7bd` | `(uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool)` |
| `liquidity()` | `0x1a686502` | `uint128` |
| `fee()` | `0xddca3f43` | `uint24` |
| `tickSpacing()` | `0xd0c93a7c` | `int24` |
| `token0()` | `0x0dfe1681` | `address` |
| `token1()` | `0xd21220a7` | `address` |
| `tickBitmap(int16)` | `0x5339c296` | `uint256` |
| `ticks(int24)` | `0xf30dba93` | `(uint128 liquidityGross, int128 liquidityNet, uint256, uint256, int56, uint160, uint32, bool)` |

Reference (not adapter) calls:

| call | selector |
|---|---|
| `QuoterV2.quoteExactInputSingle((address,address,uint256,uint24,uint160))` | `0xc6a5026a` |
| `Multicall3.aggregate3((address,bool,bytes)[])` | `0x82ad56cb` |

## 4. Read plan and the tick window

Phase 1 (`read_requests`): the six pool statics above — one Multicall batch, no dependencies.

Phase 2 (`dependent_requests`, pass 1): `tickBitmap(int16)` for `2 * word_radius + 1` words
centred on the word holding `slot0().tick`. Default `word_radius = 8` → 17 words. The centre
word cannot be known before `slot0` is read, so these reads are genuinely dependent; putting
the previous block's tick into `PoolRecord.config["tick_hint"]` lets `read_requests` emit the
window in phase 1 instead, collapsing a warm re-run into one batch.

Phase 3 (`dependent_requests`, pass 2): `ticks(int24)` for every initialized tick found in
those words. Pass 3 returns `[]`, which is the acquirer's stop condition.

At the WETH/USDC 0.05% pool this is 6 + 17 + ~1 460 reads per block — 8 Multicall3 batches at
200 subcalls each. The window is part of the snapshot's provenance
(`UniswapV3Adapter.provenance`), because a quote is only trustworthy inside it.

**Coverage is enforced, not assumed.** If the swap walk needs a bitmap word outside
`[word_lo, word_hi]`, or an initialized tick whose `liquidityNet` was not loaded,
`UniV3State` raises `core.protocols.Unsupported("insufficient tick coverage: needed word X,
loaded [a,b]")`. It never treats unloaded liquidity as empty, and it never returns a number
it cannot stand behind.

## 5. Quote semantics

`UniV3State.swap` is a step-for-step port of `UniswapV3Pool.swap` for an unconstrained
exact-input swap:

* price limit `MIN_SQRT_RATIO + 1` (token0 in) / `MAX_SQRT_RATIO - 1` (token1 in), which is
  what `SwapRouter`/`SwapRouter02` substitute for `sqrtPriceLimitX96 = 0`;
* per-step `SwapMath.computeSwapStep`, so the fee is charged **per step**, not once on the
  total, and a step that reaches its target charges
  `ceil(amountIn * fee / (1e6 - fee))` while a partial step absorbs the whole remainder;
* `TickBitmap.nextInitializedTickWithinOneWord` traversal, one word per step;
* `LiquidityMath.addDelta` on every crossed initialized tick, with `liquidityNet` negated
  when `zeroForOne`;
* `state.tick = tickNext - 1` on a `zeroForOne` cross, `getTickAtSqrtRatio` otherwise.

The state is a frozen dataclass whose maps are `MappingProxyType`; `swap()` returns
`(amount_out, new_state)` so shared-depth consumption is explicit to the evaluator.
`capacity_ids()` returns `(record.pool_id,)` — a V3 pool shares depth with nothing else.

`gas_estimate()` returns a **constant, documented 120 000**. It is a flat label for gas-aware
ranking, not a measurement: a real swap costs roughly the pool call plus ~20k per crossed
initialized tick, and the 40 000 WETH quote below crosses 755 of them.

## 6. Verification

### 6a. Offline — saved `Swap` event arithmetic consistency

Source: `evidence/crash-rescue-simulation/saved-data/swaps_crash{1,2,3}_weth_usdc_500.csv`
(11 480 rows, pool `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`, never modified).

Method: take consecutive `Swap` events **in the same block** whose recorded pool liquidity is
identical. This gives a usable pre-state assumption for the later event: the earlier event's
post-swap `sqrtPriceX96` is its pre-swap price, and the unchanged endpoint liquidity is
consistent with no net liquidity change between the events. Equal endpoint liquidity does not
exclude intervening mint/burn events, zero-net initialized-tick crossings, or changes that
cancel; those event details are not present in these CSVs. Feed the later event's positive
(pool-input) amount through each candidate `SwapMath.computeSwapStep` model and compare
amounts **and** the post-swap price.

A `Swap` event does not record the signed `amountSpecified`, the caller's price limit, or the
call shape. The candidates are therefore tested independently; a pair can match more than one:

| matching model | pairs | what it establishes |
|---|---:|---|
| `exact_in` | 3 222 | The unconstrained exact-input arithmetic model reproduces input, output, and terminal price |
| `exact_out` | 899 | The exact-output arithmetic model reproduces input, output, and terminal price |
| `observed_terminal` | 4 200 | Using the observed terminal price as the target reproduces input and output; this does not identify a caller price limit or mode |
| **no matching model** | **0** | — |

The overlap sets are:

| matching models | pairs |
|---|---:|
| `exact_in + observed_terminal` | 3 219 |
| `exact_out + observed_terminal` | 899 |
| `observed_terminal` only | 82 |
| `exact_in` only | 3 |

The script retains `classify()` and its first-match labels for existing checks (`exact_in`,
`exact_out`, `limit`, `other`). Those labels are compatibility buckets, not recovered caller
modes; `limit` means only that the `observed_terminal` candidate matched after the earlier
candidates failed.

Per file: crash1 1 061 pairs (809/229/23/0), crash2 1 935 (1 496/411/28/0), crash3 1 207
(917/259/31/0).

So **4 203 of 4 203** eligible event pairs are arithmetically consistent with at least one
candidate model, to the wei and to the last bit of `sqrtPriceX96`. The 981 pairs that the
unconstrained exact-input candidate does not match are not explained by rounding under that
model, but the data cannot establish that their original calls were not exact-input calls:
an exact-input call with a binding caller price limit can produce the same observed-terminal
match, and the original limit is absent. This is arithmetic consistency evidence, not an
execution replay or a caller-mode reconstruction.

Separately, all 11 480 rows satisfy `getSqrtRatioAtTick(tick) <= sqrtPriceX96`, and 11 479 of
them satisfy the strict `< getSqrtRatioAtTick(tick + 1)`. The single exception is the
documented `Pool.swap` behaviour of storing `tickNext - 1` when a swap stops exactly on a
crossed tick.

Evidence: `data/adapters-evidence/uniswap_v3/swap-event-replay.json`, generated by
`scripts/uniswap_v3_csv_check.py`.
Tests: `tests/test_uniswap_v3_math.py::test_compute_swap_step_reproduces_every_saved_swap_event`
retains the compatibility-label check, while
`tests/test_csv_evidence.py::test_cached_pair_reports_exact_input_and_observed_terminal_overlap`
guards the independent overlap report against a copied CSV pair.

### 6b. Online — QuoterV2 at the same block

State built entirely from `cast`/Multicall3 reads through the adapter's own read plan, then
compared against `QuoterV2.quoteExactInputSingle` (fee 500, `sqrtPriceLimitX96 = 0`) at the
same block. Every raw request/response is in
`data/adapters-evidence/uniswap_v3/quoter-cross-check-<block>-r8.json`.

### Block 25896003 (`0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`)

State: sqrtPriceX96 `1618868919676713855630301716459399`, tick `198508`, liquidity `4408226759632283186`, fee `500`, tickSpacing `10`; bitmap words `[69,85]`, 1461 initialized ticks loaded.

| direction | amount in (raw) | local `quote_exact_in` | QuoterV2 | diff | post-swap sqrtPriceX96 equal |
|---|---:|---:|---:|---:|:--:|
| WETH->USDC | 100 000 000 000 000 000 | 239 396 674 | 239 396 674 | 0 | yes |
| WETH->USDC | 1 000 000 000 000 000 000 | 2 393 942 838 | 2 393 942 838 | 0 | yes |
| WETH->USDC | 10 000 000 000 000 000 000 | 23 937 037 881 | 23 937 037 881 | 0 | yes |
| WETH->USDC | 100 000 000 000 000 000 000 | 239 131 739 028 | 239 131 739 028 | 0 | yes |
| WETH->USDC | 1 000 000 000 000 000 000 000 | 2 367 570 265 712 | 2 367 570 265 712 | 0 | yes |
| USDC->WETH | 100 000 000 | 41 729 847 749 494 718 | 41 729 847 749 494 718 | 0 | yes |
| USDC->WETH | 10 000 000 000 | 4 172 793 387 767 301 752 | 4 172 793 387 767 301 752 | 0 | yes |
| USDC->WETH | 100 000 000 000 | 41 710 542 907 929 939 781 | 41 710 542 907 929 939 781 | 0 | yes |
| USDC->WETH | 1 000 000 000 000 | 415 403 381 026 815 016 834 | 415 403 381 026 815 016 834 | 0 | yes |

### Block 23549991 (`0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623`)

State: sqrtPriceX96 `1367563279517029399426676730054431`, tick `195133`, liquidity `584371440477331545`, fee `500`, tickSpacing `10`; bitmap words `[68,84]`, 1397 initialized ticks loaded.

| direction | amount in (raw) | local `quote_exact_in` | QuoterV2 | diff | post-swap sqrtPriceX96 equal |
|---|---:|---:|---:|---:|:--:|
| WETH->USDC | 100 000 000 000 000 000 | 335 461 589 | 335 461 589 | 0 | yes |
| WETH->USDC | 1 000 000 000 000 000 000 | 3 354 316 759 | 3 354 316 759 | 0 | yes |
| WETH->USDC | 10 000 000 000 000 000 000 | 33 513 282 505 | 33 513 282 505 | 0 | yes |
| WETH->USDC | 100 000 000 000 000 000 000 | 332 132 193 469 | 332 132 193 469 | 0 | yes |
| WETH->USDC | 1 000 000 000 000 000 000 000 | 3 002 144 531 446 | 3 002 144 531 446 | 0 | yes |
| USDC->WETH | 100 000 000 | 29 779 486 917 787 405 | 29 779 486 917 787 405 | 0 | yes |
| USDC->WETH | 10 000 000 000 | 2 977 078 551 486 571 024 | 2 977 078 551 486 571 024 | 0 | yes |
| USDC->WETH | 100 000 000 000 | 29 691 913 941 796 861 054 | 29 691 913 941 796 861 054 | 0 | yes |
| USDC->WETH | 1 000 000 000 000 | 289 232 030 318 146 863 481 | 289 232 030 318 146 863 481 | 0 | yes |

**All 18 sizes agree with QuoterV2 to the wei, and the post-swap `sqrtPriceX96` matches as
well.** No size needed to be excluded, and no `Unsupported` was raised inside the requested
ladder. The `sqrtPriceX96` values in the loaded state also match the pre-existing
`evidence/crash-rescue-simulation/standing-prices.json` snapshots exactly, so the state was
read at the block those snapshots describe.

#### Tick-window sensitivity

The default `word_radius = 8` is far wider than the requested ladder needs. Rebuilding the
state from the same recorded responses with narrower windows shows what each size actually
requires:

### Minimal tick-bitmap window per size (offline, from the recorded radius-8 state)

| block | direction | amount in (raw) | minimal `word_radius` | matches QuoterV2 |
|---|---|---:|---:|:--:|
| 25896003 | WETH->USDC | 100 000 000 000 000 000 | 0 | yes |
| 25896003 | WETH->USDC | 1 000 000 000 000 000 000 | 0 | yes |
| 25896003 | WETH->USDC | 10 000 000 000 000 000 000 | 0 | yes |
| 25896003 | WETH->USDC | 100 000 000 000 000 000 000 | 0 | yes |
| 25896003 | WETH->USDC | 1 000 000 000 000 000 000 000 | 0 | yes |
| 25896003 | USDC->WETH | 100 000 000 | 0 | yes |
| 25896003 | USDC->WETH | 10 000 000 000 | 0 | yes |
| 25896003 | USDC->WETH | 100 000 000 000 | 0 | yes |
| 25896003 | USDC->WETH | 1 000 000 000 000 | 0 | yes |
| 23549991 | WETH->USDC | 100 000 000 000 000 000 | 0 | yes |
| 23549991 | WETH->USDC | 1 000 000 000 000 000 000 | 0 | yes |
| 23549991 | WETH->USDC | 10 000 000 000 000 000 000 | 0 | yes |
| 23549991 | WETH->USDC | 100 000 000 000 000 000 000 | 0 | yes |
| 23549991 | WETH->USDC | 1 000 000 000 000 000 000 000 | 1 | yes |
| 23549991 | USDC->WETH | 100 000 000 | 0 | yes |
| 23549991 | USDC->WETH | 10 000 000 000 | 0 | yes |
| 23549991 | USDC->WETH | 100 000 000 000 | 0 | yes |
| 23549991 | USDC->WETH | 1 000 000 000 000 | 1 | yes |
So the whole requested ladder is priced inside the *single* bitmap word containing the
current tick, except the two 1000-WETH-scale trades at the crash block, which need one
neighbouring word.

#### A size that does exceed the window

40 000 WETH at block 25896003 does leave the default window:

```
radius 8  words [69, 85] -> Unsupported: insufficient tick coverage: needed word 86, loaded [69,85]
radius 16 words [61, 93] -> 67 632 886 601 193 USDC
QuoterV2                 -> 67 632 886 601 193 USDC   (755 initialized ticks crossed)
```

The widened `word_radius = 16` window fixes it and still agrees to the wei, including the
post-swap price. Evidence:
`data/adapters-evidence/uniswap_v3/window-widening-40000weth-25896003.json`; test:
`tests/test_uniswap_v3_state.py::test_widening_the_window_prices_a_trade_the_default_cannot`.

The window is not unlimited: ~37 950 WETH is the largest WETH→USDC trade priceable at
radius 8 and ~41 010 WETH at radius 16 (block 25896003). Past that the walk runs out of
loaded words rather than out of liquidity, and 200 000 WETH is still `Unsupported` at
radius 16 (`needed word 94, loaded [61,93]`). That is a genuine capacity/coverage boundary
and is reported as one, never as a number.

### 6c. Comparison with the settled forge-fork rows

### Settled forge-fork rows from `results-funded.json`

| block | position size | buffer bps | weth_in (raw) | settled usdc_out | local quote | diff |
|---|---:|---:|---:|---:|---:|---:|
| 23549991 | 10 WETH | 1000 | 5 543 859 353 303 727 603 | 18 587 492 092 | 18 587 492 092 | 0 |
| 23549991 | 100 WETH | 1000 | 55 438 593 538 821 928 724 | 184 958 665 260 | 184 958 665 260 | 0 |
| 23549991 | 1000 WETH | 1000 | 554 385 935 395 932 157 505 | 1 754 463 451 103 | 1 754 463 451 103 | 0 |
| 25896003 | 10 WETH | 100 | 3 893 277 410 162 725 369 | 9 319 984 358 | 9 319 984 358 | 0 |
| 25896003 | 10 WETH | 1000 | 5 543 859 353 128 616 334 | 13 271 013 267 | 13 271 013 267 | 0 |
| 25896003 | 100 WETH | 100 | 38 932 774 101 206 690 486 | 93 163 637 737 | 93 163 637 737 | 0 |
| 25896003 | 100 WETH | 1000 | 55 438 593 538 225 456 204 | 132 636 740 482 | 132 636 740 482 | 0 |
| 25896003 | 1000 WETH | 100 | 389 327 741 020 478 168 940 | 928 026 295 648 | 928 026 295 648 | 0 |
| 25896003 | 1000 WETH | 1000 | 554 385 935 396 133 147 773 | 1 319 051 850 074 | 1 319 051 850 074 | 0 |

**Every settled row reproduces to the last USDC unit, diff 0 on all nine.**

What fork state the funded run used — answered from the data, not assumed:

* `results-funded.json` records `sqrt_price_before` per row. At block 25896003 it is
  `1618868919676713855630301716459399` and at 23549991 it is
  `1367563279517029399426676730054431`. Those are byte-identical to `slot0()` read with
  `eth_call` **at block N** (both in `standing-prices.json` and in this adapter's own reads).
* So the fork state is the **post-state of block N**, not N+1: Foundry's
  `createSelectFork(url, N)` pins state queries to block `N`, which in an archive node means
  after every transaction of block N has executed. It then takes the EVM block environment
  from block N's own header, so `block.number == N` inside the test.
* Consequently a quote built from `eth_call ... --block N` is comparing against exactly the
  state the fork test swapped into. Nothing had to be shifted or tuned to make the nine rows
  line up.

Was the path this single pool? Yes:

* `FUNDED-RUN.md`'s proof boundary states the run proves nothing "about a single venue beyond
  the one UniswapV3 0.05% WETH/USDC pool (`0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`) — no
  1inch, no multi-pool routing, no alternate fee tiers".
* The swap adapter in the copied snapshot,
  `source-snapshot/contract-work/contracts/src/swap/UniswapV3Router.sol`, forwards a packed
  keeper-supplied path to `SwapRouter02.exactInput` and only validates that the path starts
  with `fromToken` and ends with `toToken`. A one-hop path — 43 bytes, here
  `c02aaa…c756cc2 | 0001f4 | a0b869…606eb48`, i.e. WETH, fee 500, USDC — makes `exactInput`
  degenerate to a single `pool.swap` with `sqrtPriceLimitX96 = 0`, which
  `SwapRouter02.exactInputInternal` replaces with `MIN_SQRT_RATIO + 1` /
  `MAX_SQRT_RATIO - 1` — precisely the limit this adapter uses.
* The arithmetic confirms it independently: a multi-hop or different-fee-tier route would not
  reproduce all nine outputs to the unit against the 0.05% pool's own state.

**Residual difference: none.** There is nothing to explain away, and nothing was tuned to fit
— the local quote is produced by the same code path that the QuoterV2 table above validates,
from state read independently of the funded run.

The other settled rows in `results-funded.json` (blocks 23550060, 23728292, 24356381) were not
compared here because this phase only acquired tick state for the two blocks in scope; the
same check will extend to them when those blocks are snapshotted.

## 7. Discovery

`data/discovery/1/uniswap_v3.json` (schema 1). Coverage: one `eth_getLogs` per **ordered**
token pair over the eight tokens (six study endpoints WETH/USDC/USDT/DAI/wstETH/sUSDe plus
stETH and USDe as connectors), `topic0 = PoolCreated`, `topic1 = token0`, `topic2 = token1`,
`topic3` (fee) deliberately unset, from block 12369621 to block 25896003
(`0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`), plus one unfiltered
`FeeAmountEnabled` scan over the same range. 57 coverage entries, 52 pools.

The factory sorts the pair, so only the numerically sorted ordering can ever match. All 28
non-canonical ordered filters were still run and all 28 returned **0 logs**, which turns that
invariant from an assumption into a recorded observation.

Pools per pair: DAI/USDC 4, USDC/WETH 4, DAI/WETH 4, USDC/USDT 4, DAI/USDT 4, WETH/USDT 4,
stETH/WETH 4, wstETH/WETH 3, USDe/USDT 3, USDe/sUSDe 3, sUSDe/USDT 3, wstETH/USDC 2,
USDe/USDC 2, wstETH/sUSDe 2, sUSDe/USDC 2, USDC/stETH 1, wstETH/USDT 1, USDe/DAI 1,
USDe/wstETH 1.

Token symbols and decimals were resolved on chain at block 25896003 through one Multicall3
batch and cached in the inventory
(`data/adapters-evidence/uniswap_v3/token-metadata.json`): DAI 18, USDC 6, WETH 18, USDT 6,
wstETH 18, stETH 18, USDe 18, sUSDe 18.

A pair-filtered scan covers exactly the pairs it names. Adding a token later requires
backfilling the new pairs from block 12369621; that is what the `coverage` list is for.

## 8. Limitations

1. **Tick coverage is finite.** Quotes are only valid inside the loaded bitmap window. The
   default `word_radius = 8` covers every size in the requested ladder at both blocks, but a
   ~38 000 WETH trade already leaves it. `Unsupported` is raised rather than a number.
2. **Admission is per pool and block hash.** Only records with successful recorded
   quote checks at the requested hash enter routing. The calm-block checks cover 1 and
   100 input-token units in both directions; 23 pools pass all four. Other sizes remain
   subject to local tick coverage and capacity checks. Discovery alone grants no support.
3. **Exact input only.** `PoolState` exposes exact-input quoting; the exact-output branch of
   `computeSwapStep` is ported and exercised by the CSV replay but not surfaced on the state.
4. **Gas is a constant label**, not a measurement (§5).
5. **Rebasing / fee-on-transfer tokens.** All five stETH pools remain excluded from
   the admitted universe pending actual transfer-semantics and usable-liquidity checks.
   A Quoter response alone cannot establish arbitrary token-transfer behavior.
6. **Protocol fee is not modelled.** It does not affect `amount0`/`amount1` for a swap, only
   the LPs' share, so it is irrelevant to quoting — but `feeGrowthGlobal` and
   `protocolFees` are deliberately not tracked in the state.
7. **Block-end state only.** Every quote is against the post-state of block N. It does not
   reconstruct an intra-block price excursion.

## 9. How to reproduce

```
uv run pytest -q tests/test_uniswap_v3_math.py tests/test_uniswap_v3_state.py   # offline
uv run python scripts/uniswap_v3_csv_check.py                                   # offline
uv run python scripts/uniswap_v3_window_analysis.py                             # offline
uv run python scripts/uniswap_v3_online_check.py 25896003 23549991              # ~25 RPC calls
uv run python scripts/uniswap_v3_discovery_run.py                               # discovery only
uv run python scripts/validate_v3_universe.py 25896003                          # per-pool admission
```

The RPC URL is resolved by `scripts/castlib.py` per `docs/INTERFACES.md` and is redacted from
every message and artifact.
