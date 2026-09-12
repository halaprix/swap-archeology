# USDS Uniswap V3 AMM Qualification & Collection Report

Bounded qualification, tick acquisition, and QuoterV2 cross-check for the 4 active discovered USDS Uniswap V3 connector pools across the three canonical historical crash window pins (`23549939`, `23550094`, `23550192`).

## 1. Deliverables & Artifact Paths
- **Collector Script**: [`scripts/usds_amm_collect.py`](file://<checkout>/scripts/usds_amm_collect.py)
- **Unit & Regression Suite**: [`tests/test_usds_amm.py`](file://<checkout>/tests/test_usds_amm.py)
- **Supplement Artifacts**: [`outputs/source-expansion/usds-amm/`](file://<checkout>/outputs/source-expansion/usds-amm/)
  - `records/<block_hash>.json`: 3 block records formatted for `supplement_context`.
  - `snapshots/1/<block_hash>/`: Full SnapshotStore tick bitmaps and word states for all 3 pins.
  - `collection_manifest.json`: Collection manifest summarizing specs, phases, and pool states.
  - `quoter_checks.json`: Independent QuoterV2 wei-level comparison receipts.
  - `usds_amm_inventory.json`: Overlay inventory of the 4 qualified AMM pool records.

## 2. Pool Metadata & Counts
Four active Uniswap V3 pools verified with on-chain canonical ordering at Factory `0x1f98431c8ad98523631ae4a59f267346ea31f984`:

| Pair | Fee Tier | Raw Fee | Spacing | Pool Address | Token0 (Dec) | Token1 (Dec) |
|---|---|---|---|---|---|---|
| **USDS / USDC** | 1 bps (0.01%) | 100 | 1 | `0x4eb5db0134fac94e66da89764d58a9f709d53a8f` | USDC (6) | USDS (18) |
| **USDS / USDC** | 5 bps (0.05%) | 500 | 10 | `0x8aee53b873176d9f938d24a53a8ae5cf36276464` | USDC (6) | USDS (18) |
| **USDS / USDC** | 30 bps (0.30%) | 3000 | 60 | `0xa66a2770bc0e0c65b63b5a3bb4560e90f95d6146` | USDC (6) | USDS (18) |
| **USDS / DAI** | 30 bps (0.30%) | 3000 | 60 | `0xe9f1e2ef814f5686c30ce6fb7103d0f780836c67` | DAI (18) | USDS (18) |

## 3. QuoterV2 Independent Validation Results
Cross-checked against on-chain `QuoterV2` (`0x61fFE014bA17989E743c5F6cB21bF9697530B21e`) across all 3 historical pins (24 checks: 18 matches, 6 refusals):
- **1 USDS Full-Input Swap**:
  - `USDS/USDC 100`: Local `998,951` raw base units (0.998951 USDC, 6 dec) == Quoter `998,951` raw base units (0.998951 USDC) (Exact Match).
  - `USDS/USDC 500`: Local `999,629` raw base units (0.999629 USDC, 6 dec) == Quoter `999,629` raw base units (0.999629 USDC) (Exact Match).
  - `USDS/USDC 3000`: Local `999,095` raw base units (0.999095 USDC, 6 dec) == Quoter `999,095` raw base units (0.999095 USDC) (Exact Match).
  - `USDS/DAI 3000`: Local `994,291,475,265,954,718` raw base units / wei (~0.994291 DAI, 18 dec) == Quoter `994,291,475,265,954,718` raw base units / wei (~0.994291 DAI) (Wei-Exact Match).
- **100,000 USDS Full-Input Swap**:
  - `USDS/USDC 500`: Local `99,947,391,475` raw base units (99,947.391475 USDC, 6 dec) == Quoter `99,947,391,475` raw base units (99,947.391475 USDC) (Exact Match).
  - `USDS/DAI 3000`: Local `99,313,624,140,399,156,922,394` raw base units / wei (~99,313.624140 DAI, 18 dec) == Quoter `99,313,624,140,399,156,922,394` raw base units / wei (~99,313.624140 DAI) (Wei-Exact Match).
  - `USDS/USDC 100`: Fails closed with `Unsupported("insufficient tick coverage")` (QuoterV2 returned partial fill of only `1,935,991` raw base units / ~1.935991 USDC).
  - `USDS/USDC 3000`: Fails closed with `Unsupported("insufficient tick coverage")` (QuoterV2 returned partial fill of only `1,596,088,812` raw base units / ~1,596.088812 USDC).
  - **Exact Boundary Enforced**: Router avoids fabricated fullfill when liquidity is depleted outside bounded tick radius.

## 4. Verification
- Offline test execution: `./.venv/bin/pytest tests/test_usds_amm.py tests/test_usds_adapter.py`
  - 20 passed in 0.35s.
- Offline collector rerun: `./.venv/bin/python scripts/usds_amm_collect.py --offline`
  - 3 blocks re-verified offline with 0 RPC calls.
- Linter: `./.venv/bin/ruff check scripts/usds_amm_collect.py tests/test_usds_amm.py`
  - All checks passed.
