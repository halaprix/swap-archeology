# Ekubo (EVM)

Ekubo is a singleton concentrated liquidity AMM deployed on Ethereum mainnet, featuring transient accounting (EIP-1153), modular hook-like extensions, fine-grained geometric tick spacing ($1.000001^{\text{tick}}$), and 64-bit binary fixed-point fee representation.

## Deployments and Version Chronology

Ekubo EVM has two major deployments on Ethereum mainnet:

| Contract | Version | Mainnet Address | Deployment Window | Status in Oct 2025 (`23549939..23550192`) |
|---|---|---|---|---|
| **V2 Core** | `v2.1.0` | `0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444` | April 2025 (block ~22,048,334) | **Active / Immutable** (23,751 bytes code) |
| **V2 Positions** | `v2.1.0` | `0xa37cc341634afd9e0919d334606e676dbab63e17` | April 2025 | Active |
| **V2 Router** | `v2.1.0` | `0x9995855c00494d039ab6792f18e368e530dff931` | April 2025 | Active (stateless router) |
| **V2 CoreDataFetcher** | `v2.1.0` | `0x208bb00c6b142351e4a431f6dd323691ebb7c285` | April 2025 | Active |
| **V2 QuoteDataFetcher**| `v2.1.0` | `0x91cb8a896caf5e60b1f7c4818730543f849b408c` | April 2025 | Active |
| **V3 Core** | `v3.0.0+` | `0x00000000000014aA86C5d3c41765bb24e11bd701` | Early 2026 (tagged 2025-12-31) | **Absent** (`0x` bytecode across all Oct pins) |
| **V3 Positions** | `v3.0.0+` | `0x02d9876a21af7545f8632c3af76ec90b5ad4b66d` | Early 2026 | Absent (`0x`) |
| **V3 CoreDataFetcher** | `v3.0.0+` | `0xf68f25ca6c817733b7b15a42191ae72a34d56a2b` | Early 2026 | Absent (`0x`) |
| **V3 QuoteDataFetcher**| `v3.0.0+` | `0x5a3f0f1da4ac0c4b937d5685f330704c8e8303f1` | Early 2026 | Absent (`0x`) |

### Key Historical Finding
- **Ekubo V3 did not exist during the October 10, 2025 replay window.** Verified bytecode at blocks `23549939`, `23550094`, and `23550192` is empty (`0x`).
- Any historical model or replay of October 10, 2025 MUST evaluate **Ekubo V2 only**. Modeling V3 for this window is anachronistic and invalid.

## EVM Opcode Dependencies

The execution mechanics of Ekubo rely on specific EVM instructions:

### EIP-1153: Transient Storage (`TSTORE`, `TLOAD`)
- Both V2 and V3 use `FlashAccountant.sol`, relying on transient storage to track token balance deltas across swaps within the same transaction without writing to persistent storage slots.
- Activated in the Ethereum **Dencun** hard fork (March 2024, block 19,426,587).
- Supported across all target blocks in the October 2025 study.

### EIP-7939: Count Leading Zeros (`CLZ`)
- **V3 Instruction Dependency:** V3 source code uses explicit `clz(x)` Yul instructions (`src/math/ticks.sol:118`, `src/types/bitmap.sol:55,71`, `src/math/time.sol:23`) to optimize bitmap scans and tick lookups.
- **Historical Compatibility vs Current State:**
  - In October 2025, V3 was not deployed on mainnet, and EIP-7939 was not active on Ethereum mainnet.
  - In September 2026, Ekubo V3 is live and operational on Ethereum mainnet (with active aggregator fills such as 0x `Ekubo_V3`). V3 is never asserted to be currently unusable.
- **V2 Standard EVM Compatibility:** V2 uses Solady's `FixedPointMathLib.log2` and `LibBit.fls/ffs`, maintaining standard EVM compatibility without requiring EIP-7939.

## Pool Identification and Key Derivation

Ekubo is a single-contract AMM where each pool is identified by a 32-byte hash:
```solidity
struct PoolKey {
    address token0;
    address token1;
    bytes32 config;
}

poolId = keccak256(abi.encode(token0, token1, config));
```
- Pools require canonical sorting: `uint160(token0) < uint160(token1)`.

### Native ETH vs WETH
- Ekubo defines `NATIVE_TOKEN_ADDRESS = address(0)` (`0x0000000000000000000000000000000000000000`).
- Because `address(0)` is strictly less than any valid contract address, **native ETH is always `token0`** in any ETH pair.
- WETH (`0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2`) is treated as a distinct ERC-20 token.
- **Mainnet V2 Native Pairings:** In cached historical discovery inventory, active ETH pairs on mainnet Ekubo V2 pair directly with native ETH (`address(0)`). For native ETH pairs, selling WETH requires an unwrap step (`WETH -> ETH`) before trading on Ekubo V2. Whole-chain exclusion of any WETH pair across all blocks remains unindexed.

## Pool Config Bit-Packing (V2 vs V3)

The 32-byte `config` parameter encodes the fee, extension contract address, and tick parameters. However, the bit allocation between V2 and V3 diverges critically:

### Common Upper Bits (Bits 255..32)
- **Bits 255..96 (160 bits):** `extension` contract address. If `0x0`, the pool is hookless.
- **Bits 95..32 (64 bits):** `fee` as a uint64 binary fraction of $2^{64}$.

### Lower 32 Bits (Bits 31..0): Divergence
- **V2 Layout:**
  - Bits 31..0: `tickSpacing` as a 32-bit unsigned integer (`uint32`).
  - `config_v2 = (uint256(uint160(extension)) << 96) | (uint256(fee) << 32) | uint256(tick_spacing)`
  - Bit 31 is **never set** for standard tick spacings.

- **V3 Layout:**
  - Bit 31: **Discriminator bit**.
    - If `bit 31 == 1`: Concentrated liquidity pool. Bits 30..0 represent `tickSpacing` (`uint31`).
    - If `bit 31 == 0` and bits 30..0 != 0: Stableswap pool. Bits 30..24 (7 bits) = amplification parameter $A$; bits 23..0 (signed int24) = `centerTick / 16`.
    - If bits 31..0 == 0: Full-range pool.

> [!WARNING]
> Decoding V2 pool configurations with a V3 parser misidentifies concentrated pools as stableswap or full-range pools (because bit 31 is 0). Conversely, decoding V3 configurations with a V2 parser introduces a spurious $2^{31}$ (2,147,483,648) offset to the tick spacing.

## Fee Representation and Math

Fees are stored as a `uint64` binary fraction of $2^{64} = 18,446,744,073,709,551,616$, not basis points or ppm:
$$\text{fee\_raw} = \left\lfloor \frac{\text{fee\_bps} \times 2^{64}}{10,000} \right\rfloor$$

$$\text{fee\_bps} = \frac{\text{fee\_raw} \times 10,000}{2^{64}}$$

Canonical mainnet values:
- **30 bps (0.30%):** `55340232221128654`
- **5 bps (0.05%):** `9223372036854775`
- **1 bps (0.01%):** `1844674407370955`
- **0.5 bps (0.005%):** `922337203685477`
- **0.25 bps (0.0025%):** `461168601842738`
- **0.1 bps (0.001%):** `184467440737095`

## Price Representation and Tick Math

- `SqrtRatio` is a **`uint96`**, not a Uniswap V3 Q64.96 (`uint160`).
  - Bits 95..94 (2 bits): Scale factor.
  - Bits 93..0 (94 bits): Mantissa.
- **Tick Scale:** Ekubo uses a fine base ratio:
  $$\text{price} = 1.000001^{\text{tick}}$$
  $$\sqrt{\text{price}} = 1.000001^{\text{tick} / 2}$$
  Because $1.000001$ is 100x finer than Uniswap V3's $1.0001$, active ticks have magnitudes in the millions (e.g., tick $-20,054,258$ for ETH/USDC).

## Extension Architecture

Extensions receive callbacks during pool actions. The call points are encoded directly into the highest byte of the extension address:
```solidity
uint8 flags = uint8(uint160(extension) >> 152);
```
Bit flags trigger hooks:
- `AFTER_INITIALIZE` (`0x01`)
- `BEFORE_SWAP` (`0x02`)
- `AFTER_SWAP` (`0x04`)
- `BEFORE_MODIFY_POSITION` (`0x08`)
- `AFTER_MODIFY_POSITION` (`0x10`)

Mainnet V2 Extension Deployments:
- **MEVResist:** `0x553a2EFc570c9e104942cEC6aC1c18118e54C091` (top byte `0x55` = flags `01010101`b)
- **Oracle:** `0x51d02A5948496a67827242EaBc5725531342527C` (top byte `0x51` = flags `01010001`b)
- **TWAMM:** `0xd4279c050da1f5c5b2830558c7a08e57e12b54ec` (top byte `0xd4` = flags `11010100`b)

Hookless pools have `extension == address(0)`.

## On-Chain Data Fetching

1. **`CoreDataFetcher.poolState((address,address,bytes32))`**
   - Address: `0x208bb00c6b142351e4a431f6dd323691ebb7c285`
   - Returns: `(uint96 sqrtRatio, int32 tick, uint128 liquidity)`
   - Used to snapshot pool state in a single multicall.

2. **`QuoteDataFetcher.getQuoteData((address,address,bytes32)[], uint32)`**
   - Address: `0x91cb8a896caf5e60b1f7c4818730543f849b408c`
   - Returns: `(int32 tick, uint96 sqrtRatio, uint128 liquidity, int32 minTick, int32 maxTick, (int32 tick, int128 liquidityDelta)[])[]`
   - Scans bitmap words to return initialized tick ranges and liquidity deltas.

3. **Direct Storage Reads (`ExposedStorage`)**
   - `Core.sload(bytes32 key)` allows direct inspection of transient or permanent slots.

## 0x API Venue Disambiguation

The 0x Swap API v2 references two distinct venue labels:
- **`Ekubo`:** Routes to Ekubo V2 Core (`0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444`). Proven in `0x-settler` (`src/core/EkuboV2.sol:41`, commit `1df908742d38cf407f667df6518dae6e04a01ac3`).
- **`Ekubo_V3`:** Routes to Ekubo V3 Core (`0x00000000000014aA86C5d3c41765bb24e11bd701`). Proven in `0x-settler` (`src/core/EkuboV3.sol:44`).

In 0x responses, `route.fills` contains the venue label but omits pool configuration, fee tier, and tick spacing. For the October 10, 2025 study, `Ekubo_V3` could not have provided any liquidity because V3 was uncreated on mainnet (`0x` bytecode). In contrast, in September 2026, 0x actively fills swaps via `Ekubo_V3`.

## The `waEthWETH` Routing Context

- During the October 10, 2025 254-block crash window, zero `PoolInitialized` events were emitted on Ekubo V2 Core, and no `waEthWETH` (`0x0bfc9d54Fc184518A81162F8fB99c2eACa081202`) or `waEthLidoWETH` (`0x0FE906e030a44eF24CA8c7dC7B7c53A6C4F00ce9`) pools exist in cached historical discovery inventory.
- Confirmed primary execution venue for `waEthWETH` during the crash window is **Balancer V3**, not Ekubo. Whole-chain Ekubo pool creation registry remains unindexed.

## Official SDKs and Orrery Reference

- **Submodule:** `<external-repos>/ekubo-evm-contracts` pinned at release tag `v3.2.0` (`de94c77a665c54f4c848185872b8a55b2c28c1bc`), indexed with CodeGraph.
- **Rust SDK:** `crates.io/crates/ekubo_sdk` (with `features = ["evm"]`).
- **TypeScript SDK:** `@ekubo/sdk` (pure math, ticks, fees, and square roots).
- **Router SDK:** `@ekubo/yul-router-sdk`.
