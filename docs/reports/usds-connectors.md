# USDS Connectors & Historical Discovery Revision Report

Historical research, adapter modeling, and candidate liquidity inventory for Maker/Sky USDS connectors
(`DaiUsdsConverter` and `UsdsPsmWrapper`) on Ethereum mainnet (blocks 23549939..23550192).

## 1. Executive Summary & Review Revisions
- **Active Integration Model**: Single `DaiUsdsConverter` (`0x3225...`) + existing `LitePsmState`. No separate competing wrapper pool in the active universe to prevent duplicate shared LitePSM USDC reserves.
- **Wrapper Status**: `UsdsPsmWrapper` (`0xa188...`) retained as a qualified reference-only model advertising LitePSM capacity IDs (`maker_sky_psm:0xf6e7...:dai_buffer` / `:pocket_usdc`).
- **Canonical Join/Exit Semantics**: Verified via CodeGraph against canonical repo (`<external-repos>/usds`, commit `d65551d`). Gating is direction-specific; arithmetic is bounded by DSS `wad * RAY` overflow.
- **Candidate AMM Inventory**: Corrected fee units (raw fee 500=5 bps, 100=1 bps, 3000=30 bps). Clarified raw $L$ decimal scaling and uninitialized candidate inventory status.

## 2. Scope of Proof & Validation Boundaries
- **Exact Source-Traced Arithmetic**: Smart contract logic from canonical Solidity (`DaiUsds.sol`, `UsdsJoin.sol`, `DaiJoin.sol`). Proves exact integer 1:1 conservation, fee math, and the `MAX_CONVERSION_WAD` bound.
- **Raw Pinned Getter State**: Pinned historical RPC calls across blocks 23549939, 23550094, and 23550192 confirm static parameters, `live == 1`, `wards == 1`, and LitePSM buffer/allowance states.
- **Unrun Execution Boundary**: Pinned getter checks and model-vs-model equivalence tests confirm parameter state and internal mathematical equivalence; they do **NOT** constitute funded on-chain execution or independent live quote validation.

## 3. Canonical DaiJoin / UsdsJoin Semantics (CodeGraph Verified)
Primary source: `<external-repos>/usds` (commit `d65551dbc11cfe1afcc4718ab790663b99d766af`) and `<external-repos>/dss-lite-psm`:
- **`DAI -> USDS` (`daiToUsds`)**:
  - Calls `daiJoin.join(this, wad)` (burns DAI; does NOT require `daiJoin.live == 1` or `dai.wards`).
  - Calls `usdsJoin.exit(usr, wad)` (calls `usds.mint(usr, wad)`, requiring `usds.wards(usdsJoin) == 1`).
- **`USDS -> DAI` (`usdsToDai`)**:
  - Calls `usdsJoin.join(this, wad)` (burns USDS; does not require `usds.wards`).
  - Calls `daiJoin.exit(usr, wad)` (`require(live == 1, "DaiJoin/not-live")` and calls `dai.mint`, requiring `dai.wards(daiJoin) == 1`).
- **Arithmetic Limits & Overflow Protection**:
  - In `UsdsJoin.exit/join` and `DaiJoin.join/exit`, amounts are scaled by `RAY` ($10^{27}$) in `Vat.move`.
  - Solidity 0.8.21 checks overflow on `wad * RAY`. Inputs with `wad > MAX_CONVERSION_WAD` revert:
    $$\text{MAX\_CONVERSION\_WAD} = \lfloor (2^{256} - 1) / 10^{27} \rfloor = 115792089237316195423570985008687907853269984665640564$$
  - The converter fails closed on `amount_in > MAX_CONVERSION_WAD`; unbounded 1:1 conversion for `uint256.max` is not executable.

## 4. Candidate AMM Inventory & Fee Units (Block 23549939)
Raw fee units in Uniswap V3 are parts per million ($10^{-6}$):
- `fee = 100`: **1 bps** (0.01%)
- `fee = 500`: **5 bps** (0.05%)
- `fee = 3000`: **30 bps** (0.30%)

### 4.1 Uniswap V3 Candidate Pools
| Pair | Fee Tier | Raw Fee | Address | Active $L$ at 23549939 | Status |
|---|---|---|---|---|---|
| **USDS / USDC** | 5 bps (0.05%) | 500 | `0x8aee...` | `641,557,830,417,479,316,323` | Active candidate |
| **USDS / USDC** | 1 bps (0.01%) | 100 | `0x4eb5...` | `2,504,075,123,803,858` | Active candidate |
| **USDS / USDC** | 30 bps (0.30%) | 3000 | `0xa66a...` | `557,249,876,720,826,746` | Active candidate |
| **USDS / DAI** | 30 bps (0.30%) | 3000 | `0xe9f1...` | `85,593,150,411,941,631,354,179,755` | Active candidate |
| **USDS / USDT** | 1 bps (0.01%) | 100 | `0x31e2...` | `0` | Inactive candidate |

> [!NOTE]
> **Raw $L$ Across Token Decimals**: Raw integer $L = \sqrt{\Delta x \cdot \Delta y}$. For USDS/USDC (18 dec / 6 dec), raw $L$ scales as $10^{12}$, whereas for USDS/DAI (18 dec / 18 dec), raw $L$ scales as $10^{18}$. Raw $L$ cannot be compared directly across pairs or interpreted as nominal dollar depth without decimal normalization.

### 4.2 Uniswap V4 Candidate Hookless Pools
Candidate pool keys (`USDC/USDS`, `USDT/USDS`, `DAI/USDS`) returned `liquidity = 0` via `StateView.getLiquidity()` at block 23549939. Zero getter output indicates no active in-range liquidity (uninitialized/inactive candidate inventory; not proof of active deployment).

## 5. Active Integration Architecture
- **Canonical Universe Route**: Route USDS liquidity through `DaiUsdsConverter` into existing `LitePsmState`:
  $$\text{USDS} \xrightarrow{1:1} \text{DAI} \xrightarrow{\text{LitePSM}} \text{USDC}$$
- **Zero Capacity Duplication**: Consumes only the single `LitePsmState` DAI buffer and pocket USDC reserves.
- **Reference Wrapper**: `UsdsPsmWrapper` in `usds.py` is labeled reference-only and excluded from the active universe overlay (`outputs/source-expansion/usds/usds_inventory_overlay.json`).

## 6. Concrete Verification Evidence
- **Unit Test Suite**: Ran `./.venv/bin/pytest tests/test_usds_adapter.py`:
  ```
  collected 14 items
  tests/test_usds_adapter.py ..............                                [100%]
  14 passed in 0.18s
  ```
  Verified: 1:1 conservation, direction-specific gating, `MAX_CONVERSION_WAD` bound rejection, graph-edge evaluation, dust refund handling, and offline reproduction of pinned evidence at blocks 23549939, 23550094, 23550192.
- **Linter**: Ran `./.venv/bin/ruff check src/swaparch/adapters/usds.py scripts/usds_qualification.py tests/test_usds_adapter.py`:
  ```
  All checks passed!
  ```
