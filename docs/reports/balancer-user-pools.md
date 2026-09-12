# Selected Balancer V3 pools

This is a bounded identity and verification note for Ethereum (chain id 1). It
uses the official Balancer V3 GraphQL API (`https://api-v3.balancer.fi/graphql`)
for current public metadata, retrieved 2026-09-08, and the interfaces in the
pinned local `balancer-v3-monorepo` checkout for the historical call plan. The
API result is not a substitute for block-pinned RPC evidence.

## Current public metadata

| pool | API name/type | version | factory | tokens (address, decimals) | API hook/custom liquidity |
|---|---|---:|---|---|---|
| `0x1ea5870f7c037930ce1d5d8d9317c670e89e13e3` | Balancer rETH - Aave WETH / `STABLE` | 2 | `0xe42c2e153bb0a8899b59c73f5ff941f9742f1197` | `waEthWETH` `0x0bfc9d54fc184518a81162f8fb99c2eaca081202` (18); `rETH` `0xae78736cd615f374d3085123a210448e74fc6393` (18) | `hook=null`; custom add/remove false |
| `0x85b2b559bc2d21104c4defdd6efca8a20343361d` | Balancer Aave GHO/USDT/USDC / `STABLE` | 1 | `0xb9d01ca61b9c181da1051bfdd28e1097e920ab14` | `waEthUSDT` `0x7bc3485026ac48b6cf9baf0a377477fff5703af8` (6); `Aave Prime GHO` `0xc71ea051a5f82c67adcf634c36ffe6334793d24c` (18); `waEthUSDC` `0xd4fa2d31b7968e448877f69a96de69f5de8cd23e` (6) | `hook=null`; custom add/remove false |

Both API records report `protocolVersion=3`, `hasNestedPool=false` for every
listed token, and no API buffer flag was requested in the query. The reported
balances are current API display values and are not historical reserves. The
API's `createTime` values are `1762802303` and `1736461595`, respectively;
they remain current API metadata, while the exact creation/registration and
initialization evidence is recorded below from pinned event logs.

The official API/backend documents the `poolGetPool` query and its `type`,
`version`, `factory`, `poolTokens`, `hook`, and liquidity-management fields:
[Balancer backend/API](https://github.com/balancer/backend). The Vault is the
shared token accounting layer for every pool; it is deliberately agnostic to
pool math and also owns ERC-4626 buffer accounting:
[Vault concepts](https://docs.balancer.fi/concepts/vault/).

## Routing interpretation

The first pool can directly quote only the pair `(waEthWETH, rETH)`. The
second can directly quote each pair among `(waEthUSDT, Aave Prime GHO,
waEthUSDC)`. Neither record by itself establishes a direct route for the
study's canonical `WETH`, `USDC`, or `USDT` endpoints: the `waEth*` addresses
are distinct token contracts. A wrapper/buffer conversion and its historical
state must be proven before admitting a route. Do not treat a symbol or a
stable-pool label as a 1:1 peg.

`hook=null` and the two custom-liquidity booleans are only current API
metadata. A route evaluator must use the block-pinned `getHooksConfig` and
`getPoolConfig` values, including fee and pause/recovery flags. Pool balances,
token rates, and any ERC-4626 buffer balances are shared Vault state; they must
not be counted as independent liquidity copies.

## Five-pin activation evidence

The requested pins are `23549991`, `23550060`, `23728292`, `24356381`, and
`25896003`. The generated evidence code-checked the canonical Ethereum V3 Vault
`0xba1333333333a1ba1108e8412f11850a5c319ba9` and each pool with EIP-1898
`eth_getCode`, then validated the pool's `getVault()` before dependent reads.
Absent code caused all dependent getters to be skipped.

| pool | code at pins 23549991 / 23550060 / 23728292 / 24356381 / 25896003 | registered + initialized | stable amplification at active pins |
|---|---|---|---|
| `0x1ea5…13e3` | absent / absent / absent / present / present | `23770895` / `23770901` | `50000` / `100000` |
| `0x85b2…361d` | present at all five | true at all five | `3750000`, `3750000`, `4000000`, `4250000`, `4750000` |

The exact activation event provenance is:

| pool | `PoolCreated` and `PoolRegistered` | `PoolInitialized` |
|---|---|---|
| `0x1ea5…13e3` | block `23770895`, hash `0x48258763…67bbecd6`, tx `0x47c7fbcd…0492ecdf2`, logs `550` / `555` | block `23770901`, hash `0xe3124486…321e90ba`, tx `0x45253e39…436a4aec4`, log `186` |
| `0x85b2…361d` | block `21589896`, hash `0xd402eea5…9512df6d`, tx `0x95ccf9a1…f28106bd`, logs `142` / `144` | block `21589910`, hash `0x8592d82b…11ad73f`, tx `0x60dfc7e1…6845c2d9`, log `58` |

At every active pin both pools returned `isPoolRegistered=true` and
`isPoolInitialized=true`; every observed hook contract was the zero address
with all hook flags false. Pool 1's `waEthWETH` has an initialized shared
ERC-4626 buffer underlying WETH; `rETH` had no initialized buffer. Pool 2's
three `waEthUSDT`, GHO, and `waEthUSDC` buffers were initialized, with
underlyings USDT, GHO, and USDC. These are shared-capacity identity facts only;
they do not admit a pricing route or quote adapter.

The full event and per-pin raw-call evidence is in
`data/discovery-evidence/balancer-user-pools/<block-hash>.json`; the two
machine-readable pool records are appended to
`data/discovery/1/balancer_v3.json`. The complete Balancer pool universe is
still unresolved.

## Pinned call plan

The following are canonical V3 Vault/Explorer view signatures from the local
Balancer V3 interfaces. Use the deployed Ethereum Vault (or the official
Explorer delegate that forwards these views) supplied by the root worker; do
not infer its address from a pool page.

| target | call | purpose |
|---|---|---|
| pool | `getVault()`; `getAmplificationParameter()` | contract/Vault identity; StablePool amplification state |
| Vault | `isPoolRegistered(address)`; `isPoolInitialized(address)` | registration and trading activation |
| Vault | `getPoolTokens(address)` | ordered token addresses |
| Vault | `getPoolTokenInfo(address)`; `getCurrentLiveBalances(address)` | token type/rate-provider data, raw balances, live scaled balances |
| Vault | `getPoolTokenRates(address)` | decimal scaling and token rates |
| Vault | `getPoolConfig(address)`; `getHooksConfig(address)` | fees, liquidity flags, pause/recovery, hook address and all hook flags |
| Vault | `getPoolPausedState(address)`; `getPoolData(address)` | pause windows and stable-pool state (the latter only after initialization) |
| Vault | `isERC4626BufferInitialized(address)`; `getERC4626BufferAsset(address)`; `getBufferBalance(address)`; `getBufferTotalShares(address)` | wrapper/buffer identity and shared underlying/wrapped balances; record reverts for non-buffers |
| factory | `isPoolFromFactory(address)`; `getPoolCount()`; `getPoolsInRange(uint256,uint256)` | verify the API factory identity and enumerate without assuming a complete list |

For both pools, run the buffer calls for every listed `waEth*` token and keep
the revert/success result. A successful buffer read is evidence of a Vault
buffer, not proof that the pool's entire balance is independently withdrawable.
Do not use `getBufferOwnerShares` for route capacity unless the relevant
liquidity owner is explicitly identified.

## Status

`current_public_metadata`: **observed via official API**. `pool_type`: **stable
per API and StablePoolFactory family**. `direct_pool_pairs`: **identified above**.
`historical_five_pin_activation`: **verified for these two pool addresses from
block-hash-pinned code, Vault reads, and factory/Vault event provenance**.
`historical_tokens/hooks/buffers`: **verified at active requested pins**, with
decimals sourced from current API metadata and marked as API metadata in the
inventory. No quote adapter, pricing admission, or completeness claim for all
Balancer V3 pools is made.

## Probe command

The companion probe has no network activity by default and emits call specs for
offline review. Its online mode uses the project's `RpcClient` and
`SnapshotStore`, performs only read-only block-pinned calls, and writes one
`<block-hash>.json` per requested pin. For example:

```text
.venv/bin/python scripts/balancer_user_pools_probe.py --online \
  --blocks 23549991 23550060 23728292 24356381 25896003 \
  --output-dir data/discovery-evidence/balancer-user-pools
```

Add `--events` to fetch indexed `PoolCreated`, `PoolRegistered`, and
`PoolInitialized` logs through the root worker's serialized RPC path. The
probe never claims a pool is usable from API metadata alone.
