# Lista Stable — identity resolution

The brief listed Lista as needing deployment research, and SOURCE_DISCOVERY.md warned against substituting the BNB-chain lisUSD PSM. That warning is right, but for the wrong reason.

## Lista StableSwap IS deployed on Ethereum mainnet

Independently verified here, not just read from docs. Factory `0xF6c9ffA64bD0aE8a068dd7b7d954c654A3E7F8a6`:

| pin | `pairLength()` |
|---|---|
| 23549991 | no code |
| 23550060 | no code |
| 23728292 | no code |
| 24356381 | 6 |
| 25760917 | 6 |
| 25896003 | 8 |

Creation lies in `(23728292, 24356381]`; 24356381 is an upper bound. **Lista contributes nothing at the first three pins.**

The label is KyberSwap's exchange id `lista-stable`. The implementation is a **PancakeSwap-StableSwap (Curve-style) fork** sharing one implementation `0x86d2946BD6C807e797E8Eec0d632931Dc337DeA3` behind ERC-1967 proxies — not a lisUSD PSM, and not Fluid-ABI-compatible. **No mainnet pool contains lisUSD.** lisUSD itself, the CDP PSM and Lista's V2/V3 concentrated-liquidity DEX are genuinely BNB-only.

## Pools — all eight enumerated and read on chain at 25896003

| i | pool | pair | fee_raw | A_raw |
|---|---|---|---|---|
| 0 | `0x23072d03…` | wstETH / ETH (`0xEeee…EEeE` sentinel) | 1000000 | 5000 |
| 1 | `0x35c9a4Da…` | USDC / USDT | 10000 | 10000 |
| 2 | `0xD46Bfa2B…` | USD1 / USDT | 1000000 | 5000 |
| 3 | `0x94E4A9f2…` | WBTC / cbBTC | 1000000 | 10000 |
| 4 | `0x56a47577…` | USDe / USDT | 10000 | 5000 |
| 5 | `0xA838D405…` | USDe / USDC | 1000000 | 5000 |
| 6 | `0x0a935ea5…` | USDT / USDS | 10000 | 10000 |
| 7 | `0x2Bc918B6…` | PYUSD / USDS | 10000 | 10000 |

Pools 6 and 7 did not exist at pins 1–4 (`pairLength` was 6 at 24356381). Relevant to this study: 1, 4, 5, 6 and 0.

**Discovery** is pure registry enumeration, no logs: `pairLength()` then `swapPairContract(uint256 i)`, then `coins(0)`, `coins(1)`, `N_COINS()`, `fee()`, `A()` per pool. Re-enumerate at each pinned block — the count changes.

## Quote semantics

Trusted reference: **`get_dy(uint256 i, uint256 j, uint256 dx)` — a plain view**. Verified live at 25896003 on the USDC/USDT pool: `get_dy(0, 1, 1000000)` → `1000302`, i.e. 1 USDC → 1.000302 USDT, with balances 920,813.70 USDC / 3,756,174 USDT.

**The argument type is `uint256`, not Curve's `int128`.** Using the Curve selector reverts. Swap is `exchange(uint256,uint256,uint256,uint256)`.

`fee_raw` and `A_raw` are meaningless without their denominators. PancakeStable uses `FEE_DENOMINATOR = 1e10` and `A_PRECISION = 100`, but that is **not verified here** — read the implementation at `0x86d2946B…` before converting.

For cross-checking fills: `TokenExchange(address indexed user, uint256 i, uint256 dx, uint256 j, uint256 dy, uint256 dy_fee, uint256 dy_admin_fee)`, topic0 `0x143f1f8e861fbdeddd5b46e844b7d3ac7b86a122f36e8c463859ee6811b1f29c`. This is a **7-parameter** signature; Curve's 5-parameter `TokenExchange` is a different topic0.

**Trap.** Pool 0 holds wstETH against `0xEeee…EEeE`, the native-ETH sentinel, not WETH. The adapter must wrap/unwrap itself and must not treat the sentinel as an ERC20.

**Open.** Exact factory creation block not bisected. Fee/A denominators unconfirmed. Per-pool creation blocks unknown.
