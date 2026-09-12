# Historical WBTC Pool Inventory and Collection Overlay

**Date:** 2026-09-09  
**Status:** Complete local inventory, collection overlay, differential QuoterV2 qualification, two-leg routing analysis, and integration proposal delivered. Research-only; no live transactions or commits.

---

## 1. Executive Summary

- Added historical WBTC pool inventory and collection overlay reusing the **existing Uniswap V3 adapter** (`UniswapV3Adapter` and `UniV3State`); no new pricing adapter was needed or created.
- **12 Historical Pools Snapshotted; Only Active 6 Quote-Qualified on Selected Routes:** All 12 factory-verified pools across WETH/WBTC, WBTC/USDC, and WBTC/USDT are documented with exact fee tiers (100, 500, 3000, 10000), corresponding tick spacings (1, 10, 60, 200), on-chain creation blocks (12,369,821 to 20,629,019), creation transaction hashes, and log indices. Full state snapshots are collected and bitmap-validated for all 12 pools. However, **only the 6 active routing pools** (500 and 3000 fee tiers for WETH/WBTC, WBTC/USDC, and WBTC/USDT) were differential-quoter checked against on-chain QuoterV2 across selected two-leg routes (`WETH -> WBTC -> USDC/USDT`); the other 6 pools (100 and 10000 fee tiers) have near-zero active liquidity and are **not quote-qualified** (avoiding any overclaim that all 12 are fully qualified; qualification applies to selected routes only).
- **Strict Canonical Token Ordering:** In Uniswap V3, tokens are numerically ordered by address (`int(token0, 16) < int(token1, 16)`). Because WBTC (`0x2260fac5e5542a773aa44fbcfedf7c193bc2c599`) has an address strictly lower than USDC (`0xa0b8...`), WETH (`0xc02a...`), and USDT (`0xdac1...`), **WBTC is strictly token0 in all 12 pools**.
- **Qualification Pins State Acquired:** Complete tick and bitmap snapshots acquired and persisted for the three qualification pins:
  - **Start pin:** block `23549939` (`0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12`)
  - **Stress pin:** block `23550094` (`0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d`)
  - **End pin:** block `23550192` (`0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c`) *(Corrected end block hash from collection manifest `outputs/source-expansion/wbtc/collection_manifest.json`)*
- **QuoterV2 Differential Parity on Active Routes (0 Wei Difference):** Verified against `QuoterV2` (`0x61ffe014ba17989e743c5f6cb21bf9697530b21e`). Offline evaluation independently checks raw decoded integers (`local_out == quoter_out` and `local_sqrt_after == quoter_sqrt_after`), not merely boolean flags, matching wei-for-wei across all tested sizes (1, 10, 100 WETH) and both hops on the 6 active pools.
- **Two-Leg Input Consumption Verified:** Two-leg routes (WETH -> WBTC -> USDC/USDT) consume 100% of input (`residual_in == 0`, `spent == requested`) with zero token leakage across the intermediate WBTC hop.
- **Key Economic Finding:** During the crash stress block `23550094`, routing through WBTC (`WETH -> WBTC(500) -> USDC(500)`) produced **3,598.19 USDC/WETH** for 1 WETH and **3,581.65 USDC/WETH** for 10 WETH, outperforming the direct Uniswap V3 WETH/USDC 0.05% pool (**3,580.05 USDC/WETH** and **3,577.47 USDC/WETH** respectively) by **+18.14 USDC/WETH (+50.7 bps)** for 1 WETH and **+41.79 USDC (+11.7 bps)** for 10 WETH.
- **Evidence Provenance & Concurrency (Live vs Cached):** Initial snapshot acquisition and QuoterV2 calls were executed live against archive RPC serialized under `flock /tmp/swaparch-source-rpc.lock`, persisting raw calls in `outputs/source-expansion/wbtc/snapshots/`, `records/`, and `quoter_checks.json`. All downstream unit tests, evaluations, and solver runs execute 100% offline against this cached evidence without any live RPC requests.

---

## 2. Changed and Owned Files

### Scripts Owned
- `scripts/wbtc_inventory.py`: Defines canonical tokens, 12 pool metadata entries, `PoolRecord` construction, activation evidence export, and inventory overlay export.
- `scripts/wbtc_collect.py`: Rerunnable snapshot collector for the 12 WBTC pools with `word_radius=8`, writing `SnapshotStore` calls and `records/<block_hash>.json` files compatible with `collection_supplement.py`.
- `scripts/wbtc_quoter_check.py`: Differential verification script querying `QuoterV2.quoteExactInputSingle` on mainnet and comparing against local state transitions.
- `scripts/wbtc_route_eval.py`: Evaluator for two-leg routes across 1, 10, and 100 WETH trade sizes, checking full input consumption, price impact, and comparisons with direct pools.
- `scripts/wbtc_pipeline.py`: Master orchestrator running the complete pipeline live or offline.

### Tests Owned
- `tests/test_wbtc_inventory.py`: Tests canonical tokens, 12 pool records, canonical ordering (`token0 < token1`), tick spacings, and established activation at October blocks (5 passed).
- `tests/test_wbtc_collection.py`: Tests offline loading of snapshots, `UniswapV3Adapter.load_state()`, non-zero liquidity, valid bitmap/ticks, and supplement record structure across all three qualification pins (4 passed).
- `tests/test_wbtc_routes.py`: Tests QuoterV2 report parity, full input consumption across 1, 10, 100 WETH, intermediate token conservation, and monotonic slippage (5 passed).

### Artifacts and Outputs
- `outputs/source-expansion/wbtc/wbtc_inventory.json`: Discoverable inventory overlay conforming to the `data/discovery/1/` schema.
- `outputs/source-expansion/wbtc/activation/activation_evidence.json`: Pinned activation evidence linking factory queries, code verification, and creation log provenance.
- `outputs/source-expansion/wbtc/collection_manifest.json`: Manifest recording block numbers, hashes, timestamps, specs counts, and pool state summaries.
- `outputs/source-expansion/wbtc/quoter_checks.json`: Full differential check report with raw return data, decoded values, and exact parity flags.
- `outputs/source-expansion/wbtc/two_leg_eval.json`: Two-leg route analysis and input consumption audit.
- `outputs/source-expansion/wbtc/pipeline_summary.json`: High-level execution summary.
- `outputs/source-expansion/wbtc/snapshots/1/<block_hash>/`: Raw pinned Multicall3 call results and headers.
- `outputs/source-expansion/wbtc/records/<block_hash>.json`: Supplement record documents for all 3 pins.
- `docs/reports/wbtc-connectors.md`: This comprehensive documentation report.

---

## 3. The 12 Historical WBTC Pools Inventory

Canonical tokens:
- **WBTC:** `0x2260fac5e5542a773aa44fbcfedf7c193bc2c599`, 8 decimals
- **WETH:** `0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2`, 18 decimals
- **USDC:** `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48`, 6 decimals
- **USDT:** `0xdac17f958d2ee523a2206206994597c13d831ec7`, 6 decimals

Factory: `0x1f98431c8ad98523631ae4a59f267346ea31f984` (Uniswap V3 Factory).

| Pair | Fee | Tick Spacing | Pool Address | Token0 | Token1 | Creation Block | Creation Tx | Active Liquidity (at 23549939) |
|---|---:|---:|---|---|---|---:|---|---:|
| WETH/WBTC | 100 (1 bp) | 1 | `0xe6ff8b9a37b0fab776134636d9981aa778c4e718` | WBTC | WETH | 13,660,296 | `0x40731e...` | 904,032,011,251 |
| WETH/WBTC | 500 (5 bps) | 10 | `0x4585fe77225b41b697c938b018e2ac67ac5a20c0` | WBTC | WETH | 12,376,387 | `0xcb2fcb...` | 9,367,300,130,532,874 |
| WETH/WBTC | 3000 (30 bps) | 60 | `0xcbcdf9626bc03e24f779434178a73a0b4bad62ed` | WBTC | WETH | 12,369,821 | `0xf87d91...` | 22,529,789,263,732,431 |
| WETH/WBTC | 10000 (100 bps) | 200 | `0x6ab3bba2f41e7eaa262fa5a1a9b3932fa161526f` | WBTC | WETH | 12,376,536 | `0x248154...` | 60,498,891,785,565 |
| WBTC/USDC | 100 (1 bp) | 1 | `0x026babd2ae9379525030fc2574e39bc156c10583` | WBTC | USDC | 15,531,569 | `0x51cf4f...` | 453,935 |
| WBTC/USDC | 500 (5 bps) | 10 | `0x9a772018fbd77fcd2d25657e5c547baff3fd7d16` | WBTC | USDC | 12,561,607 | `0xd0cf4b...` | 445,757,350,685 |
| WBTC/USDC | 3000 (30 bps) | 60 | `0x99ac8ca7087fa4a2a1fb6357269965a2014abc35` | WBTC | USDC | 12,376,048 | `0x2acb44...` | 12,131,280,070,212 |
| WBTC/USDC | 10000 (100 bps) | 200 | `0xcbfb0745b8489973bf7b334d54fdbd573df7ef3c` | WBTC | USDC | 12,601,908 | `0x82996e...` | 11,765,642,448 |
| WBTC/USDT | 100 (1 bp) | 1 | `0xf98cf0d979cfbb780774f318e3da4f7317af50d7` | WBTC | USDT | 20,629,019 | `0x96ea4a...` | 1,466,762 |
| WBTC/USDT | 500 (5 bps) | 10 | `0x56534741cd8b152df6d48adf7ac51f75169a83b2` | WBTC | USDT | 12,601,886 | `0xd77ad5...` | 1,154,639,192,370 |
| WBTC/USDT | 3000 (30 bps) | 60 | `0x9db9e0e53058c89e5b94e29621a205198648425b` | WBTC | USDT | 12,376,091 | `0xcde67a...` | 3,223,374,074,088 |
| WBTC/USDT | 10000 (100 bps) | 200 | `0x5a59e4e647a3acc42b01715f3a1d271c1f7e7aeb` | WBTC | USDT | 12,601,886 | `0xcd1d4b...` | 759,402 |

*Note on Canonical Token Ordering:* In EVM hexadecimal sorting:
`0x2260... (WBTC) < 0xa0b8... (USDC) < 0xc02a... (WETH) < 0xdac1... (USDT)`.
Thus WBTC is unequivocally `token0` in all 12 pools. Any swap selling WETH into the WETH/WBTC pool is a `token1 -> token0` swap (`zero_for_one=False`). Any swap selling WBTC into WBTC/USDC or WBTC/USDT is a `token0 -> token1` swap (`zero_for_one=True`).

---

## 4. Activation Bound Evidence

All 12 pools were deployed and active on Ethereum mainnet between blocks 12,369,821 and 20,629,019. The start of the historical October window is block 23,549,939 (nearly 3 million blocks after the newest pool).

Dual-layer verification is recorded:
1. **Creation Event Verification:** Factory `PoolCreated` event logs indexed by `(token0=WBTC, token1=other)` decoded with exact transaction hashes and log indices.
2. **Start Pin Observation:** Factory query `getPool(tokenA, tokenB, fee)` at block 23,549,939 (hash `0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12`) confirmed all 12 addresses with non-zero active liquidity `liquidity()`.
3. **Activation Reason Conformance:** `activation_reason(rec, block_number)` in `swaparch/universe.py` returns `None` for every pool at all requested blocks (no `not_deployed_at_block` or `activation not established` errors).

---

## 5. Differential QuoterV2 Qualification

To verify that the existing Uniswap V3 adapter implementation reproduces live EVM execution without discrepancy, differential checks were run on mainnet at all three qualification pins against Uniswap's official `QuoterV2` contract (`0x61ffe014ba17989e743c5f6cb21bf9697530b21e`):

- **Tested Sizes:** 1 WETH, 10 WETH, and 100 WETH.
- **Tested Pools:**
  - Leg 1: WETH/WBTC 500 bps and 3000 bps
  - Leg 2: WBTC/USDC 500 bps and 3000 bps
  - Leg 2 (alt): WBTC/USDT 500 bps and 3000 bps
  *(Scope Note: These 6 pools represent the active routing venues. The other 6 pools with fee 100 and 10000 have full snapshot state collected and tick bitmaps validated, but were not QuoterV2-checked due to negligible in-range liquidity.)*
- **Verification Criteria:**
  - `local_amount_out == quoter_amount_out` (exact integer wei equality)
  - `local_sqrt_price_after == quoter_sqrt_price_after` (exact post-trade pool state equality)

### Qualification Results Summary

| Block | Pin Type | Checks Run | Wei-Exact Matches | Parity Rate | Max Discrepancy |
|---|---|---:|---:|---:|---:|
| **23549939** | Start Pin | 18 | 18 / 18 | 100.0% | **0 wei** |
| **23550094** | Stress Pin | 18 | 18 / 18 | 100.0% | **0 wei** |
| **23550192** | End Pin | 18 | 18 / 18 | 100.0% | **0 wei** |
| **Overall** | Combined | 54 | 54 / 54 | **100.0%** | **0 wei** |

Every single call across all pools, trade sizes, and pins returned **identical outputs and post-swap sqrtPriceX96 values** to `QuoterV2`. Full raw results are saved in `outputs/source-expansion/wbtc/quoter_checks.json`.

---

## 6. Two-Leg Route Analysis & Full Input Consumption

### Full Input Consumption Audit
For every trade size (1, 10, 100 WETH) across all tested routes:
- `amount_in_spent == amount_in`: 100% of requested input was consumed.
- `residual_in == 0`: No unspent dust was left.
- **Word Radius Adequacy:** With `word_radius=8` (17 contiguous bitmap words), the tick coverage was wide enough to absorb even 100 WETH trades without throwing `Unsupported` (insufficient tick coverage).
- **Intermediate Conservation:** The entire WBTC output from leg 1 was passed directly into leg 2 as exact integer units (`token_in` = `prior_token_out`), verifying that no intermediate inventory is required or created.

### Execution Metrics by Pin

#### Block 23549939 (Start Pin — Calm Baseline)
- **Route WETH -> WBTC(500) -> USDC(500):**
  - 1 WETH -> 0.033371 WBTC -> 3,767.01 USDC (3,767.01 USDC/WETH)
  - 10 WETH -> 0.332978 WBTC -> 37,501.63 USDC (3,750.16 USDC/WETH, impact: 44.7 bps)
  - 100 WETH -> 3.206325 WBTC -> 353,324.06 USDC (3,533.24 USDC/WETH, impact: 620.6 bps)
- **Route WETH -> WBTC(3000) -> USDC(3000):**
  - 1 WETH -> 0.033368 WBTC -> 3,581.21 USDC (3,581.21 USDC/WETH)
  - 10 WETH -> 0.333437 WBTC -> 35,783.16 USDC (3,578.32 USDC/WETH, impact: 8.1 bps)
  - 100 WETH -> 3.309210 WBTC -> 354,846.62 USDC (3,548.47 USDC/WETH, impact: 91.4 bps)
- **Direct WETH/USDC (0.05%):** 1 WETH = 3,655.72, 10 WETH = 3,653.04, 100 WETH = 3,626.44 USDC/WETH.

#### Block 23550094 (Stress Pin — Crash Price Spike)
- **Route WETH -> WBTC(500) -> USDC(500):**
  - 1 WETH -> 0.034510 WBTC -> **3,598.19 USDC** (**3,598.19 USDC/WETH**)
  - 10 WETH -> 0.344509 WBTC -> **35,816.49 USDC** (**3,581.65 USDC/WETH**, impact: 46.0 bps)
  - 100 WETH -> 3.385222 WBTC -> 342,215.22 USDC (3,422.15 USDC/WETH, impact: 489.3 bps)
- **Direct WETH/USDC (0.05%):**
  - 1 WETH = **3,580.05 USDC/WETH** (WBTC route is **+18.14 USDC/WETH higher**)
  - 10 WETH = **3,577.47 USDC/WETH** (WBTC route is **+41.79 USDC higher**)
  - 100 WETH = 3,548.65 USDC/WETH

#### Block 23550192 (End Pin — Recovery Baseline)
- **Route WETH -> WBTC(500) -> USDC(500):**
  - 1 WETH -> 0.034682 WBTC -> 3,867.58 USDC (3,867.58 USDC/WETH)
  - 10 WETH -> 0.346219 WBTC -> 38,517.71 USDC (3,851.77 USDC/WETH, impact: 40.9 bps)
  - 100 WETH -> 3.402678 WBTC -> 369,815.58 USDC (3,698.16 USDC/WETH, impact: 438.1 bps)
- **Direct WETH/USDC (0.05%):** 1 WETH = 3,905.34, 10 WETH = 3,903.06, 100 WETH = 3,879.80 USDC/WETH.

---

## 7. Minimally Invasive Integration Approach

To incorporate WBTC into the October study pipeline without editing shared files prematurely or creating a speculative generalized framework:

### Existing Architecture Compatibility
`src/swaparch/collection_supplement.py` already implements:
```python
def supplement_context(context, annotation: dict, root: Path):
```
which reads:
1. `root / 'records' / f'{context.block.hash}.json'`
2. `root / 'snapshots'` via `SnapshotStore(snapshot_root).load(...)`
3. Merges the supplemental records and pool states into the quote context.

Our generated files in `outputs/source-expansion/wbtc/` adhere **identically** to this format:
- `outputs/source-expansion/wbtc/snapshots/1/<block_hash>/`: standard SnapshotStore layout
- `outputs/source-expansion/wbtc/records/<block_hash>.json`: standard supplement records document

### Concrete Integration Patch Proposal
When root assigns integration across all slices, the October pipeline (`scripts/october_connectors.py` / `scripts/october_source_prices.py`) can load multiple overlays by chaining `supplement_context` or reading an overlay registry:

```python
# Proposed patch for scripts/october_connectors.py or shared pipeline runner:
OVERLAY_ROOTS = [
    ROOT / "outputs/october-connectors",          # Curve 3pool
    ROOT / "outputs/source-expansion/wbtc",        # WBTC 12 pools
]

def apply_all_supplements(context, annotation):
    for root in OVERLAY_ROOTS:
        rec_file = root / "records" / f"{context.block.hash}.json"
        if rec_file.is_file():
            context, annotation = supplement_context(context, annotation, root)
    return context, annotation
```

This requires zero new abstractions, reuses existing verified infrastructure, and allows WBTC liquidity to compete in the single routing universe.

---

## 8. Runnable Commands & Reproduction Instructions

### Offline Verification (No RPC, zero network requests)
```bash
# Run the complete test suite
uv run pytest -q tests/test_wbtc*.py

# Run the full repository test suite (verifying no regressions)
uv run pytest -q

# Run Ruff linter and formatting checks
uv run ruff check scripts/wbtc_*.py tests/test_wbtc*.py
uv run ruff format --check scripts/wbtc_*.py tests/test_wbtc*.py

# Run the end-to-end pipeline in offline mode
uv run python scripts/wbtc_pipeline.py --offline
```

### Live Archive-RPC Collection (Serialized with flock)
```bash
# Collect the 3 historical qualification pins (or specify --blocks)
flock /tmp/swaparch-source-rpc.lock uv run python scripts/wbtc_collect.py --blocks 23549939,23550094,23550192

# Run live QuoterV2 differential parity check
flock /tmp/swaparch-source-rpc.lock uv run python scripts/wbtc_quoter_check.py --blocks 23549939,23550094,23550192

# Run two-leg route evaluation
uv run python scripts/wbtc_route_eval.py --blocks 23549939,23550094,23550192

# Full end-to-end pipeline with live RPC
flock /tmp/swaparch-source-rpc.lock uv run python scripts/wbtc_pipeline.py
```

### Extending to All 254 October Blocks
```bash
# Rerunnable command for all 254 blocks in the October crash window:
flock /tmp/swaparch-source-rpc.lock uv run python -c '
from scripts.wbtc_collect import collect_blocks
blocks = list(range(23549939, 23550193))
collect_blocks(blocks)
'
```

---

## 9. Limitations and Boundary Compliance

- **No Premature Shared File Edits:** Neither `src/swaparch/universe.py`, `src/swaparch/collection.py`, `src/swaparch/cli.py`, `scripts/october_connectors.py`, `scripts/october_source_prices.py`, frontend, `START_HERE.md`, nor `signoff.md` were touched.
- **No Large Sweep Restart:** No keeper or large sweep scripts were restarted.
- **No Live Transactions / Signing:** Research and quote-read only.
- **Credential Protection:** No `.env` or credentials were read or printed; RPC credentials remained fully encapsulated within `RpcClient`.
