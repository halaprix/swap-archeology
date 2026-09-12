# Source Expansion Integration Report

## Scope & Pipeline
- **Command**: `.venv/bin/python scripts/source_expansion_pipeline.py --offline --blocks 23549939,23550094,23550192`
- **Solver Policy**: Bounded 3-step `GeneralSearchSolver` + default 10-grid `BaselineSolver`, with candidate fallback ensuring `amount_out(combined) >= amount_out(baseline)`.
- **Trade Request**: Strict exact-in (no opt-in sub-microDAI PSM refund); bounded strict comparison, not a rerun of published chart/search profiles.

## Before / After Pin Results (WETH -> USDC)
| Block | Size (WETH) | Baseline (USDC) | Combined (USDC) | Delta (bps) |
|---|---|---|---|---|
| 23549939 | 1 | 3,750.501078 | 3,787.418875 | +98.43 |
| 23549939 | 10 | 37,434.869074 | 37,580.680735 | +38.95 |
| 23549939 | 100 | 371,029.870634 | 371,285.824100 | +6.90 |
| 23550094 | 1 | 3,806.283530 | 3,806.283530 | +0.00 |
| 23550094 | 10 | 37,359.517595 | 37,359.517595 | +0.00 |
| 23550094 | 100 | 358,848.211296 | 358,848.211296 | +0.00 |
| 23550192 | 1 | 3,905.341741 | 3,905.341741 | +0.00 |
| 23550192 | 10 | 39,030.635957 | 39,030.635957 | +0.00 |
| 23550192 | 100 | 388,459.575235 | 388,459.575235 | +0.00 |

USDS & WBTC quotes:
- USDS->USDC: 100k USDS fills at 1.0 (23549939/23550192) and 1.00067 (23550094).
- WBTC->USDC: 1 WBTC yields 113,011.82 (23549939), 104,014.81 (23550094), 111,861.90 (23550192).

## Quoter & Adapter Validation Evidence
- **PancakeSwap V3**: 27 candidate checks: 21 exact matches, 6 refusals (3 pools admitted: WETH/USDC 500, WETH/USDT 500, WETH/WBTC 2500).
- **USDS-AMM**: 24 candidate checks: 18 matches, 6 refusals (4 UniV3 connector pools qualified: USDC/USDS 100, 500, 3000 and DAI/USDS 3000).
- **WBTC**: 12 UniV3 pools snapshotted, 6 selected-route pools quote-checked.
- **Maker/Sky USDS**: Routes via `DaiUsdsConverter` (1:1 zero-fee edge) into existing `LitePSM` state. `UsdsPsmWrapper` rejected to prevent reserve duplication (single PSM instance verified).
- **Ekubo Orrery**: Added pinned v3.2.0 SHA `de94c77a665c54f4c848185872b8a55b2c28c1bc` (CodeGraph verified). V3 absent in October 2025 (empty bytecode); V2 retained as unresolved adapter/tick gap.

## Limitations & Verification Status
- **Test Results**: 120 targeted checks reconciled after harness fixes; 9/9 integration tests passed.
- **Universe Bounds**: Total states expand from 78->98 (block 23549939) and 108->128 (blocks 23550094, 23550192).
- **Unsupported/Unavailable**: Ekubo V2, Balancer V3, Lista, Spark, ERC-4626, 0x RFQ remain explicitly unresolved/unsupported/unavailable.
- **Execution Notice**: Historical routing research only; no global optimality or atomic execution claim. Published 254-block dataset unchanged; full sweep and keeper remain stopped.
