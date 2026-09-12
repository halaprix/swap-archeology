# Uniswap V3 adapter — phase 1 report

Date: 2026-09-07.
Scope: Uniswap V3 on Ethereum mainnet — discovery, read plan, exact swap math, verification.
Research-only: no transactions, no commits, no git repo created, `evidence/` untouched.

**Verdict: `supported`.** Local quotes agree with `QuoterV2` to the wei on all 18 requested
sizes at both blocks (post-swap `sqrtPriceX96` matches too), reproduce all nine settled swaps
from a funded fork run exactly, and reproduce all 4 203 replayable historical
`Swap` events bit-exactly.

## 1. Files changed

Created (all inside my ownership boundary):

```
src/swaparch/adapters/uniswap_v3/__init__.py
src/swaparch/adapters/uniswap_v3/math.py       FullMath, TickMath, SqrtPriceMath, SwapMath,
                                               LiquidityMath, BitMath, TickBitmap
src/swaparch/adapters/uniswap_v3/state.py      UniV3State  (core.protocols.PoolState)
src/swaparch/adapters/uniswap_v3/adapter.py    UniswapV3Adapter (core.protocols.SourceAdapter)
src/swaparch/discovery/uniswap_v3.py           PoolCreated/FeeAmountEnabled filters + decoders
tests/test_uniswap_v3_math.py                  16 offline tests
tests/test_uniswap_v3_state.py                 19 offline tests
docs/adapters/uniswap_v3.md                    full semantics + verification write-up
docs/reports/uniswap-v3-phase1.md              this file
data/discovery/1/uniswap_v3.json               inventory: 52 pools, 57 coverage entries
data/adapters-evidence/uniswap_v3/*.json       13 raw-evidence files (see below)
scripts/castlib.py                             bounded `cast`/Multicall3 read helper
scripts/uniswap_v3_csv_check.py                offline Swap-event replay driver
scripts/uniswap_v3_online_check.py             QuoterV2 cross-check driver
scripts/uniswap_v3_window_analysis.py          offline tick-window sensitivity
scripts/uniswap_v3_discovery_run.py            discovery runner
```

Not touched: `src/swaparch/core/`, `docs/INTERFACES.md`, `evidence/`, `src/swaparch/rpc/`,
`src/swaparch/snapshot/`, `src/swaparch/evaluator/`, other adapters, `docs/SOURCE_STATUS.md`.
`scripts/castlib.py` is a new file in a shared directory; it does not modify the infra
worker's `scripts/inv_*.py`.

Evidence files under `data/adapters-evidence/uniswap_v3/`:
`factory-creation.json`, `token-metadata.json`, `poolcreated-raw-logs.json`,
`swap-event-replay.json`, `quoter-cross-check-25896003-r8.json`,
`quoter-cross-check-23549991-r8.json`, `snapshot-25896003-r8.json`,
`snapshot-23549991-r8.json`, `snapshot-25896003-r16.json`, `window-analysis.json`,
`window-widening-40000weth-25896003.json`, `oversized-window-widening.json`,
`funded-run-comparison.json`.

## 2. Commands and output (trimmed)

Acceptance:

```
$ uv run pytest -q tests/test_uniswap_v3_math.py tests/test_uniswap_v3_state.py
...................................                                      [100%]
35 passed in 1.88s

$ uv run pytest -q                       # whole suite, including other workers' tests
44 passed, 1 skipped, 1 warning in 1.44s

$ uv run ruff check --select E,F src/swaparch/adapters/uniswap_v3 \
      src/swaparch/discovery/uniswap_v3.py tests/test_uniswap_v3_*.py \
      scripts/castlib.py scripts/uniswap_v3_*.py
All checks passed!
```

Offline replay of the saved `Swap` events:

```
$ uv run python scripts/uniswap_v3_csv_check.py
{ "total": {"pairs": 4203, "exact_in": 3222, "exact_out": 899, "limit": 82, "other": 0},
  "per_file": {
    "swaps_crash1_weth_usdc_500.csv": {"rows":3133,"pairs":1061,"exact_in":809,"exact_out":229,"limit":23,"other":0},
    "swaps_crash2_weth_usdc_500.csv": {"rows":5010,"pairs":1935,"exact_in":1496,"exact_out":411,"limit":28,"other":0},
    "swaps_crash3_weth_usdc_500.csv": {"rows":3337,"pairs":1207,"exact_in":917,"exact_out":259,"limit":31,"other":0}}}
```

Online cross-check (RPC URL redacted by the helper itself):

```
$ uv run python scripts/uniswap_v3_online_check.py 25896003 23549991
== block 25896003 (word radius 8) ==
   words [69,85] ticks 1461
   WETH->USDC in=100000000000000000     local=239396674   quoter=239396674   diff=0 sqrt_match=True
   ... 8 more rows, all diff=0 ...
== block 23549991 (word radius 8) ==
   words [68,84] ticks 1397
   ... 9 rows, all diff=0 ...
total cast/eth_call invocations this run: 25
```

Discovery:

```
$ uv run python scripts/uniswap_v3_discovery_run.py
pools: 52  fee tiers in pools: [100, 500, 3000, 10000]
fee tiers enabled on the factory: [(500, 10, 12369621), (3000, 60, 12369621),
                                   (10000, 200, 12369621), (100, 1, 13604706)]
coverage entries: 57   cast/eth_* invocations: 59
written: data/discovery/1/uniswap_v3.json
```

Tick-window sensitivity and the coverage boundary:

```
$ uv run python scripts/uniswap_v3_window_analysis.py
== block 25896003 ==   every requested size: minimal_radius=0, out_matches_quoter=True
   oversized 200000 WETH at radius 8 -> insufficient tick coverage: needed word 86, loaded [69,85]
== block 23549991 ==   1000 WETH and 1e6 USDC need radius 1; the rest radius 0
   oversized 200000 WETH at radius 8 -> insufficient tick coverage: needed word 85, loaded [68,84]
```

Inventory schema check:

```
missing top-level keys: none
coverage entries: 57 pools: 52
to_block: {25896003}  to_block_hash: {0xf2c9645a…3dba5a5}  from_block: {12369621}
SCHEMA OK
```

## 3. Verification table

Local `UniV3State.quote_exact_in` vs `QuoterV2.quoteExactInputSingle` (fee 500,
`sqrtPriceLimitX96 = 0`), same block, `word_radius = 8`.

### Block 25896003 (calm control, hash `0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`)

| size | amount in (raw) | local | QuoterV2 | diff |
|---|---:|---:|---:|---:|
| 0.1 WETH | 100000000000000000 | 239396674 | 239396674 | 0 |
| 1 WETH | 1000000000000000000 | 2393942838 | 2393942838 | 0 |
| 10 WETH | 10000000000000000000 | 23937037881 | 23937037881 | 0 |
| 100 WETH | 100000000000000000000 | 239131739028 | 239131739028 | 0 |
| 1000 WETH | 1000000000000000000000 | 2367570265712 | 2367570265712 | 0 |
| 100 USDC | 100000000 | 41729847749494718 | 41729847749494718 | 0 |
| 10 000 USDC | 10000000000 | 4172793387767301752 | 4172793387767301752 | 0 |
| 100 000 USDC | 100000000000 | 41710542907929939781 | 41710542907929939781 | 0 |
| 1 000 000 USDC | 1000000000000 | 415403381026815016834 | 415403381026815016834 | 0 |

### Block 23549991 (crash, hash `0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623`)

| size | amount in (raw) | local | QuoterV2 | diff |
|---|---:|---:|---:|---:|
| 0.1 WETH | 100000000000000000 | 335461589 | 335461589 | 0 |
| 1 WETH | 1000000000000000000 | 3354316759 | 3354316759 | 0 |
| 10 WETH | 10000000000000000000 | 33513282505 | 33513282505 | 0 |
| 100 WETH | 100000000000000000000 | 332132193469 | 332132193469 | 0 |
| 1000 WETH | 1000000000000000000000 | 3002144531446 | 3002144531446 | 0 |
| 100 USDC | 100000000 | 29779486917787405 | 29779486917787405 | 0 |
| 10 000 USDC | 10000000000 | 2977078551486571024 | 2977078551486571024 | 0 |
| 100 000 USDC | 100000000000 | 29691913941796861054 | 29691913941796861054 | 0 |
| 1 000 000 USDC | 1000000000000 | 289232030318146863481 | 289232030318146863481 | 0 |

No size in the requested ladder raised `Unsupported`. The post-swap `sqrtPriceX96` matched
QuoterV2's `sqrtPriceX96After` on all 18.

### Coverage boundary, shown deliberately

| size | radius 8 | radius 16 | QuoterV2 |
|---|---|---:|---:|
| 40 000 WETH @ 25896003 | `Unsupported: insufficient tick coverage: needed word 86, loaded [69,85]` | 67632886601193 | 67632886601193 (755 ticks crossed) |
| 200 000 WETH @ 25896003 | `needed word 86, loaded [69,85]` | `needed word 94, loaded [61,93]` | 67836232189444 |

Largest priceable WETH→USDC trade at 25896003: ~37 950 WETH at radius 8, ~41 010 WETH at
radius 16. Past that the walk runs out of *loaded words*, and that is reported as
`Unsupported`, never as a number.

### Independent settlement comparison (`results-funded.json`)

| block | weth_in (raw) | settled usdc_out | local quote | diff |
|---|---:|---:|---:|---:|
| 23549991 | 5543859353303727603 | 18587492092 | 18587492092 | 0 |
| 23549991 | 55438593538821928724 | 184958665260 | 184958665260 | 0 |
| 23549991 | 554385935395932157505 | 1754463451103 | 1754463451103 | 0 |
| 25896003 | 3893277410162725369 | 9319984358 | 9319984358 | 0 |
| 25896003 | 5543859353128616334 | 13271013267 | 13271013267 | 0 |
| 25896003 | 38932774101206690486 | 93163637737 | 93163637737 | 0 |
| 25896003 | 55438593538225456204 | 132636740482 | 132636740482 | 0 |
| 25896003 | 389327741020478168940 | 928026295648 | 928026295648 | 0 |
| 25896003 | 554385935396133147773 | 1319051850074 | 1319051850074 | 0 |

**Fork state**: post-state of block N, not N+1. Established from the data, not assumed —
`results-funded.json`'s `sqrt_price_before` is byte-identical to `slot0()` read by `eth_call`
at block N (and to `standing-prices.json`). Foundry's `createSelectFork(url, N)` pins state
reads to block `N` while taking the block environment from block N's header, so
`block.number == N` inside the test and the storage is post-block-N.

**Router path**: the single 0.05% WETH/USDC pool `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`.
`FUNDED-RUN.md`'s proof boundary says so explicitly ("no 1inch, no multi-pool routing, no
alternate fee tiers"); `UniswapV3Router.sol` forwards a packed path to
`SwapRouter02.exactInput`, and a one-hop path degenerates to a single `pool.swap` with
`sqrtPriceLimitX96 = 0`, which the router replaces with `MIN_SQRT_RATIO + 1` /
`MAX_SQRT_RATIO - 1` — the same limit this adapter uses. The arithmetic confirms it: a
different tier or a multi-hop route could not reproduce all nine outputs to the unit.

**Residual difference: none, on any of the nine rows.** Nothing was tuned to fit.

### Offline replay of historical `Swap` events

4 203 replayable consecutive same-block pairs across 11 480 saved events:
3 222 reproduce as exact-input calls, 899 as exact-output calls, 82 as
`sqrtPriceLimitX96`-capped calls — **0 unexplained**. Amounts *and* post-swap
`sqrtPriceX96` match bit-for-bit in every case. The 981 pairs a naive exact-input replay
would score as mismatches are not rounding error: they were not exact-input calls, and each
is exact under its own call shape. Full analysis in `docs/adapters/uniswap_v3.md` §6a.

## 4. RPC call count

**162 `cast` invocations total** (budget: 400). Every one was an `eth_call`, `eth_getCode`,
`eth_getLogs` or `eth_getBlockByNumber` against the archive node; reads were batched through
Multicall3 wherever possible (~3 000 individual pool reads compressed into 25 `eth_call`s for
the two cross-check blocks).

| purpose | calls |
|---|---:|
| connectivity probe (`chain-id`, `block-number`) | 2 |
| Multicall3 feasibility probe (statics + bitmap, both blocks) | 4 |
| QuoterV2 cross-check run, both blocks (`uniswap_v3_online_check.py`) | 25 |
| oversized-trade exploration at radius 12/16, both blocks | 48 |
| radius-16 snapshot fixture for block 25896003 | 11 |
| QuoterV2 quote for the 40 000 WETH widening demo | 1 |
| factory topic/code probe | 4 |
| factory-creation evidence (`eth_getCode` × 4, `eth_getLogs` × 1) | 5 |
| single `PoolCreated` query smoke test | 1 |
| discovery run: 56 pair filters + FeeAmountEnabled + token metadata + block hash | 59 |
| block-hash repair after a `cast block` argument-order bug | 2 |
| **total** | **162** |

The RPC URL was never printed, logged or written into any artifact; `scripts/castlib.py`
redacts it from every error message and all shell output was piped through
`sed -E 's#https?://[^ "]+#<rpc-url>#g'`.

## 5. Limitations

1. **Tick window.** Quotes are valid only inside the loaded bitmap window. Default
   `word_radius = 8` (17 words) covers every size in the requested ladder at both blocks —
   in fact all but two sizes need only the single current word — but ~38 000 WETH already
   leaves it. Outside the window the adapter raises `Unsupported`; it never extrapolates.
2. **One pool cross-checked.** `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640` only. The other
   51 discovered pools inherit trust from the factory deploying one immutable
   `UniswapV3Pool` bytecode. Recorded in the inventory's `unresolved` list.
3. **Two blocks checked.** 25896003 and 23549991. The other three pins (23550060, 23728292,
   24356381) have settled rows in `results-funded.json` that are *not* yet compared, because
   tick state was not acquired for them in this phase.
4. **Fee tiers.** Enumerated from `FeeAmountEnabled` over the factory's whole life:
   500/3000/10000 from the constructor, 100 from block 13604706. No other tier has ever
   existed on mainnet — a measured result, not an assumption. All four appear in the
   discovered pools.
5. **Exact input only** on `PoolState`; the exact-output branch of `computeSwapStep` is
   ported and exercised by the replay but not exposed.
6. **Gas is a constant 120 000**, explicitly labelled as an estimate, not a measurement. A
   real swap crossing 755 ticks costs far more.
7. **Rebasing/fee-on-transfer tokens** (stETH pools) are discovered and priced by the same
   math but were not depth-verified; flagged in the inventory.
8. **Protocol fee / fee growth are not tracked** in the state. They do not affect a swap's
   `amount0`/`amount1`, only the LP/protocol split.
9. **Discovery coverage is pair-filtered.** It covers exactly the 28 unordered pairs of the
   eight listed tokens. A ninth token needs a backfill from block 12369621.
10. **Block-end state only.** No intra-block price excursion is reconstructed.

## 6. Proposed interface changes (not made — `core/` is lead-owned)

1. **`SourceAdapter` needs a provenance hook.** The tick window a snapshot was acquired with
   determines which trade sizes the resulting state can price, so it belongs in the snapshot
   record, not only in the adapter object. I added
   `UniswapV3Adapter.provenance(pool, snapshot) -> Mapping` as a family-local method; I
   suggest promoting an optional `provenance(...)` to the `SourceAdapter` protocol so the
   acquirer can persist it uniformly.

2. **`PoolState` should expose a capacity/validity bound.** Right now the only way to learn
   that a size is unpriceable is to call `quote_exact_in` and catch `Unsupported`. A solver
   doing a size search will do that a lot. Something like
   `max_amount_in(token_in, token_out) -> int | None` (best-effort, `None` when unbounded)
   would let a solver bracket its search cheaply. For V3 this is directly computable from the
   loaded window.

3. **`Unsupported` would benefit from structured fields.** A `reason_code` (e.g.
   `"insufficient_coverage"`, `"wrong_pair"`, `"price_limit_exhausted"`) plus a small dict
   would let the report aggregate failures without string matching. The current
   message-only contract works but forces tests to assert on prose.

4. **`Snapshot.get`/`has` key semantics should be stated explicitly.** I keyed my fake
   snapshot on `(spec.to, spec.data)` and ignored `spec.tag`, matching the documented cache
   key. Worth stating in `protocols.py` that `tag` is never part of lookup identity, since an
   adapter that reconstructs a spec with a different tag must still find its result.

5. **Exact-output quoting.** `PoolState` has no exact-output entry point. Several venues
   (V3 included) implement it natively and a router benchmark may want it. Not needed for
   phase 2; flagging it before the interface calcifies.

None of these block phase 2. The adapter works against the interfaces exactly as they stand.
