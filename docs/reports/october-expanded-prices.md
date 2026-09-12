# October Expanded Source Prices Report

## Executive Summary

This report documents the completed bounded crash replay and export update for the October 10 crash window (blocks 23549939..23550192, 254 consecutive blocks). It expands the routing universe beyond the baseline and Curve connectors by incorporating all qualified overlays into a single unified routing evaluation:
- **Curve**: Canonical legacy 3pool (DAI/USDC/USDT, `0xbebc4478...`)
- **WBTC**: 12 Uniswap V3 connector pools (6 active quote-qualified on selected routes)
- **USDS**: `DaiUsdsConverter` (`0x3225...`, 1:1 zero-fee conversion edge into LitePSM)
- **USDS-AMM**: 4 Uniswap V3 connector pools (USDC/USDS 100, 500, 3000 and DAI/USDS 3000)
- **PancakeSwap V3**: 3 qualified pools (WETH/USDC 500, WETH/USDT 500, WETH/WBTC 2500)

Across all 254 blocks × 6 cases = 1,524 aggregate quotes evaluated:
- **286 improved**, **1,238 unchanged**, and **0 regressions** against connector candidate floors.
- **Improvements by case**: WETH:1 improved in 155 blocks, WETH:10 improved in 91 blocks, and WETH:100 improved in 40 blocks.
- **Winning routes using new sources**: WBTC connector routes participated in 208 winning routes; PancakeSwap V3 participated in 104 winning routes (with route split overlap).
- **USDS**: Both `DaiUsdsConverter` and the 4 USDS-AMM pools were fully available in the state universe, but produced 0 winning routes in this specific sell-side interval.
- **Largest gain**: Block 23550124 (2025-10-10 21:51:23 UTC) for WETH:1:
  - Connector price: **3,737.773222 USDC** (279.82 bps below Chainlink)
  - Expanded price: **3,929.703712 USDC** (**+513.488857 bps gain**)
  - Reference oracles at block: Chainlink = **3,845.373809 USDC**, Aave = **3,834.113897 USDC**
  - Result: The newly resolved execution route is **2.193% ABOVE Chainlink** (219.30 bps premium over oracle, not merely closing a discount gap).
  - Independent qualification: Canonical historical `Uniswap V3 Quoter` was executed via `eth_call` for **both** exact winning legs (`outputs/october-sources-expanded/largest-gain-qualification.json`), matching `amount_out` and `sqrt_price_after` to the exact wei.

---

## Architectural Implementation in `scripts/october_source_prices.py`

### 1. Multiple Mandatory Supplements & Fail-Closed Full Input
The script accepts multiple `--supplement` arguments. For the canonical expanded run, the mandatory supplements are:
1. `outputs/october-connectors` (Curve 3pool supplement)
2. `outputs/october-expansion` (standard supplement root collected for all 254 blocks)

A fail-closed preflight check (`validate_supplement_coverage`) verifies that for every requested block, every supplement directory contains:
- `records/<blockHash>.json`
- `snapshots/1/<blockHash>/header.json`
- `snapshots/1/<blockHash>/calls.json.gz`

Any missing block artifact halts execution immediately before routing commences.

### 2. Search Policy Preservation vs Small Diagnostic
The published search policy is strictly preserved:
- Solver: `GeneralSearchSolver` with `grid_parts=10`, `max_steps=8`, `beam_width=128`, `max_expansions=5000`.
- LitePSM DAI refund: Opt-in sub-microDAI refund (`allow_psm_dai_refund=True`), permitting exactly one terminal LitePSM DAI→USDC swap leaving `< 1e12` wei DAI reported as `terminal_refund`.
- Note on Diagnostic: The earlier `source_expansion_pipeline.py` ran a strict 3-step diagnostic without PSM refund. That diagnostic is **not** the published policy and is superseded by this complete policy evaluation.

### 3. Candidate Floor Enforcement & Re-Evaluation
The previous published export in `outputs/october-sources-connectors/` serves as a candidate floor:
- Each block quote is compared against the previous `best_split` from `outputs/october-sources-connectors/aggregate-raw/<hash>.json.gz`.
- If beam search pruning in the expanded state universe yields a lower `amount_out` than the previous output, `_re_evaluate_candidate` reconstructs the previous winning `Plan` and evaluates it against `context.states` with `Evaluator(allow_psm_dai_refund=True)`.
- Because all previous pools are retained in `context.states`, the incumbent candidate is guaranteed to be valid and evaluate to identical output.
- Zero regressions are enforced across all 1,524 cases.

### 4. Incremental Checkpoints & Bounded Concurrency
- `OUT/checkpoint.json` is updated after every block with atomic replacement.
- Individual block direct quotes are cached in `OUT/raw/<hash>.json.gz` and aggregate quotes in `OUT/aggregate-raw/<hash>.json.gz`.
- `--workers 4` partitions the 254-block sweep across 4 worker subprocesses with native Cython acceleration, avoiding GIL contention and mutable state pollution.

### 5. Guarded Atomic Frontend Export
- Frontend datasets (`frontend/public/october-sources.json` and `october-sources.csv`) are published **only** when all 254 blocks are completely evaluated and validated.
- Validation checks confirm `frontend/public/october-sources.json` matches `outputs/october-sources-expanded/prices.json` bit-for-bit (SHA256: `7f739ea3938dc4e7...`).

---

## Timings: Cold Execution vs Warm Cache Benchmarks

Empirical timing measurements on Block 23549939 (6 quotes: ETH/WETH × 1/10/100) under identical hardware:

| Execution Mode | Per-Block Time (6 Quotes) | Description | Notes |
|---|---|---|---|
| **Cold Native** (`perf_native.optimized`) | **3.994s** | Full beam search from scratch (empty output dir, no cache hit) | Cython acceleration active; 128 beam width |
| **Cold Pure-Python** | **~25.0s** | Full beam search from scratch without native patches | ~6.3× slower than native |
| **Warm Cache Hit** | **0.211s** | Reads direct quotes from `raw/` and aggregate quotes from `aggregate-raw/` | 18.9× faster than cold native; used during assembly |

- **Full Sweep Timing (254 Blocks)**:
  - 4 parallel workers finished the full 254-block sweep in **~3.8 minutes** (~230s).
  - Assembly and validation across all 254 blocks completed in **9.2 seconds**.

---

## Detailed Results & Winning Route Mechanics

### Representative Historical Pins
Derived from exact raw execution evidence:

| Block | Asset | Size | Connector Price (USDC) | Expanded Price (USDC) | Delta (bps) | Exact Winning Feature |
|---|---|---|---|---|---|---|
| **23549939** | ETH | 1 | 3,759.910802 | 3,759.910802 | +0.00 | Direct V4 |
| 23549939 | ETH | 10 | 37,409.874249 | 37,409.874249 | +0.00 | Direct V4 |
| 23549939 | ETH | 100 | 370,671.569512 | 370,671.569512 | +0.00 | Direct V4 |
| 23549939 | WETH | 1 | 3,750.501078 | 3,787.418875 | **+98.43** | **WBTC-only**: 70/30 split via UniV3 WBTC connector legs (`0x4585fe...` & `0x6ab3bb...` → `0xcbfb07...` & `0x9a7720...`) |
| 23549939 | WETH | 10 | 37,434.869074 | 37,580.680735 | **+38.95** | **WBTC-only**: 10/90 split via UniV3 WBTC connector legs (`0x4585fe...` & `0xcbcdf9...` → `0xcbfb07...` & `0x9a7720...`) |
| 23549939 | WETH | 100 | 371,029.870634 | 371,285.824100 | **+6.90** | **Split**: 40 WETH via UniV3 WBTC (`0xcbcdf9...` → `0x9a7720...`) + 60 WETH via UniV4 WETH/USDT + UniV3 USDT/USDC |
| **23550094** | ETH | 1 | 3,792.660179 | 3,792.660179 | +0.00 | Direct V4 |
| 23550094 | ETH | 10 | 37,573.959223 | 37,573.959223 | +0.00 | Direct V4 |
| 23550094 | ETH | 100 | 359,979.979182 | 359,979.979182 | +0.00 | Direct V4 |
| 23550094 | WETH | 1 | 3,807.041091 | 3,807.041091 | +0.00 | Retains LitePSM DAI refund (211,768,636,185 wei DAI returned) |
| 23550094 | WETH | 10 | 37,359.517595 | 37,359.517595 | +0.00 | Candidate floor preserved |
| 23550094 | WETH | 100 | 358,848.211296 | 358,848.211296 | +0.00 | Candidate floor preserved |
| **23550192** | All 6 | - | Candidate floors | Candidate floors | +0.00 | All 6 cases match candidate floors (0 regressions) |

### PancakeSwap V3 Participation
PancakeSwap V3 pools participate in 104 winning routes across the 254 blocks, first appearing at block 23549940 for WETH:1 (`3,787.793683 USDC`). In these blocks, the direct PancakeSwap V3 WETH/USDC 500 pool (`0x1ac1a8fe...`) and WETH/USDT 500 pool (`0x6ca298d2...`) provide superior execution depth over pure Uniswap V3 direct routes.

### Largest Gain: Block 23550124
- Case: WETH:1 (1 WETH → USDC) at 2025-10-10 21:51:23 UTC.
- Leg 1: 1 WETH → WBTC via `uniswap_v3:0x4585fe77225b41b697c938b018e2ac67ac5a20c0` (fee 500) produces `3,482,096` satoshis.
  - Quoter check: matches exact `3,482,096` satoshis, `sqrt_price_after = 42451368190500822910421441282186012`.
- Leg 2: 3,482,096 satoshis → USDC via `uniswap_v3:0x9a772018fbd77fcd2d25657e5c547baff3fd7d16` (fee 500) produces `3,929,703,712` raw units (**3,929.703712 USDC**).
  - Quoter check: matches exact `3,929,703,712` raw units, `sqrt_price_after = 2661888682810840030557290684049`.
- Oracle reference: Chainlink was 3,845.373809 USDC; execution price is **2.193% above Chainlink**.

---

## Automated Verification & Reproducibility

1. **Independent Verification Script**:
   - `scripts/validate_october_expanded.py` independently verifies all 1,524 cases against previous connector floors, verifies no duplicate wrapper pools (`0xa188...`), checks improvements and regressions, and confirms public frontend files match canonical prices.
   - Run command: `.venv/bin/python scripts/validate_october_expanded.py`
2. **Targeted Test Suite**:
   - `tests/test_october_expanded_prices.py` covers fail-closed input validation, candidate plan reconstruction with PSM refund, positive Pancake swap outputs and family preservation, complete routed quote parity, and payload structure enforcement.
   - Run command: `.venv/bin/pytest tests/test_october_expanded_prices.py` (6 passed in 5.98s).
3. **Linter**:
   - `ruff check scripts/october_source_prices.py scripts/validate_october_expanded.py tests/test_october_expanded_prices.py` passes with 0 warnings.

---

## Boundaries & Prior Disclaimers Preserved

- **No All-Block Quoter Qualification**: Independent RPC quoter verification was performed for pins and the largest gain block; not all 254 blocks are independently quote-checked against on-chain quoter contracts.
- **Ekubo Status**: Ekubo V3 was uncreated in October 2025 (empty bytecode); Ekubo V2 remains an unresolved adapter/tick inventory gap.
- **ETH Wrapping**: Direct WETH/ETH wrapping edge is not fabricated; ETH and WETH execution curves remain strictly separate.
- **USDS Routing**: USDS AMM and DaiUsdsConverter were active in the routing graph without reserve duplication, but produced 0 wins in this sell-side window.
- **Research Only**: Historical model research; no claims of atomic execution or live transaction submission.
