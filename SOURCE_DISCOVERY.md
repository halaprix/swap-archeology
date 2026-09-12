# Source discovery and historical data plan

2026-09-07 — scope update. This is a checked discovery-method inventory, not a populated pool cache or a claim that historical adapters have been implemented. Documentation and public source were inspected; no new chain acquisition ran for this update.

## Routes to prioritize

Endpoints: WETH, wstETH, sUSDe, USDC, USDT and DAI. Ethereum mainnet remains the chain scope.

- WETH <-> USDC; WETH <-> USDT <-> USDC.
- wstETH <-> WETH <-> USDC; wstETH <-> WETH <-> USDT <-> USDC.
- Also test the wstETH <-> stETH <-> ETH/WETH connection, including Origin's Lido ARM when the historical deployment and available liquidity support it.
- For sUSDe, discover direct stablecoin pools, market sUSDe <-> USDe paths, and the previously studied sUSDe -> sDAI -> DAI -> USDC path. USDe, sDAI, USDS and sUSDS are connector candidates where a verified source requires them, not automatically liquid assets at par.

Arrows describe candidates in both directions, not a promise both directions are enabled. Keep the short routes as the first search set; any longer conversion path is labeled and its coverage reported.

## Discovery inventory

“Automatic” below means a public discovery mechanism exists. Exact mainnet deployment addresses, versions, creation blocks and historical compatibility still require pinning. A source brand appearing in an aggregator is not sufficient identity: its label may describe a wrapper, router or the same underlying liquidity as another label.

| Source | Can discovery be automated and cached? | Initial method and historical data needed |
|---|---|---|
| Uniswap V2 | Yes | `getPair(a,b)` gives the one pair per token pair per factory; `PairCreated` events or `allPairsLength/allPairs` enumerate the factory. Cache creation events and token identities. Read reserves and verify the implementation's fees. Other V2 forks are separate factories. [Interface](https://github.com/Uniswap/v2-core/blob/master/contracts/interfaces/IUniswapV2Factory.sol). |
| Uniswap V3 | Yes | Filter/cache `PoolCreated` events by token pair, capturing every fee tier. Alternatively enumerate `FeeAmountEnabled` then use `getPool`; do not assume today's four familiar fee tiers are exhaustive. Historical quoting needs the quoter or tick bitmap/ticks, active liquidity, price and fees. [Interface](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/IUniswapV3Factory.sol). |
| Uniswap V4 | Yes; quote support depends on hooks | Index PoolManager `Initialize` events. Store chain, manager, pool ID and complete PoolKey: currencies, fee, tick spacing and hooks. There is no separate pool contract address for each pool. Include native ETH currency, not just WETH. Historical reads/quotes must include hook and dynamic-fee behavior; unsupported hooks get an explicit status. [Interface](https://github.com/Uniswap/v4-core/blob/main/src/interfaces/IPoolManager.sol). |
| Curve | Yes, with registry coverage recorded | MetaRegistry `pool_count/pool_list`, `get_coins/get_underlying_coins`, and the registries/factories it covers. Deduplicate pools found through several registries; supplement uncovered factory families. An underlying-token match does not prove a usable underlying exchange method. Historical registry versions and removed entries may require event history or older snapshots. Read or quote implementation-specific balances, amplification, rates and fees. [MetaRegistry](https://github.com/curvefi/metaregistry/blob/main/contracts/MetaRegistry.vy). |
| Fluid | Yes for the inspected DEX family | Factory/resolver exposes pool count, IDs and `getAllPools`; the reserve resolver exposes per-pool and batched reserve/limit reads. Cache factory/version and token pairs, then read effective reserves, prices, fees and trade limits per block. Token balances alone omit smart collateral/debt behavior. Other Fluid versions must be separately identified. [Resolver](https://github.com/Instadapp/fluid-contracts-public/blob/main/contracts/periphery/resolvers/dexReserves/main.sol). |
| Balancer V3 | Yes; hooks/wrappers need classification | Index Vault `PoolRegistered` and `PoolInitialized`; retain pool/factory/token configuration and hooks. Registration is not yet usable liquidity. Read actual pool state, rates, dynamic fees and applicable ERC-4626 buffer capacity, or use the correct historical router query. Several apparently different paths may share one buffer. [Vault events](https://github.com/balancer/balancer-v3-monorepo/blob/main/pkg/interfaces/contracts/vault/IVaultEvents.sol). |
| Ekubo | Yes, version-specific | Use the EVM Core initialization events or public indexer, then cache complete pool keys/configuration and extensions. Ekubo's indexer includes initialization, ticks, positions and swaps. Do not apply Starknet math/ABI to EVM, or current EVM math to older versions without verification. Extension-aware historical quotes remain an implementation task. [Indexer](https://docs.ekubo.org/products/indexer/), [price representations](https://docs.ekubo.org/reference/price-representation/). |
| Maker/Sky PSM | Curated deployment discovery; then automatic reads | Pin each applicable PSM/LitePSM and token pair from official deployments. Read enabled directions, fees, spendable token inventory, debt-ceiling or refill constraints appropriate to that version. A fixed rate is not infinite capacity. Keep user-accessible paths distinct from permissioned fee-free functions. [PSM](https://github.com/sky-ecosystem/dss-psm), [LitePSM](https://github.com/sky-ecosystem/dss-lite-psm). |
| Spark | Curated deployment discovery; source label needs resolution | Pin the exact swap/conversion contract on the selected chain. PSM3 supports USDC/USDS/sUSDS, but that alone does not establish that an aggregator's Ethereum “Spark” label refers to PSM3. It might be a vault/conversion path. Do not count SparkLend deposits as an ordinary token-pair AMM. Read applicable rates and spendable inventory/capacity. [PSM3](https://github.com/sparkdotfi/spark-psm/blob/master/README.md). |
| Lista Stable | Needs further deployment/ABI research; not declared impossible | Identify the exact Ethereum Smart Swap/Stable implementation behind the label. Lista documents Smart Lending/Swap and planned Ethereum expansion; its organization also publishes V2/V3 factories, which do not identify the Stable implementation by themselves. Do not substitute BNB-chain lisUSD PSM or assume Fluid ABI compatibility. Cache verified deployments once resolved; require creation before each sample block. [Official roadmap](https://blog.lista.org/lista-dao-2026-h1-road-map), [V3 repository](https://github.com/lista-dao/lista-v3). |
| ERC-4626 | No universal on-chain vault enumeration | This is a standard, not a venue. Discover through each issuer's factories/deployment lists and wrappers exposed by already discovered pools; cache vault -> asset links. `asset`, previews and maximums support subsequent reads. `convertToAssets` is not an executable price and previews deliberately exclude some limits. Owner-specific maximums require the intended ownership context; a zero-balance dummy owner is not proof of zero market capacity. [Standard](https://eips.ethereum.org/EIPS/eip-4626). |
| Lido | Curated known conversions | Cache wstETH/stETH conversion contract identity and historical rate. Unwrap wstETH into stETH; sell stETH through a separately discovered pool/ARM. Queued stETH redemptions do not produce immediate ETH output. [Wrapper](https://docs.lido.fi/contracts/wsteth/). |
| Origin ARM / Lido ARM | Curated registry/deployments; then automatic reads | Origin's Lido ARM is the stETH/WETH liquidity facility; avoid duplicating it under “Origin” and “Lido ARM.” Pin version, live buy/sell rates, accessible liquidity and any lending withdrawal constraints. LP-share withdrawals and trader swaps are different operations. Origin also documents Ethena ARM as a candidate for sUSDe/USDe; exact deployment and historical availability remain unverified. [Lido ARM registry](https://docs.originprotocol.com/registry/contracts/arm-registry/lido-steth-weth), [ARM overview](https://docs.originprotocol.com/automated-redemption-manager-arm/introduction-to-arm). |
| Bebop | Supported-token discovery via API; complete historical offered liquidity unavailable from chain alone | Separate PMM/RFQ from JAM/solver routing. An on-chain fill records an executed trade, not every quote or available size at that block. Backtesting unexecuted historical offers needs an external quote archive. Mark `requires_external_quote_archive`, not zero liquidity. [API discovery](https://docs.bebop.xyz/rfq-api/quickstart), [API types](https://docs.bebop.xyz/bebop/bebop-api-pmm-rfq/bebop-trading-apis-comparison). |
| 0x RFQ | No public pool inventory; complete historical offered liquidity unavailable from chain alone | Market-maker quotes are obtained off-chain. Reconstructing arbitrary past size quotes requires archived offers and relevant validity conditions; historical fills alone cannot provide a full depth curve. Do not count the AMMs routed by the broader 0x API a second time as RFQ liquidity. [Architecture](https://docs.0x.org/docs/core-concepts/0x-cheat-sheet). |

Repeated Curve, Uniswap V4, Maker PSM and Fluid mentions refer to one source-family inventory each; distinct deployments and versions still get separate identities.

## What waEthWETH means

The name denotes a wrapped Aave Ethereum WETH position: a share token representing WETH supplied to Aave, with yield reflected in its share value. It is economically different from plain WETH and from a staking derivative such as wstETH. The Aave address book lists both older static and newer stata WETH wrappers, so the symbol alone does not identify the exact contract in the cited Ekubo pair. Resolve the token address, underlying asset, wrapper version and historical redemption limits from pool discovery before adding the conversion. A waEthWETH/WETH pool trades the wrapper against its underlying; it does not create an independent USDC exit by itself. [Aave address book](https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol), [wrapper implementation](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/extensions/stata-token/StataTokenV2.sol).

For sUSDe, read the historical cooldown and redemption restrictions. A cooldown-enabled sUSDe -> USDe unstake cannot be inserted as an instant ERC-4626 hop. A market sale to a pool or funded ARM is a separate possible exit. [Ethena](https://docs.ethena.fi/solution-design/staking-usde/user-security-measures).

## Cache discovery once, refresh incrementally

Use two separate datasets:

1. A source inventory: identities and how each venue was discovered.
2. Per-block snapshots: changing liquidity, rates, limits and quotes.

Suggested files are `discovery/<chain>/<source>.json` and `snapshots/<chain>/<block-hash>.json.gz`. These are planned paths, not existing populated artifacts. Save source deployment/factory/manager, pool address or ID, token addresses and decimals, full pool configuration, creation/initialization blocks when known, evidence links, and discovery coverage. Unknown creation blocks remain unknown; never make a current API discovery look historically verified.

Every inventory includes its scanned block ranges and filters, last scanned block/hash, ABI/source version and unresolved entries. Prefer protocol-wide creation-event metadata when practical so a new token can be selected locally. If the first scan was pair-filtered, a new token requires a historical backfill for that token; resuming only from the last block would miss its older pools. A negative lookup is complete only for its explicit factory/filter/block range.

Subsequent runs reuse the file. A new upper block scans only the missing range, with a small overlap and hash checks for reorganizations. Reconcile registry changes instead of assuming enumeration indexes are eternally stable. Filter pools by deployment/initialization before quoting a past block. Cache identity does not mean freeze mutable fee, pause, rate-provider, hook or upgrade state.

## Multicall for the later block acquisition

Target one block-pinned Multicall for independent on-chain reads where it fits. That reduces network round trips; it does not guarantee one provider billing unit, unlimited response size, or all route calculations in a single call. Verify Multicall exists at the historical block. [Multicall3](https://github.com/mds1/multicall).

Build a call list from the cached inventory. Each subcall keeps its identity, success flag and raw return bytes; failures remain unavailable/error rows. Chunk adaptively for gas, response-size and timeout limits while pinning every chunk to the same block/hash. Historical headers are acquired and cached separately as needed; current-block hash cannot be recovered reliably from the EVM BLOCKHASH opcode inside that block's Multicall.

Dependent data is the main limit: discovering initialized tick words may require another batch; quoting hop 2 needs hop 1's output. Use a protocol's full-path quoter where appropriate, or retrieve enough state for verified local math. Plain Multicall does not feed one result into the next call's arguments. Do not silently interpolate quotes across discontinuities to claim exact results.

Check call semantics as well as ABI: Multicall changes msg.sender, and some quoters use non-view/revert-based simulations. Independent quotes must not leak simulated state between subcalls. Keep caller-sensitive queries explicit, and compare batched versus separate pinned reads before trusting a new source adapter. JSON-RPC batching is a separate fallback transport; it is not a single EVM Multicall.

Split routes must not double-count a shared pool, Curve base pool, ERC-4626 vault, Balancer buffer or other shared withdrawal capacity. The updated all-source design requires state-aware combined quoting for these combinations. The earlier disjoint-route shortcut is superseded; see ROUTING_ALGORITHM.md. A source whose shared state cannot be evaluated must remain explicitly unsupported.

## Reuse and next implementation milestone

Reusable acquisition code needs endpoint loading, JSON caching, retries and chunked log fetching. It also needs a Multicall helper, chain/hash cache keys and support for nested tuple inputs such as `aggregate3`. Reuse proven components through an explicit adapter for these requirements; do not silently change old evidence caches.

The immediate implementation milestone is the source inventory/discovery runner with resumable cache and explicit coverage, followed by five-block quote baselines. The later Multicall stage is a requirement captured here, not a delivered implementation. Lista identity, Spark label, exact Ekubo wrapper pool and source activation dates remain research items. No source is labeled impossible merely because one documentation URL failed.
