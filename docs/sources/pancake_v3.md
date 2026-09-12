# PancakeSwap V3 — Source Semantics and Integration Reference

Historical research and adapter specification for PancakeSwap V3 on Ethereum mainnet,
calibrated for the October 10 2025 ETH/WETH sell→USDC interval (blocks 23549939..23550192).

## 1. Core Deployments

| Component | Ethereum Mainnet Address | Role |
|---|---|---|
| **Factory** | `0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865` | Pool registry and deployment controller |
| **PoolDeployer** | `0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9` | Deploys `PancakeV3Pool` bytecode via `parameters()` |
| **QuoterV2** | `0xb048bbc1ee6b733fffcfb9e9cef7375518e25997` | Simulates exact-input and exact-output quotes on chain |
| **SwapRouter** | `0x1b81d678ffb9c0263b24a97847620c99d213eb14` | Canonical single and multi-hop router |
| **TickLens** | `0x9a489505a00ce272eaa5e07dba6491314cae3796` | Bulk populated-tick reader |
| **MasterChefV3** | `0x556b9306565093c855aea9ae92a594704c2cd59e` | Staking and liquidity mining controller |

Canonical source repository: [pancakeswap/pancake-v3-contracts](https://github.com/pancakeswap/pancake-v3-contracts).
Official address registry: [developer.pancakeswap.finance/contracts/v3/addresses](https://developer.pancakeswap.finance/contracts/v3/addresses).

## 2. Source Semantics vs Uniswap V3

While PancakeSwap V3 is derived from Uniswap V3, critical architectural and semantic
differences must be accounted for by the adapter:

### 2.1 `slot0()` Tuple and Protocol Fee Packing
- **PancakeSwap V3**:
  ```solidity
  function slot0() external view returns (
      uint160 sqrtPriceX96,
      int24 tick,
      uint16 observationIndex,
      uint16 observationCardinality,
      uint16 observationCardinalityNext,
      uint32 feeProtocol,
      bool unlocked
  );
  ```
  `feeProtocol` is **`uint32`**, packing two 16-bit values:
  `feeProtocol0 = feeProtocol % 65536` (lower 16 bits) and `feeProtocol1 = feeProtocol >> 16` (upper 16 bits).
  The default factory initialization sets `feeProtocol = 209718400` (`3200 + (3200 << 16)`), representing
  a 32.00% protocol fee cut (`3200 / 10000`).
- **Uniswap V3**:
  `feeProtocol` is **`uint8`**, storing a denominator (e.g. 4 for 1/4th fee).
- **Decoder Impact**:
  Attempting to decode a PancakeSwap V3 `slot0()` response using Uniswap V3's `uint8` tuple type
  raises `eth_abi.exceptions.NonEmptyPaddingBytes` / `DecodingError` because the high bytes
  of `uint32` are non-zero. The adapter must explicitly use `PANCAKE_SLOT0_TYPES`.

### 2.2 Canonical Fee Tiers and Tick Spacings
PancakeSwap V3 uses 4 canonical fee tiers enabled at factory construction:
| Fee (pips) | Fee Percentage | Tick Spacing | Uniswap V3 Equivalent |
|---|---|---|---|
| `100` | 0.01% (1 bp) | `1` | 100 (tickSpacing 1) |
| `500` | 0.05% (5 bps) | `10` | 500 (tickSpacing 10) |
| **`2500`** | **0.25% (25 bps)** | **`50`** | **None (UniV3 uses 3000 / tickSpacing 60)** |
| `10000` | 1.00% (100 bps) | `200` | 10000 (tickSpacing 200) |

The medium tier `2500` with `tickSpacing = 50` is a distinct PancakeSwap convention.
Ticks must be multiples of 50, and tick bitmap compression uses `tick // 50`.

### 2.3 Protocol Fee Impact on Swap Output
In `PancakeV3Pool.swap`:
```solidity
if (cache.feeProtocol > 0) {
    uint256 delta = (step.feeAmount.mul(cache.feeProtocol)) / PROTOCOL_FEE_DENOMINATOR;
    step.feeAmount -= delta;
    state.protocolFee += uint128(delta);
}
```
The protocol fee is subtracted from `step.feeAmount` before crediting `feeGrowthGlobalX128` to LPs.
The trader pays `step.amountIn + step.feeAmount` regardless of protocol fee settings.
Protocol fees do **not** reduce the amount out received by the trader.

### 2.4 Liquidity Mining Hooks (`lmPool`)
`PancakeV3Pool` holds an `IPancakeV3LmPool public lmPool` pointer.
During swaps, the pool invokes `lmPool.accumulateReward(blockTimestamp)` and `lmPool.crossLmTick(step.tickNext, zeroForOne)`.
These calls maintain reward accounting for MasterChef V3 farm staking. They do not
affect token balances, reserves, price movement, or swap execution math.
In offline quote evaluation, `lmPool` is entirely passive.

### 2.5 Swap Price Math Reuse
The pricing arithmetic in PancakeSwap V3 (`computeSwapStep`, `getSqrtRatioAtTick`,
`getTickAtSqrtRatio`, `getNextSqrtPriceFromInput/Output`, `getAmount0/1Delta`) is
mathematically and bit-for-bit identical to Uniswap V3.
The adapter reuses `swaparch.adapters.uniswap_v3.math` directly, eliminating redundant code.

### 2.6 QuoterV2 Partial Fills vs Full Input Consumption
`QuoterV2.quoteExactInputSingle` executes an optimistic swap in a revert-based simulation.
When a pool has insufficient liquidity to absorb the requested input size, `PancakeV3Pool.swap`
stops at the price limit (`MIN_SQRT_RATIO + 1` or `MAX_SQRT_RATIO - 1`) and consumes only a partial
fraction of the input.
`pancakeV3SwapCallback` in `QuoterV2` reverts with `amountReceived` for whatever partial fill occurred,
**without** checking if `amountToPay == params.amountIn`.
As a result:
- A thin pool (e.g. WETH/USDC 2500) will report a nominal quote of e.g. 2,972 USDC for 1 WETH.
- Dividing nominal output by requested input suggests an absurd price ($0.002972 / ETH), when in fact
  the pool simply ran out of liquidity after consuming a few hundred wei.
- `PancakeV3State` strictly checks `if remaining != 0: raise Unsupported(...)`, ensuring
  that partial fills are never counted as full-fill execution liquidity.

## 3. Read Plan

- **Phase 1 (`read_requests`)**:
  - `slot0()`: `0x3850c7bd` → `(uint160, int24, uint16, uint16, uint16, uint32, bool)`
  - `liquidity()`: `0x1a686502` → `uint128`
  - `fee()`: `0xddca3f43` → `uint24`
  - `tickSpacing()`: `0xd0c93a7c` → `int24`
  - `token0()`: `0x0dfe1681` → `address`
  - `token1()`: `0xd21220a7` → `address`
  - Optional: if `tick_hint` and `tick_spacing` are provided in `PoolRecord.config`,
    pre-emits bitmap words in window `[centre - radius, centre + radius]`.
- **Phase 2 (`dependent_requests`, pass 1)**:
  - Decodes `slot0` and `tickSpacing`
  - Emits missing `tickBitmap(int16)` words for `[centre - radius, centre + radius]` (default radius = 8 → 17 words).
- **Phase 3 (`dependent_requests`, pass 2)**:
  - Decodes loaded bitmap words, locates initialized ticks
  - Emits `ticks(int24)` for all initialized ticks in the window.
- **Phase 4**: Returns `[]` (stop condition).

## 4. Discovery Overlay

Discovered pools are keyed by `pool_id = f"pancake_v3:{PANCAKE_FACTORY}:{pool_address}"`.
Pools are registered with tokens, fee tiers, tick spacings, created blocks, and support statuses.
Candidate pools compete in the unified routing universe with independent capacity IDs.
