# Ekubo EVM Historical Research and Adapter Specification

## Executive Summary

This report documents the historical qualification, source discovery, opcode compatibility, mathematical specification, and adapter status for **Ekubo EVM** on Ethereum mainnet, specifically evaluated for the historical study window of **October 10, 2025, 21:14–22:05 UTC (blocks 23549939..23550192)**.

### Key Findings & Corrected Scoping
1. **Ekubo V3 Historical Uncreated Status vs Current Live Status:**
   - **October 10, 2025:** Ekubo V3 Core (`0x00000000000014aA86C5d3c41765bb24e11bd701`) was not yet deployed on Ethereum mainnet; verified on-chain bytecode across all reference pins (`23549939`, `23550094`, `23550192`) is empty (`0x`). EIP-7939 (`CLZ`) was not activated on Ethereum mainnet during this window. Upstream release tag `v3.0.0` was tagged on December 31, 2025, and V3 core was deployed post-block 24,134,506 (early 2026). Any historical replay of October 10, 2025 must strictly evaluate Ekubo V2.
   - **September 2026 (Current State):** Ekubo V3 is live on Ethereum mainnet and actively filled by aggregators including 0x Swap API v2 (`Ekubo_V3`). Current usability on mainnet is verified; V3 is never asserted to be currently unusable.
2. **Ekubo V2 Mainnet Qualification:** Ekubo V2 was live and deployed at core `0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444` (bytecode length: 23,751 bytes / 47,502 hex characters) during the October 10, 2025 window.
3. **Crash Window Event Scan Scope:** During the 254-block crash window `23549939..23550192`, zero `PoolInitialized` events were emitted. This scan only bounds *new* pool creation during those 51 minutes; it does not discover pre-existing pools. Pre-existing inventory from cached logs (`data.local-backup/discovery/1/ekubo.json`) records 74 V2 pools initialized prior to block 23549939, including pools with active extensions (`0x514d...`, `0x51d0...`).
4. **Sampled Candidate Pool State & Liquidity Distinction:** A sample of 9 guessed hookless candidate pools (covering common fee tiers 30, 5, 1, 0.5, 0.25, 0.1 bps and standard tick spacings) was probed at 3 discrete pins (`23549939`, `23550094`, `23550192`). While all 9 candidate pools returned `liquidity == 0`, this value represents only **active in-range liquidity at the current tick**. Zero active liquidity at the current tick does not establish zero swappable liquidity across other ticks in the order book, nor does it establish zero liquidity across the wider unprobed V2 pool universe.
5. **Token Pairing & WETH Scope:** Ekubo native pairs use `address(0)` (native ETH). While no WETH pools appear in the cached historical inventory, protocol-wide absence across all history has not been exhaustively scanned. For native ETH pairs, a WETH sell requires unwrapping (`WETH -> ETH`) before trading on Ekubo V2.
6. **`waEth` Routing Context:** No pools containing `waEthWETH` (`0x0bfc9d54Fc184518A81162F8fB99c2eACa081202`) or `waEthLidoWETH` (`0x0FE906e030a44eF24CA8c7dC7B7c53A6C4F00ce9`) were initialized during the 254-block crash window or observed in cached discovery inventory. The confirmed active execution venue for `waEthWETH` during the crash window was Balancer V3; whole-chain Ekubo pool creation logs remain unindexed.
7. **Status in Routing Universe:** Ekubo V2 is retained as an **unresolved inventory / adapter gap** rather than dismissed as having zero liquidity.

---

## 1. Version Chronology and Mainnet Deployment Qualification

Ekubo EVM deployment timeline and bytecode presence:

| Parameter | Ekubo V2 (`v2.1.0`) | Ekubo V3 (`v3.0.0+` / `v3.2.0`) |
|---|---|---|
| **Core Contract** | `0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444` | `0x00000000000014aA86C5d3c41765bb24e11bd701` |
| **Positions Contract** | `0xa37cc341634afd9e0919d334606e676dbab63e17` | `0x02d9876a21af7545f8632c3af76ec90b5ad4b66d` |
| **Router (Stateless)** | `0x9995855c00494d039ab6792f18e368e530dff931` | Built into Yul Router / per-route |
| **CoreDataFetcher** | `0x208bb00c6b142351e4a431f6dd323691ebb7c285` | `0xf68f25ca6c817733b7b15a42191ae72a34d56a2b` |
| **QuoteDataFetcher** | `0x91cb8a896caf5e60b1f7c4818730543f849b408c` | `0x5a3f0f1da4ac0c4b937d5685f330704c8e8303f1` |
| **Release Tag / Commit** | `v2.1.0` (`79ae633c5b8`) | `v3.0.0` (2025-12-31), `v3.2.0` (`de94c77a665c...`) |
| **Mainnet Status in Oct 2025** | Live (deployed ~block 22,048,334, April 2025) | Absent (`0x` bytecode across blocks 23549939..23550192) |
| **Mainnet Status in Sep 2026** | Immutable historical singleton | Active / Live singleton, actively routed by 0x |
| **Bytecode at Block 23549939** | `0x6103006040526004...` (23,751 bytes) | `0x` (Uncreated) |
| **Bytecode at Block 23550094** | `0x6103006040526004...` (23,751 bytes) | `0x` (Uncreated) |
| **Bytecode at Block 23550192** | `0x6103006040526004...` (23,751 bytes) | `0x` (Uncreated) |

---

## 2. EVM Opcode Dependencies

### Transient Storage (EIP-1153: `TSTORE`, `TLOAD`)
- Both V2 and V3 implement `FlashAccountant.sol` using transient storage to track token debit/credit balances across flash-accounting swaps.
- Transient storage was activated on Ethereum mainnet in the **Dencun** hard fork (block 19,426,587, March 2024), making it fully available during the October 10, 2025 window.

### Count Leading Zeros (EIP-7939: `CLZ`)
- **V3 Implementation:** V3 introduces inline assembly calls to the proposed `clz` instruction (`src/math/ticks.sol:118`, `src/types/bitmap.sol:55, 71`, `src/math/time.sol:23`).
- **Historical vs Current State:**
  - In October 2025, V3 was not deployed on mainnet, and EIP-7939 was not active on Ethereum mainnet.
  - In September 2026, Ekubo V3 is live and operational on Ethereum mainnet.
- **V2 Implementation:** V2 does **not** rely on `CLZ`. It uses Solady's `FixedPointMathLib.log2` and `LibBit.fls/ffs`, maintaining standard EVM compatibility without requiring EIP-7939.

---

## 3. 0x API Venue Label Disambiguation

0x Swap API v2 references two venue labels for Ekubo:
- **`Ekubo`:** Routes to Ekubo V2 Core (`0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444`).
- **`Ekubo_V3`:** Routes to Ekubo V3 Core (`0x00000000000014aA86C5d3c41765bb24e11bd701`).

### Canonical Code Provenance in `0x-settler`
Verified in Orrery repository clone `<external-repos>/0x-settler` (commit `1df908742d38cf407f667df6518dae6e04a01ac3`):
- `src/core/EkuboV2.sol:41`: `IEkuboCore constant CORE = IEkuboCore(0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444);`
- `src/core/EkuboV3.sol:44`: `IEkuboCore constant CORE = IEkuboCore(0x00000000000014aA86C5d3c41765bb24e11bd701);`
- `src/chains/Mainnet/Common.sol:114-118`:
  - `action == uint32(ISettlerActions.EKUBO.selector)` dispatches `sellToEkuboV2(...)`.
  - `action == uint32(ISettlerActions.EKUBOV3.selector)` dispatches `sellToEkuboV3(...)`.

### Release Dates vs Live Mainnet Deployment
- **Git Commit History:** Initial Ekubo Settler integration was authored in April 2025 (commit `056a8f4a`, 2025-04-07). Support was restructured and re-enabled alongside V3 in January 2026 (commits `53b96034`, `3f58ed39`).
- **Deployment Distinction:** Git commit dates bound code existence, not exact mainnet contract feature deployment. Enabling feature actions in 0x Settler on mainnet depends on Settler's deployer registry (`0x00000000000004533Fe15556B1E086BB1A72cEae`). In the absence of an executed historical calldata fill or deployer registration proof at block 23549939, live availability of the `EKUBO` action during the crash window is treated as an inference bounded by code existence. In contrast, current September 2026 Settler instances actively route `EKUBOV3`.

---

## 4. Historical On-Chain Observations at October 10, 2025 Pins

### Precise Sample Coverage
The bounded probe queried **9 guessed candidate pool keys** across **3 discrete pin blocks**:
- **Pin 23549939** (Start): `0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12`
- **Pin 23550094** (Stress): `0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d`
- **Pin 23550192** (End): `0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c`

### Sampled Candidate Pool States (Guessed Candidates)
| Candidate Pool | Tokens (`token0 / token1`) | Fee Tier | Tick Spacing | Initialized | Current Tick | `sqrtRatio` | Active Liquidity at Tick |
|---|---|---|---|---|---|---|---|
| **ETH/USDC 30bps** | `address(0)` / USDC | 30.0 bps | 5,982 | Yes | -20,054,258 | $1.98079 \times 10^{28}$ | **0** |
| **ETH/USDC 5bps** | `address(0)` / USDC | 5.0 bps | 1,000 | Yes | 88,722,835 | $7.92276 \times 10^{28}$ | **0** |
| **ETH/USDC 5bps (FR)** | `address(0)` / USDC | 5.0 bps | 0 (full range) | Yes | -19,827,021 | $1.98080 \times 10^{28}$ | **0** |
| **USDC/USDT 0.5bps** | USDC / USDT | 0.5 bps | 100 | Yes | 346 | $3.96140 \times 10^{28}$ | **0** |
| **USDC/USDT 0.25bps** | USDC / USDT | 0.25 bps | 50 | Yes | -289 | $3.96112 \times 10^{28}$ | **0** |
| **USDC/USDT 0.1bps** | USDC / USDT | 0.1 bps | 50 | Yes | -208 | $3.96120 \times 10^{28}$ | **0** |
| **ETH/USDT 30bps** | `address(0)` / USDT | 30.0 bps | 5,982 | No | 0 | 0 | **0** |
| **ETH/wstETH 1bps** | `address(0)` / wstETH | 1.0 bps | 200 | Yes | -186,251 | $3.78528 \times 10^{28}$ | **0** |
| **DAI/USDC 0.5bps** | DAI / USDC | 0.5 bps | 100 | No | 0 | 0 | **0** |

### Event Log Scan Bounds
- Query: `eth_getLogs` for topic0 `PoolInitialized` on V2 Core (`0xe0e0e0...`) over `23549939..23550192`.
- Result: Exactly 0 logs found inside this 254-block window.
- Scope Limitation: This confirms only that no *new* pools were initialized during the 51-minute window. It does not bound pre-existing pools.
- Cached Discovery Comparison: Prior cached whole-chain log index (`data.local-backup/discovery/1/ekubo.json`) shows 74 V2 pools initialized prior to block 23549939, including extension-governed pools.

---

## 5. Canonical Lens ABI Verification and Liquidity Semantics

Canonical V2 contracts were verified via CodeGraph and source inspection in Orrery at tag `v2.1.0` (`ekubo-evm-contracts`):

### 5.1 `CoreDataFetcher` ABI
- Address: `0x208bb00c6b142351e4a431f6dd323691ebb7c285`
- Function: `poolState(PoolKey memory poolKey) public view returns (SqrtRatio sqrtRatio, int32 tick, uint128 liquidity)`
- Raw Decoded Fields:
  - `sqrtRatio` (`uint96`): dynamic fixed-point square root price.
  - `tick` (`int32`): current price tick index ($1.000001^{\text{tick}}$).
  - `liquidity` (`uint128`): active in-range liquidity $L$ at the current tick.

### 5.2 `QuoteDataFetcher` ABI
- Address: `0x91cb8a896caf5e60b1f7c4818730543f849b408c`
- Function: `getQuoteData(PoolKey[] calldata poolKeys, uint32 minBitmapsSearched) external view returns (QuoteData[] memory results)`
- Struct:
  ```solidity
  struct QuoteData {
      int32 tick;
      SqrtRatio sqrtRatio;
      uint128 liquidity;
      int32 minTick;
      int32 maxTick;
      TickDelta[] ticks; // (int32 tick, int128 liquidityDelta)[]
  }
  ```

### 5.3 Active vs Swappable Liquidity
- In concentrated liquidity AMMs, `liquidity == 0` returned by `poolState` signifies that no positions currently straddle `current_tick`.
- It does **not** prove zero swappable liquidity across the pool; if resting orders exist at other tick boundaries (represented in `QuoteData.ticks`), incoming volume that crosses ticks will encounter swappable liquidity.
- Therefore, probing active liquidity at current tick across 9 guessed pools does not establish an absence of swappable liquidity in V2.

---

## 6. Mathematical Specification & Config Packing

### 6.1 Pool Key Derivation
$$\text{poolId} = \text{keccak256}(\text{abi.encode}(token_0, token_1, config))$$
Where `token0 < token1` and `NATIVE_TOKEN = address(0)`.

### 6.2 Config Packing (V2 vs V3)
- **V2 Config (`bytes32`):**
  $$\text{config}_{\text{v2}} = (\text{extension} \ll 96) \mid (\text{fee} \ll 32) \mid \text{tickSpacing}$$
  Bit 31 is never set for valid V2 tick spacings.
- **V3 Config (`bytes32`):**
  Bit 31 is a discriminator bit. Bit 31 = 1 indicates concentrated pools (`tickSpacing = lower32 & 0x7FFFFFFF`). Bit 31 = 0 indicates stableswap or full-range pools.

### 6.3 Fee Math (64-Bit Binary Fraction)
$$\text{fee\_raw} = \left\lfloor \frac{\text{fee\_bps} \times 2^{64}}{10,000} \right\rfloor$$
Canonical constants:
- 30 bps $\rightarrow 55,340,232,221,128,654$
- 5 bps $\rightarrow 9,223,372,036,854,775$
- 1 bps $\rightarrow 1,844,674,407,370,955$

---

## 7. Resolution & Acceptance Boundary

| Component | Status | Acceptance Justification / Blocker |
|---|---|---|
| **Ekubo V3** | **Excluded (Historical)** | Uncreated at Oct 10, 2025 pins (`0x` bytecode). Correctly separated from live Sep 2026 status. |
| **Ekubo V2 Core** | **Qualified** | Bytecode confirmed live on mainnet at Oct 10, 2025 pins. |
| **V2 Lens ABI** | **Verified** | Verified against canonical `v2.1.0` source in Orrery via CodeGraph. |
| **0x Route Mapping** | **Verified (Code)** | Canonical mapping `EKUBO` $\rightarrow$ V2 Core proven in `0x-settler` source. |
| **V2 Liquidity Adapter** | **Unresolved Gap** | Pre-crash V2 pool inventory and tick order books unindexed; retained as adapter gap. |

