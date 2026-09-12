# October Oracle References Report: 1inch Spot Aggregator & Uniswap V3 TWAPs

## Executive Summary

This report documents the acquisition, canonical verification, mathematical implementation, and full publication of oracle-like price reference series across all **254 blocks** (`23549939..23550192`) of the October 10, 2025 crash window (2025-10-10 21:14:11 UTC to 22:04:59 UTC).

Two new reference mechanisms have been added alongside existing Chainlink and Aave feeds:
1. **1inch Spot Price Aggregator (`oneinch_spot`)**: The canonical on-chain `OffchainOracle` contract querying liquidity-weighted spot prices across DEXes for WETH $\rightarrow$ USDC with wrappers disabled.
2. **Uniswap V3 Geometric Observation TWAPs (`uniswap_v3_twap_60` and `uniswap_v3_twap_300`)**: Time-weighted average prices derived from canonical pool observations (`observe([secondsAgo, 0])`) on the flagship USDC/WETH 0.05% pool (`0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`), with a 300-second window designated as the default frontend comparison and a 60-second window capturing high-frequency shifts.

All 254 blocks were collected using a single bounded, idempotent collector leveraging Multicall3 and local SQLite caching. The data has been published to the independent public sidecar file [`frontend/public/october-oracle-references.json`](../../frontend/public/october-oracle-references.json) adhering strictly to the agreed schema contract. Full raw integer evidence, bytecode pins, and provenance records are stored under [`outputs/october-oracle-references/`](../../outputs/october-oracle-references/).

---

## 1. Orrery Submodule Addition & CodeGraph Indexing

In accordance with project maintenance rules in [`external/CONTRIBUTING.md`](file://<external-repos>/CONTRIBUTING.md):
- **Repository**: [https://github.com/1inch/spot-price-aggregator](https://github.com/1inch/spot-price-aggregator)
- **Submodule Location**: `<external-repos>/spot-price-aggregator`
- **Pinned Commit**: `393e7b14c8e89ee6a352116d3f10f2da15daf49a` (the HEAD of master as of October 10, 2025, specifically commit `393e7b1` merged on 2025-09-19).
- **Configuration**: Configured `.gitmodules` with `ignore = untracked` to prevent local `.codegraph` artifacts from dirtying git status.
- **Indices**: Added entry to [`indices/oracles.md`](file://<external-repos>/indices/oracles.md) and updated [`index.md`](file://<external-repos>/index.md) (updating oracle repository count to 2).
- **CodeGraph**: Initialized and indexed (`codegraph init`: 147 files, 895 nodes, 1,566 edges). Explored `OffchainOracle`, `OracleBase`, and sub-oracles prior to code implementation.
- **No Commits/Pushes**: No git commit or push was executed in either repository.

---

## 2. Canonical 1inch Deployment & Pricing Semantics

### Deployment Provenance & On-Chain Proofs
- **Canonical Address**: `0x00000000000D6FFc74A8feb35aF5827bf57f6786`
- **Deployment Block**: `20535992` (Ethereum mainnet, August 15, 2024, approximately 3 million blocks before the crash window).
- **Deployment Transaction**: `0x92b2468bc445ae33741f5c880449b00e29f54b3bd3bf31ffa01c787cda3b5707`.
- **Bytecode Verification**: Bytecode length is 12,150 bytes (24,302 hex characters). Keccak256 hash is `0x90010a17e0e152a1dba0b17a085039debe9249111b73c401f464806f4af0eaa0`. Pre-deployment block `20535991` confirmed empty (`0x`). The contract existed and was active throughout blocks `23549939..23550192`.
- **Raw Deployment Proof**: Stored in [`outputs/october-oracle-references/deployment-proof.json`](../../outputs/october-oracle-references/deployment-proof.json) containing actual raw `eth_getCode` responses at block 20535991, 20535992, and all three historical pins (23549939, 23550094, 23550192) alongside the deployment transaction receipt.

### Historical Configuration & Chainlink Presence Analysis
- **Interface Structure**: `OffchainOracle.oracles()` returns `(address[] allOracles, uint8[] oracleTypes)`, whereas `connectors()` is a separate getter returning `address[] allConnectors`.
- **Actual Historical Values**: Captured verbatim across all three pins in [`outputs/october-oracle-references/historical-1inch-config.json`](../../outputs/october-oracle-references/historical-1inch-config.json):
  - **Connectors (8)**: `ETH (0x0)`, `WETH`, `USDC`, `DAI`, `USDT`, `NONE (0xFF..FF)`, `3Crv (0x6c3f...)`, `WBTC`.
  - **Oracles (15)**:
    - 9 Uniswap-like AMMs: Uniswap V3 (`0x008d...`), Uniswap V2 (`0xa21e...`), SushiSwap (`0x2a45...`), ShibaSwap (`0x0fe8...`), Equalizer (`0xeba3...`), PancakeSwap V3 (`0x7e72...`), Uniswap V4 (`0xfbf5...`), Uniswap V1 (`0x7e72...`), Mooniswap (`0x5f6a...`).
    - 2 DODO AMMs: DODO V1 (`0x0a7c...`), DODO V2 (`0x03aa...`).
    - 1 Curve Oracle (`0x4e5c...`).
    - 1 Kyber DMM (`0xb194...`).
    - 1 Synthetix (`0xb7ef...`).
    - **1 ChainlinkOracle (`0x8606321723d9ca7db708a8b12dad0a8a83f2f3bd`, OracleType 1 / ETH)**.
- **Chainlink Presence & Independence Nuance**:
  - The 1inch `OffchainOracle` registry explicitly includes `ChainlinkOracle`.
  - Calling `ChainlinkOracle.getRate(WETH, USDC, NONE, 0)` directly at block 23549939 **reverts with `"Feed not found"`** because Chainlink does not maintain an ERC20 WETH/ETH exchange rate feed in its feed registry.
  - As a result, for direct WETH $\rightarrow$ USDC without custom connectors, Chainlink contributes zero weight, and the returned spot price is derived entirely from the on-chain DEX AMM pools (Uniswap V3, Uniswap V2, Curve, etc.).
  - **Critical Caveat**: Specifying `useWrappers=false` does **NOT** automatically prove architectural independence from Chainlink across arbitrary token pairs. If a pair has direct Chainlink feeds or if multi-hop connector routing evaluates an intermediate hop with a valid Chainlink feed, Chainlink could participate in the weighted price. Therefore, `oneinch_spot` is classified accurately as a composite on-chain liquidity-weighted spot aggregator that includes Chainlink in its registry, rather than an uncoupled pure DEX primitive or independent CEX benchmark.

### Pricing & Decimal Semantics
- **Method**: `getRate(address srcToken, address dstToken, bool useWrappers)`
  - `srcToken`: WETH (`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`, 18 decimals)
  - `dstToken`: USDC (`0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, 6 decimals)
  - `useWrappers`: `false` (bypasses wrapped token conversion to evaluate direct DEX liquidity)
  - Selector: `0x802431fb`
- **Liquidity-Weighted Arithmetic Mean**: The returned `rate` is the liquidity-weighted arithmetic mean of spot rates across DEX venues. It is **not** a trade execution quote (it does not model size-dependent price impact or slippage) and is **not** an off-chain CEX price.
- **Decimal Normalization**:
  The contract calculates rate as:
  $$\text{rate} = \frac{\text{rawDstUnits}}{\text{rawSrcUnits}} \times 10^{18}$$
  To obtain the price in USDC per 1 WETH:
  $$\text{Price} = \frac{\text{rate} \times 10^{\text{srcDecimals} - \text{dstDecimals}}}{10^{18}} = \frac{\text{rate} \times 10^{18 - 6}}{10^{18}} = \frac{\text{rate}}{10^6}$$
  For example, at block 23549939, `rawRate = 3781532393`, yielding a spot price of **3,781.532393 USDC/WETH**.

---

## 3. Uniswap V3 Pool Observation TWAP Semantics

### Pool Identity Verification
- **Pool Address**: `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`
- **Fee Tier**: 500 (0.05%)
- **Token 0**: USDC (`0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, 6 decimals)
- **Token 1**: WETH (`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`, 18 decimals)
- Verified against existing inventory in `swaparch` discovery (`docs/reports/uniswap-v3-top5-discovery.md` and `docs/reports/uniswap-v3-phase1.md`).

### Mathematical Formulation
Following Uniswap V3 documentation and canonical `@uniswap/v3-periphery/contracts/libraries/OracleLibrary.sol`:
1. **Geometric Time-Weighted Average**:
   The pool maintains an internal ring buffer of cumulative observations. Calling `observe([secondsAgo, 0])` yields `tickCumulatives[0]` at $(t - \Delta t)$ and `tickCumulatives[1]` at $t$.
   The time-weighted arithmetic mean of the tick is:
   $$\bar{t} = \frac{\text{tickCumulatives}[1] - \text{tickCumulatives}[0]}{\Delta t}$$
   Because price $P(t) = 1.0001^{\text{tick}(t)}$, the logarithm is $\ln P(t) = \text{tick}(t) \ln(1.0001)$. Averaging the tick arithmetically computes:
   $$1.0001^{\bar{t}} = \exp\left( \frac{1}{\Delta t} \int_{t - \Delta t}^t \ln P(\tau) d\tau \right)$$
   which is the exact **geometric time-weighted average price (TWAP)** over the duration $\Delta t$.
2. **Floor Negative Tick Division**:
   In Solidity, integer division truncates towards zero (e.g., $-5 / 2 = -2$). Canonical `OracleLibrary.consult` explicitly rounds towards negative infinity:
   ```solidity
   arithmeticMeanTick = int24(tickCumulativesDelta / secondsAgo);
   if (tickCumulativesDelta < 0 && (tickCumulativesDelta % secondsAgo != 0)) arithmeticMeanTick--;
   ```
   In Python, integer floor division `//` natively rounds toward negative infinity (`-5 // 2 == -3`), precisely replicating the canonical EVM rounding behavior.
3. **Canonical Quote Conversion (`getQuoteAtTick`)**:
   With `baseToken = WETH` (token1) and `quoteToken = USDC` (token0), the address comparison `baseToken.lower() > quoteToken.lower()` holds.
   Under `OracleLibrary.getQuoteAtTick`, for `sqrtRatioX96 <= type(uint128).max`:
   $$\text{ratioX192} = (\text{sqrtRatioX96})^2$$
   $$\text{quoteAmount} = \text{FullMath.mulDiv}(1 \ll 192, \text{baseAmount}, \text{ratioX192})$$
   For $\text{baseAmount} = 10^{18}$ (1 WETH), `quoteAmount` represents raw USDC units ($10^6$ scale). The normalized price is:
   $$\text{Price} = \frac{\text{quoteAmount}}{10^6}$$
4. **Observation Windows**:
   - **60s TWAP (`uniswap_v3_twap_60`)**: Captures short-term directional trends.
   - **300s TWAP (`uniswap_v3_twap_300`)**: Designated default comparison for the frontend, smoothing block-to-block noise.
5. **Fail-Closed Fallback Policy**:
   If an observation reverts (e.g. pool uninitialized or insufficient observation history `'OLD'`), the collector returns `price: null` with status `"reverted"` and the reason. It never falls back to spot price or a shorter window.

---

## 4. Bounded Collection & Dataset Verification

The bounded collection script [`scripts/collect_october_oracle_references.py`](file://<checkout>/scripts/collect_october_oracle_references.py) was executed across the complete 254-block dataset:
- **Block Range**: `23549939` to `23550192` (254 consecutive blocks).
- **Block Hashes**: Every block hash was verified against [`frontend/public/october-sources.json`](file://<checkout>/frontend/public/october-sources.json).
- **Execution Timing**: Initial collection completed in **61.37 seconds** over 242 Multicall3 network requests. Cached idempotent replay completed in **0.20 seconds** with 0 network requests.
- **Availability / Success**:
  - `oneinch_spot`: **254/254 (100% OK, 0 nulls)**
  - `uniswap_v3_twap_60`: **254/254 (100% OK, 0 nulls)**
  - `uniswap_v3_twap_300`: **254/254 (100% OK, 0 nulls)**

### Three Historical Pins Breakdown

| Block | Timestamp (UTC) | Chainlink (USDC) | Aave (USDC) | 1inch Spot (USDC) | UniV3 60s TWAP (USDC) | UniV3 300s TWAP (USDC) |
|---|---|---|---|---|---|---|
| **23549939** (Start) | 21:14:11 | 3,716.85 | 3,846.13 | 3,781.532393 | 3,755.554042 (tick 194010) | 3,849.466577 (tick 193763) |
| **23550094** (Trough) | 21:45:23 | 3,780.00 | 3,648.78 | 3,703.034020 | 3,550.678198 (tick 194571) | 3,521.683129 (tick 194653) |
| **23550192** (End) | 22:04:59 | 3,841.48 | 3,873.34 | 3,822.672246 | 3,893.988728 (tick 193648) | 3,881.160385 (tick 193681) |

### Key Dynamics Observed
1. **Crash Initiation (Block 23549939)**:
   As ETH rapidly drops, the 300s TWAP (3,849.47) reflects past higher prices, lagging behind the 60s TWAP (3,755.55). The 1inch Spot (3,781.53) sits between them, closely tracking aggregate instantaneous market liquidity.
2. **Crash Trough (Block 23550094)**:
   In the trough, the single-pool V3 TWAPs drop to ~3,521–3,550 USDC, while 1inch Spot remains supported at 3,703.03 USDC due to liquidity weighting across alternative venues (Curve, Uniswap V2, etc.).
3. **Recovery (Block 23550192)**:
   During price recovery, shorter-window 60s TWAP (3,893.99) leads the rebound ahead of 300s TWAP (3,881.16), while 1inch Spot stabilizes at 3,822.67 USDC.

---

## 5. Artifact & Sidecar Specification

### Public Sidecar Contract
Path: [`frontend/public/october-oracle-references.json`](file://<checkout>/frontend/public/october-oracle-references.json)
SHA256: `417f0abac379c1e688c5125b8f4dda211338a5998683560e3089d7deb6cfb6d2`

Schema definition:
```json
{
  "schemaVersion": 1,
  "sources": [
    {
      "id": "oneinch_spot",
      "label": "1inch Spot (OffchainOracle)",
      "kind": "spot",
      "description": "Liquidity-weighted DEX spot price from canonical 1inch OffchainOracle (getRate WETH/USDC, useWrappers=false)",
      "address": "0x00000000000D6FFc74A8feb35aF5827bf57f6786"
    },
    {
      "id": "uniswap_v3_twap_60",
      "label": "Uniswap V3 TWAP (60s)",
      "kind": "twap",
      "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (60-second window)",
      "windowSeconds": 60,
      "pool": "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
    },
    {
      "id": "uniswap_v3_twap_300",
      "label": "Uniswap V3 TWAP (300s)",
      "kind": "twap",
      "description": "Uniswap V3 USDC/WETH 0.05% observation geometric TWAP (300-second window, default frontend comparison)",
      "windowSeconds": 300,
      "pool": "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
    }
  ],
  "rows": [
    {
      "block": 23549939,
      "blockHash": "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
      "timestamp": "2025-10-10T21:14:11Z",
      "values": {
        "oneinch_spot": {
          "price": 3781.532393,
          "status": "ok"
        },
        "uniswap_v3_twap_60": {
          "price": 3755.554042,
          "status": "ok"
        },
        "uniswap_v3_twap_300": {
          "price": 3849.466577,
          "status": "ok"
        }
      }
    }
  ]
}
```

### Raw Evidence & Provenance
- [`outputs/october-oracle-references/raw-responses.json`](../../outputs/october-oracle-references/raw-responses.json): Retains all unrounded raw integers (`rawRate`, `tickCumulatives`, `deltaTick`, `arithmeticMeanTick`, `quoteAmountRaw`), raw hex strings, and call metadata.
- [`outputs/october-oracle-references/pins.json`](../../outputs/october-oracle-references/pins.json): Stores deployment block, bytecode hash, transaction hash, formula definitions, and references.
- [`outputs/october-oracle-references/provenance.json`](../../outputs/october-oracle-references/provenance.json): Records execution timing, SHA256 fingerprints, request counts, and explicit separation between initial network acquisition (242 requests, 61.37s) and subsequent cached replays.
- [`outputs/october-oracle-references/deployment-proof.json`](../../outputs/october-oracle-references/deployment-proof.json): Verifiable bytecode presence and transaction receipt proof at block 20535992 and the three historical pins.
- [`outputs/october-oracle-references/historical-1inch-config.json`](../../outputs/october-oracle-references/historical-1inch-config.json): Verbatim on-chain `oracles()` and `connectors()` query outputs across all three pins, plus direct Chainlink sub-oracle analysis.

---

## 6. Automated Test Suite

A dedicated regression test suite [`tests/test_october_oracle_references.py`](file://<checkout>/tests/test_october_oracle_references.py) covers all required edge cases:
1. `test_floor_negative_tick_rounding`: Compares Python floor division against a line-by-line Solidity `OracleLibrary.consult` arithmetic simulator across 14 positive and negative delta vectors.
2. `test_canonical_get_quote_at_tick_and_decimals`: Validates canonical `getQuoteAtTick` integer arithmetic at ticks 194010 and 193763 and 1inch decimal scaling.
3. `test_call_failure_and_revert_semantics_no_fallback`: Verifies fail-closed behavior on call reverts, zero rates, and malformed return payloads.
4. `test_pinned_three_blocks_independent_decode`: Decodes raw RPC responses at blocks 23549939, 23550094, and 23550192 and checks exact match.
5. `test_published_sidecar_contract_and_integrity`: End-to-end verification of all 254 blocks in `frontend/public/october-oracle-references.json` against `frontend/public/october-sources.json`.
6. `test_observe_requires_exact_array_lengths`: Enforces that `observe` returns exactly 2 cumulative and 2 liquidity entries, rejecting malformed length arrays.
7. `test_source_validation_truncated_duplicate_outofrange`: Validates fail-closed rejection of truncated rows (< 254), duplicate blocks, and out-of-range block requests.
8. `test_subset_run_isolation_does_not_clobber_full_artifacts`: Confirms that running a subset (e.g. 1 block) isolates its output to a `subset/` directory and never overwrites the published sidecar or full 254-block raw artifacts.

All 8 tests pass in 0.53 seconds with clean Ruff linting.

---

## 7. Boundaries & Limitations

- **Read-Only Scope**: This research is entirely historical and read-only. No live transactions, production changes, commits, or pushes were made.
- **Benchmark Reference Only**: All prices represent USDC per 1 WETH. They are benchmark references; no execution wrapping claims or settlement guarantees are made.
- **Frontend Isolation**: No frontend React or UI components were edited; the peer retains frontend component ownership. The public sidecar file is delivered ready for consumption.
