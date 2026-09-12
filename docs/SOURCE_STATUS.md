# Source status — phase 1 inventory

Owner: source-inventory worker. Companion files: `docs/sources/<family>.md` (one per family, quote semantics), `data/discovery/1/<family>.json` (machine-readable records), `data/discovery-evidence/` (every raw RPC response relied on).

Pins: **P1** 23549991 · **P2** 23550060 · **P3** 23728292 · **P4** 24356381 · **P5** 25896003. `✓` = code present and verified at that block, `—` = no code, `≠` = present but a different instance than at P5.

**Six bounded quote models are implemented and independently qualified at five hashes:**
Uniswap V2/V3, standard-token plain Curve StableSwap-NG, Maker/Sky LitePSM,
Lido wstETH conversion, and Origin Lido ARM. Each pool still requires validation
at the requested block hash; unsupported variants and the other nine families
remain explicit. See `docs/COMPLETION.md` and `docs/reports/*-review.md` for the
current admission evidence. The inventory table below retains its original
identity research; it is not a statement of current adapter coverage.
Discovery alone never admits a pool. `unavailable` describes missing historical
evidence, not absent liquidity.

| Family | Deployment(s) | Discovery mechanism | Activation evidence | P1 | P2 | P3 | P4 | P5 | Quote reference | Blocking questions |
|---|---|---|---|---|---|---|---|---|---|---|
| **uniswap_v2** | Factory `0x5C69bEe7…5aA6f` | logs `PairCreated` topic0 `0x0d3648bd…d0e9`, from 10000835; or `getPair(a,b)` | **exact: 10000835** (code there, none at 10000834) | ✓ | ✓ | ✓ | ✓ | ✓ | none on chain; `UniswapV2Library.getAmountOut` (997/1000), reproducible via Router02 pure fn | pool list not scanned |
| **uniswap_v3** *(identity only — pools owned by another worker)* | Factory `0x1F98431c…F984` | logs `PoolCreated` topic0 `0x783cca1c…7118` + `FeeAmountEnabled` | **exact: 12369621** (code there, none at 12369620) | ✓ | ✓ | ✓ | ✓ | ✓ | QuoterV2 (adapter worker's scope) | none from this pass |
| **uniswap_v4** | PoolManager `0x00000000…8A90`; StateView `0x7fFE42C4…7227`; V4Quoter `0x52F0E24D…1203` | logs `Initialize` topic0 `0xdd466e67…6438`, from 21688329; 3 indexed params (id, cur0, cur1) | doc'd 21688329; code at all 5 pins; periphery `poolManager()` cross-links verified | ✓ | ✓ | ✓ | ✓ | ✓ | `V4Quoter.quoteExactInputSingle` — **non-view**, revert/unlock based | hook classification; dynamic-fee pools |
| **curve** | MetaRegistry `0xF98B45FA…379fC` + 8 handlers / base registries | `pool_count`/`pool_list` union, or per-factory enumeration | code at all 5; `pool_count` 1800/1800/1858/1967/2452 | ✓ | ✓ | ✓ | ✓ | ✓ | pool's own `get_dy` — **int128 for stableswap, uint256 for cryptoswap** | pool list not enumerated; LLAMMA in scope? |
| **fluid_dex** | Factory `0x91716C4E…9085`; resolvers `0xC93876C0…` (old) / `0x05Bd8269…` (new) | `getTotalPools()` + `getAllPools()`; fallback logs `LogDexDeployed` topic0 `0x80d4769b…da7f` | factory code at all 5; `totalDexes` 39/39/42/43/50 | ✓ | ✓ | ✓ | ✓ | ✓ | `estimateSwapIn(dex,swap0to1,amountIn,minOut)` — **non-view, caller-sensitive** | **new resolver has no code at P1–P3** |
| **balancer_v3** | Vault `0xbA133333…9bA9` + 11 factories | logs `PoolRegistered` topic0 `0xbc1561ee…2dad` (factory-filterable) + `PoolInitialized` | Vault code at all 5; doc'd creation 21332121 | ✓ | ✓ | ✓ | ✓ | ✓ | `BatchRouter.querySwapExactIn` — **non-view but returns normally**, not revert-encoded | shared ERC-4626 buffer capacity ids |
| **ekubo** | v2 core `0xe0e0e08A…d444`; v3 core `0x00000000…d701` | logs `PoolInitialized` topic0 `0x5e4688b3…154d` — **no indexed params**, full-deployment scan | v2 ✓ all 5 (first event 22048334); **v3 creation in (23728292, 24134506]** | v2 ✓ | v2 ✓ | v2 ✓ | ✓ | ✓ | version-matched `QuoteDataFetcher`; SqrtRatio is uint96 w/ scale selector | extension-bearing pools unmodelled |
| **maker_sky_psm** | LitePSM `0xf6e72Db5…3042`; PSM-USDC-A `0x89B78CfA…cC5A`; wrapper `0xA188EEc8…f98c`; converter `0x3225737a…276A` | curated (no factory) | code at all 5 | ✓ | ✓ | ✓ | ✓ | ✓ | arithmetic: `tin`/`tout` + `to18ConversionFactor`, clamped by pocket USDC / PSM DAI / vat line | converter capacity unread; LitePSM creation block |
| **spark** | **alias only** → sDAI, sUSDS, UsdsPsmWrapper | label resolution (ParaSwap dex-lib), not a pool scan | code at all 5 | ✓ | ✓ | ✓ | ✓ | ✓ | inherits `erc4626` / `maker_sky_psm` | LlamaSwap's label unverified (repo private) |
| **lista_stable** | Factory `0xF6c9ffA6…F8a6`, 8 pools | `pairLength()` + `swapPairContract(i)` | **no code at P1–P3**; creation in (23728292, 24356381] | — | — | — | ✓ | ✓ | `get_dy(uint256,uint256,uint256)` — plain view; **not** Curve's int128 | FEE_DENOMINATOR / A_PRECISION unconfirmed |
| **erc4626** | sUSDe, sDAI, sUSDS, waEthWETH, waEthLidoWETH, waEthLidowstETH; registry `StataTokenFactory 0xCb0b5cA2…974F` | per-issuer curation + `getStataTokens()` | code at all 5 | ✓ | ✓ | ✓ | ✓ | ✓ | `previewRedeem`/`previewDeposit` in the traded direction; protocol-side cap for capacity | **sUSDe redeem reverts at every pin** |
| **lido** | stETH `0xae7ab965…7fE84`; wstETH `0x7f39C581…2Ca0`; queue `0x889edC2e…f9B1` | curated (no factory) | code at all 5; ≤23549991 upper bound | ✓ | ✓ | ✓ | ✓ | ✓ | `getStETHByWstETH` / `getWstETHByStETH` (pure fns of totalPooledEther/totalShares) | none blocking; queue is `unavailable` by design |
| **origin_arm** | Lido ARM `0x85B78AcA…acc6`; **Ethena ARM `0xCEDa2d85…366d`** | curated (ARM registry + `deployments-1.json`) | Lido ✓ all 5 (product live ~Oct 2024); **Ethena created 23924639** | Lido ✓ | Lido ✓ | Lido ✓ | ✓ | ✓ | no quoter: `amountIn * traderate{0,1} / 1e36`, clamped by `getReserves()` | **two ABI generations; same proxy changes ABI mid-study** |
| **bebop** | Blend `0xbbbbbBB5…AD5F`; legacy `0xbeb09000…01a5`; JAM `0xbeb0b062…4ea6` / `0xbEbEbEb0…f47b`; Router `0xBeb0009A…3bf2A` | curated; fills via the topic0 table in `docs/sources/bebop.md` | settlements ✓ all 5; **Router only at P5** | ✓ | ✓ | ✓ | ✓ | ✓ | **none exists** — quotes are off-chain EIP-712 | needs an external quote archive |
| **zerox_rfq** | Deployer `0x00000000…cEae`; a *different* Settler per pin; legacy proxy `0xDef1C0de…5EfF` | `ownerOf(feature)` **at the pinned block**; history via `Deployed` topic0 `0xaa94c583…c0b2` | Deployer ✓ all 5; Settler instance differs at every pin | ≠ | ≠ | ≠ | ≠ | ✓ | **none exists** — Settler emits no fill event at all | needs an external quote archive |

## Targeted research items

### 1. Lista Stable — Ethereum identity: **RESOLVED, and the premise was wrong**

Lista StableSwap **is** deployed on Ethereum mainnet. Factory `0xF6c9ffA64bD0aE8a068dd7b7d954c654A3E7F8a6`, verified independently on chain here, with 8 pools at 25896003 and 6 at 24356381.

The label is KyberSwap's exchange id `lista-stable`. The implementation is a **PancakeSwap-StableSwap (Curve-style) fork** behind ERC-1967 proxies over one shared implementation `0x86d2946BD6C807e797E8Eec0d632931Dc337DeA3`. SOURCE_DISCOVERY.md's warnings — don't substitute the BNB lisUSD PSM, don't assume Fluid ABI compatibility — both hold: **no mainnet pool contains lisUSD**, and the ABI is Curve-shaped with a `uint256` twist. lisUSD, the CDP PSM and Lista's V2/V3 CL DEX are genuinely BNB-only.

Live proof at 25896003 on the USDC/USDT pool `0x35c9a4DaE1ff05788f24B5B32721D89340CBB636`: `get_dy(0,1,1000000)` → `1000302`. Pools relevant here: USDC/USDT, USDe/USDT, USDe/USDC, USDT/USDS, wstETH/ETH.

**Remaining:** exact factory creation block (bracketed to `(23728292, 24356381]`, not bisected); `FEE_DENOMINATOR` and `A_PRECISION` must be read from the implementation before `fee_raw`/`A_raw` mean anything; per-pool creation blocks. **Lista contributes nothing at P1–P3.**

### 2. Spark — what the label means: **RESOLVED, and it is not a venue**

Two separate answers.

**PSM3 has no Ethereum mainnet deployment.** The `spark-psm` deploy script targets only Arbitrum, Base, Optimism and Unichain; `spark-address-registry/src/Ethereum.sol` has no `PSM3` constant. The docs say PSM3 *extends mainnet Sky PSM liquidity to other chains* — on mainnet there is nothing for it to be. Caution recorded: the Base PSM3 address `0x1601843c…` **does** have bytecode on mainnet and both `totalAssets()` and `name()` revert on it, so a cross-chain address copy plus a naive has-code check would have produced a wrong record.

**The aggregator "Spark" swap label on mainnet is the sDAI ERC-4626 vault** (and the sibling "sUSDS" label is the sUSDS vault), per ParaSwap/Velora `dex-lib` `src/dex/spark/config.ts`, whose DEX key is the string shown in route breakdowns. The mainnet adapter list contains `Spark` and `sUSDS` and **not** `SparkPsm`. Confirmed on chain: `sDAI.pot()` = `0x197E90f9…7cf7`, matching the config. If the label is specifically "Spark PSM", that is Sky's `UsdsPsmWrapper 0xA188EEc8…f98c` (Etherscan tag "Spark: Usds Psm Wrapper") over the LitePSM.

**Consequence:** every Spark pool record is a pointer with a shared `capacity_id`. Treating "Spark" and "ERC-4626 sDAI" as two sources double-counts one vault. SparkLend is a lending market and is out of scope as a swap venue.

**Remaining:** DefiLlama's server/app repos are no longer public, so LlamaSwap's own "Spark" adapter could not be read.

### 3. The Ekubo waEthWETH/WETH pool: **NOT FOUND — a complete-coverage negative**

`waEthWETH` was resolved: `0x0bfc9d54Fc184518A81162F8fB99c2eACa081202`, a `StataTokenV2` over Aave v3 Ethereum **Core**, `asset()` = WETH, `aToken()` = `0x4d5F47FA…14E8`, and `StataTokenFactory.getStataToken(WETH)` returns exactly it.

Complete `PoolInitialized` scans of **both** mainnet Ekubo cores — 242 events on v2 from block 21900000, 134 on v3 from 24134000, saved under `data/discovery-evidence/ekubo/` — contain **no pool** whose token0 or token1 is `waEthWETH` or `waEthLidoWETH 0x0FE906e0…0ce9`. Because `PoolInitialized` has no indexed parameters, these scans are necessarily unfiltered and therefore token-complete. This is a negative result, not a failed lookup.

What does exist: Balancer V3 is a confirmed waEthWETH venue, and Ekubo v3 does have a `USDC/WETH` pool (block 25684162) distinct from its `ETH/USDC` pools.

**Needed from the lead:** the source of the claim — a different chain, an aggregator label, or a Balancer pool read as Ekubo. Also note the disambiguation trap: `waEthWETH` (Core), `waEthLidoWETH 0x0FE906e0…` (Lido market, same underlying WETH) and `0x252231882fb38481497f3c767469106297c8d93b` (what the **legacy** factory returns for WETH) are three different tokens.
