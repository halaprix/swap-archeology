# PancakeSwap V3 Source Adapter Report

## 1. Executive Summary & Verification
- **Goal**: Refactor PancakeSwap V3 adapter to reuse `UniV3State` and shared math while preserving Pancake-specific identity, `uint32 feeProtocol` decoder, and public interfaces.
- **Verification Commands & Results**:
  - `pytest tests/test_pancake_v3*.py tests/test_uniswap_v3*.py` -> **51 passed in 0.87s**
  - `ruff check src/swaparch/adapters/pancake_v3.py tests/test_pancake_v3*.py` -> **Clean (0 errors)**
  - Replay of saved raw quote responses: 27 comparison points evaluated (21 exact matches, 6 strict refusals).

## 2. Scoped Pool & Size Qualification
9 discovered pools != 9 fully-qualified at all trade sizes. Qualification across 3 pinned blocks (23549939, 23550094, 23550192) evaluates candidate pools against on-chain QuoterV2 (`0xb048bbc1ee6b733fffcfb9e9cef7375518e25997`):
- **WETH/USDC 500** (`0x1ac1a8feaaea1900c4166deeed0c11cc10669d36`): Fully qualified at 1, 10, 100 WETH (exact bit-for-bit match).
- **WETH/USDT 500** (`0x6ca298d2983ab03aa1da7679389d955a4efee15c`): Qualified at 1, 10 WETH (exact match); 100 WETH strictly rejected with `Unsupported` (exceeds loaded tick window).
- **WETH/WBTC 2500** (`0x9b5699d18dff51fc65fb8ad6f70d93287c36349f`): Qualified at 1, 10 WETH (exact match).
- **WETH/USDC 2500 (thin)** (`0x19ac5f80ec17497d0e585b953100e6d18c330040`): 1 WETH strictly rejected with `Unsupported` (partial fill refused).
- **Aggregate**: 27 evaluations -> 21 exact matches, 6 strict `Unsupported` protections. 71 network calls preserved in `qualification-evidence.json`.

## 3. Semantics, Math Reuse & Hook Analysis
- **State Reuse**: `PancakeV3State` subclasses `UniV3State`, eliminating the 150+ line duplicated swap loop while keeping `fee_protocol` and `record` identity intact via dataclass `replace()`.
- **`slot0` Decoder**: Uses `uint32 feeProtocol` (index 5) rather than UniV3 `uint8`. Protocol fees deduct from LP fee growth; trader output is unaltered.
- **Tiers & Spacings**: Enforces Pancake-specific 2500 tier (`tickSpacing = 50`) alongside 100 (1), 500 (10), 10000 (200).
- **Partial Fill Protection**: QuoterV2 returns partial fills without warning. `PancakeV3State` validates full input consumption and raises `Unsupported` when `remaining != 0`.
- **LM Pool Hooks (`lmPool`)**: `IPancakeV3LmPool` updates MasterChef V3 farm rewards. Quote and pricing math is completely unaffected; settlement would fail only if hook contract execution reverts.

## 4. Task-Owned Files
- `src/swaparch/adapters/pancake_v3.py`: Minimal refactored adapter (355 lines, reuses `UniV3State`).
- `tests/test_pancake_v3_semantics.py`: Unit tests for inheritance, slot0 typing, fee tiers, LM hooks, partial fill guard.
- `tests/test_pancake_v3_historical.py`: Offline replay test suite asserting exact matches and aggregate counts.
- `scripts/pancake_v3_discovery.py` & `scripts/pancake_v3_qualify.py`: Discovery & qualification runners.
- `docs/sources/pancake_v3.md`: Detailed protocol and architectural reference.
- `outputs/source-expansion/pancake/`: Preserved evidence (`discovery.json`, `qualification-evidence.json`, RPC cache).

## 5. Proposed Integration Patch (No Shared Edits Made)
```python
# Proposed addition in src/swaparch/adapters/__init__.py
from .pancake_v3 import PancakeV3Adapter, PancakeV3State

# In universe registry / source dispatcher:
adapters["pancake_v3"] = PancakeV3Adapter()
```
*Note: If unifying adapter base classes in the future, `UniswapV3Adapter` could accept a `slot0_types` override and tag prefix, allowing `PancakeV3Adapter` to inherit multicall orchestration as well.*
