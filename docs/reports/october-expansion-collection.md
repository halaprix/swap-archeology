# October Expanded-Source Collection Report

## Executive Summary
Completed historical state collection for all 254 consecutive Ethereum mainnet blocks (`23549939..23550192`) across the 20 qualified expanded liquidity venues. Emitted a standard `collection_supplement` root at `outputs/october-expansion/` with complete records and snapshot calls compatible with `supplement_contexts`.

## Scope & Inventory
- **Blocks**: 254 consecutive blocks `23549939..23550192` (October 10 2025 ETH crash window).
- **Canonical Provenance**: Exact canonical block hashes and timestamps loaded from `outputs/dense-crash/references.json`.
- **Expanded Venues (20 pools / block)**:
  1. **12 WBTC Uniswap V3 connector pools**:
     - WETH/WBTC (1, 10, 60, 200 spacing; fees 100, 500, 3000, 10000)
     - WBTC/USDC (1, 10, 60, 200 spacing; fees 100, 500, 3000, 10000)
     - WBTC/USDT (1, 10, 60, 200 spacing; fees 100, 500, 3000, 10000)
  2. **4 USDS AMM Uniswap V3 connector pools**:
     - USDS/USDC (1, 10, 60 spacing; fees 100, 500, 3000)
     - USDS/DAI (60 spacing; fee 3000)
  3. **3 Admitted PancakeSwap V3 pools**:
     - USDC/WETH 500 (spacing 10; `0x1ac1a8feaaea1900c4166deeed0c11cc10669d36`)
     - WETH/USDT 500 (spacing 10; `0x6ca298d2983ab03aa1da7679389d955a4efee15c`)
     - WBTC/WETH 2500 (spacing 50; `0x9b5699d18dff51fc65fb8ad6f70d93287c36349f`)
  4. **1 DAI-USDS Converter**:
     - `DaiUsdsConverter` (`0x3225737a9bbb6473cb4a45b7244aca2befdb276a`): 1:1 zero-fee edge between DAI and USDS.
     - **Shared PSM constraint**: `UsdsPsmWrapper` is explicitly excluded to preserve single-instance PSM reserves and prevent duplicate reserve modeling.

## Execution Command & Performance
- **Command**: `uv run python scripts/october_expansion_collect.py --benchmark-count 3`
- **RPC Lock**: Serialized under `/tmp/swaparch-source-rpc.lock` via `fcntl.flock(LOCK_EX)`.
- **Total Elapsed**: 614.59s (~10.2 minutes) for all 254 blocks.
- **Seeding**: 3 historical qualification pins (`23549939`, `23550094`, `23550192`) seeded directly from existing verified evidence in `outputs/source-expansion/`.
- **Benchmarking (First 3 Live Blocks)**:
  - Block 23549941: 2,770 specs in 3 phases, 0 RPC reqs (cache), 0.616s
  - Block 23549942: 2,770 specs in 3 phases, 15 RPC reqs, 2.925s
  - Block 23549943: 2,770 specs in 3 phases, 15 RPC reqs, 2.828s
  - **Benchmark summary**: Average 2.12s/block; remaining ETA estimated at 8.9 minutes (actual elapsed: ~10 minutes).
- **Batching & Network**: Multicall3 aggregated reads per block (batch size 200, word radius 8), averaging ~15 RPC round trips per block with 50ms inter-block pacing.

## Completeness & Validation
- **Missing Blocks**: 0 / 254 (100% complete).
- **Errors**: 0 errors, 0 reverts, 0 partial fill fallbacks.
- **Dataset Artifacts**:
  - `outputs/october-expansion/records/<blockHash>.json`: 254 files, each containing 20 valid `PoolRecord` dicts.
  - `outputs/october-expansion/snapshots/1/<blockHash>/header.json`: 254 files with chain, block number, hash, and timestamp.
  - `outputs/october-expansion/snapshots/1/<blockHash>/calls.json.gz`: 254 gzip archives containing ~2,770 call results per block.
  - `outputs/october-expansion/collection_manifest.json`: Full manifest with block bounds, statistics, and validation status.

## State Collection vs. Independent On-Chain Quote Qualification
- **Collection-Model State**: This collection pass reconstructs raw, block-pinned contract state (`slot0`, `liquidity`, tick bitmaps within radius 8, initialized ticks, and converter live/wards gates) across all 254 consecutive blocks into standard `collection_supplement` snapshots and records.
- **Quote Qualification Distinction**: Populating `validated_block_hashes` in pool records satisfies downstream solver/supplement loading gates, but is **not** proof that every individual pool and block has been independently compared against on-chain Quoter contracts. Independent on-chain Quoter qualification was rigorously established across the three historical pins (`23549939`, `23550094`, `23550192`) during initial qualification passes (`pancake_v3_qualify.py`, `usds_amm_collect.py`, `usds_qualification.py`). Full per-block quoter comparison or sweep replay remains a separate responsibility for downstream simulation.

## Test Evidence
- **Targeted Test**: `tests/test_october_expansion_collect.py`
  - `test_block_range_and_references`: Confirms all 254 blocks match canonical references and pin hashes.
  - `test_build_expansion_records_structure`: Asserts exact 20 records, family breakdown (16 UniV3, 3 PancakeV3, 1 MakerSky), unique pool IDs, and absence of `UsdsPsmWrapper`.
  - `test_seed_pinned_evidence_and_supplement_context`: Validates offline pin seeding and `supplement_contexts` state expansion (77 -> 97 states), routing 100k USDS -> USDC through `DaiUsdsConverter` + existing `LitePSM` at exact 1.0 parity.
  - `test_collected_dataset_completeness_and_manifest`: Verifies all 254 records and snapshot calls exist on disk.
  - `test_supplement_context_on_collected_dataset_across_pins`: Verifies `supplement_contexts` on the full collected directory across pins 23549939, 23550094, and 23550192, evaluating feasible 1 WETH -> USDC baseline plans.
  - `test_corrupted_checkpoint_refusal`: Validates that `is_block_collected` strictly refuses forged or corrupted checkpoints (tampered timestamp, mismatched block number, duplicate pool IDs, unexpected pool IDs, tampered header, or corrupted gzip calls).
  - Result: **6/6 passed in 10.15s**.
- **Linting & Code Quality**: `uv run ruff check scripts/october_expansion_collect.py tests/test_october_expansion_collect.py` -> **Clean (0 errors)**.

## Boundaries & Constraints
- **Ownership**: Strictly limited to `scripts/october_expansion_collect.py`, `tests/test_october_expansion_collect.py`, `outputs/october-expansion/`, and `docs/reports/october-expansion-collection.md`.
- **Security**: No credentials, private endpoints, or `.env` files read or printed.
- **Transactions & Commits**: Read-only research artifact; no live transactions, git commits, or pushes.
