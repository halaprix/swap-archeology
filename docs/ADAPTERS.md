# Adapter support

Status reviewed 2026-09-12 against `implemented_adapters()` in
`src/swaparch/universe.py` and the reports linked below. “Implemented” means a
bounded research quote model, not universal protocol coverage or funded settlement
validation. Admission is specific to chain, deployment, block hash and loaded state.

| Registered family | Supported model | Limits and recorded validation |
| --- | --- | --- |
| Uniswap V2 | Standard constant-product pairs | Standard-transfer tokens; 208 quotes/416 raw references at five pins. [Review](reports/v2-review.md) |
| Uniswap V3 | Concentrated liquidity with integer tick traversal | Only loaded tick coverage; rejects incomplete fills. [Review](reports/review-uniswap-v3.md) |
| Uniswap V4 | Hookless, static-fee pools | Hooks/dynamic fees excluded; 1,190 recorded exact Quoter checks. [Review](reports/fluid-v4-source-review.md) |
| PancakeSwap V3 | V3 math with Pancake identity and ABI | Three qualified October pools; 21 exact matches, six refusals across candidate checks. Coverage varies by amount. [Report](reports/pancake-v3-adapter.md) |
| Fluid | DEX T1 bounded quote model | Single-use model; other variants and reuse excluded. 562 recorded exact positives. [Review](reports/fluid-v4-source-review.md) |
| Curve | Selected plain StableSwap NG pools and legacy 3pool | Not general metapool, rebasing or oracle-dependent support. [NG review](reports/curve-ng-review.md), [3pool integration](reports/october-stablecoin-connectors.md) |
| Maker/Sky | Finite LitePSM plus DAI/USDS converter | Fees, inventory and rounding matter; no unlimited peg. Converter shares the existing PSM route; wrapper is not a competing duplicate reserve. [PSM review](reports/litepsm-review.md), [USDS](reports/usds-connectors.md) |
| Lido | stETH/wstETH conversion | Bounded wrapper model; no asynchronous withdrawal/staking execution. [Review](reports/lido-review.md) |
| Origin ARM | Historical Lido ARM profiles | Rates/capacity qualified from versioned source and getters; not funded settlement proof. Ethena ARM excluded. [Review](reports/origin-arm-review.md) |

## Not supported as active routing adapters

| Source | Remaining work or exclusion |
| --- | --- |
| Origin Ethena ARM | Implementation/deployment research exists; quote adapter and capacity qualification unfinished. Absent during October 2025. |
| Ekubo | Historical V2 adapter/tick gap; researched V3 deployment unavailable in October. |
| Balancer V3 | Discovery exists; pricing adapter and shared-buffer accounting missing. |
| Lista Stable | Getter spot-check only; no integrated pricing adapter. |
| Spark | No separately qualified adapter/source interpretation. |
| Generic ERC-4626 | No general adapter; explicit supported connectors do not imply arbitrary-vault support. |
| Bebop | Cancelled experiment; draft module is not registered or supported. |
| 0x RFQ | Current indicative source discovery exists; historical executable offers cannot be reconstructed from fills alone. |

WBTC and USDS are tokens, not adapter families: the October expansion added
12 WBTC V3 connector pools, four qualified USDS V3 pools, and DAI/USDS conversion.
A discovered pool is not necessarily qualified at every block or amount.

## Keep three measurements separate

- **Adapters** simulate a proposed amount against pinned state.
- **Oracle collectors** read benchmark values (Chainlink/Aave, 1inch spot and V3 TWAP).
- **Swap-event analysis** measures observed pool-leg executions and buy/sell VWAP.

An event is not an available quote, a pool leg is not a user's net trade, and an
oracle answer is not a promise of liquidity. Shared-resource evaluation and bounded
search still have stated limits; no global-optimality claim is made.
