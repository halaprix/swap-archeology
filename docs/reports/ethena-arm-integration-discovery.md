# Ethena ARM historical integration discovery

Scope: read-only source and cached-evidence discovery for
`0xceda2d856238aa0d12f6329de20b9115f07c366d`. This is not an adapter,
qualification, or quote result.

## Deployment gate and existing evidence

The proxy was created at block **23924639**. It cannot contribute at the first
three project pins (23549991, 23550060, and 23728292): the saved
`liquidityAsset()` probes return empty data there. The probe script refuses any
block below that creation boundary without constructing an RPC client.

The two in-scope pins are materially different dates:

| Pin | Timestamp | Existing evidence | Status |
| --- | --- | --- | --- |
| 24356381 | 2026-01-31T17:34:47Z | legacy ABI, implementation, rates, reserves, direct balances, and sUSDe conversion ladder | implementation and source deployment record agree |
| 25896003 | 2026-09-03T09:52:35Z | multi-asset config, reserve, direct balances, adapter conversion ladder, active market, and dependent `maxWithdraw(proxy)` | implementation and source deployment record agree; observed market withdrawal capacity is zero, while source-capacity behavior remains unvalidated |

The completed probes identify implementation
`0x11e6bee1662a2e2b20a7163cc33334fa1cf19979` at 24356381 and
`0xebb2b66759b593ea50eb8c306e2e13464cdb99fe` at 25896003. Deployment
manifests at the source commits below map those addresses to the legacy and
multi-asset source objects. This is strong ABI provenance, though not a
compiled-runtime-bytecode match.

At 25896003, cached responses establish `liquidityAsset == USDe`,
`getBaseAssets() == [sUSDe]`, `activeMarket ==
0x0dc20109ea012f050beda184844c1ed5ec6da33a`, and successful
`getReserves(sUSDe)`. The eight returned `baseAssetConfigs(sUSDe)` words decode
under the historical multi-asset layout as:

| Field | Raw value | Decimal interpretation |
| --- | ---: | ---: |
| `buyPrice` | 999514745405190386000000000000000000 | 0.999514745405190386 USDe per converted sUSDe |
| `sellPrice` | 999960000000000000000000000000000000 | 0.99996 USDe per converted sUSDe |
| `buyLiquidityRemaining` | 250000000000000000000000 | 250,000 USDe |
| `sellLiquidityRemaining` | 340282366920938463463374607431768211455 | `uint128.max` sUSDe |
| `crossPrice` | 999960000000000000000000000000000000 | 0.99996 |
| `pendingRedeemAssets` | 509769882941621430313728 | 509,769.882941621430313728 USDe |
| `peggedToLiquidityAsset` | 0 | false |
| `adapter` | `0xe620afb67223ae03c260112ae21a717af94c90f0` | Ethena conversion/queue dependency |

The saved reserve pair is 13.425156286433921730 USDe and
35.697372637678532703 sUSDe. It is an observed getter result, not a proof that
the active-market withdrawal performed by a future swap will succeed.
The saved dependent `activeMarket.maxWithdraw(proxy)` response is zero, so this
pin provides no observed active-market withdrawal capacity.

## Source epochs and pricing

CodeGraph was used first on the registered `arm-oeth` checkout. It traces the
current `EthenaARM` through `AbstractARM`, the proxy's public
`implementation()` getter, and `EthenaAssetAdapter`. Current source is not a
historical implementation proof, so this discovery retains two candidate Git
objects and their SHA-256 file digests in the probe output.

The January pin uses the legacy deployment record at commit
`bd5f945c769244ae6f8beac3726f015e7c752e55`, which maps its observed
implementation to the following source content:

- `EthenaARM.sol`: `797d33c87351e1fec5c2d5e063d4ec767f8a8e6aa3898663c9846658f311b547`
- `AbstractARM.sol`: `2f5c0b102c994a951fc0ac5b78b2b5df04fa6bcde61a1196aee933ad688ca23e`

Its constructor makes USDe the liquidity asset/token0 and sUSDe the base
asset/token1. It first applies `sUSDe.convertToShares(USDeIn)` or
`sUSDe.convertToAssets(sUSDeIn)`, then computes the exact-input output as
`floor(convertedInput * traderate{0,1} / 1e36)`. Thus a legacy model cannot use
the Lido ARM's identity conversion. Legacy output capacity is direct token
inventory: USDe paid out is constrained by the raw USDe balance net of
outstanding LP withdrawals; sUSDe paid out is constrained by the raw sUSDe
balance. An active market and Ethena cooldown claims do not automatically fund
a legacy swap path.

The September cached ABI shape and observed implementation match the multi-asset
deployment record at commit `b6cbc6aa114a657cc54f29741f7fc3de31a26a53`:

- `EthenaARM.sol`: `a3dea2686d704177b9f0840ce601f36a169ce4af8215da89963d3b331507164b`
- `AbstractARM.sol`: `18ddb3bbc33ee17c6348b6023574fe355b29632ebb1f432c2a7c1d38bf7f1a6a`
- `EthenaAssetAdapter.sol`: `93d5994d4d75507085193f7ad74f92b8a00f0f67c333f16a862082121bb0a1e5`

For USDe to sUSDe, this source converts the USDe input to sUSDe shares through
the adapter, then returns `floor(convertedShares * 1e36 / sellPrice)`. It caps
the result by both the raw sUSDe balance and `sellLiquidityRemaining`. For
sUSDe to USDe, it converts shares to USDe assets through the adapter, then
returns `floor(convertedAssets * buyPrice / 1e36)`. It caps the payout by
`buyLiquidityRemaining` and obtains any shortfall from `activeMarket`; the
published reserve getter adds `maxWithdraw(proxy)` and subtracts
`reservedWithdrawLiquidity`. The adapter delegates both conversion functions
to sUSDe's ERC-4626 `convertToAssets`/`convertToShares`; it also owns the
asynchronous cooldown queue, which is not instant swap inventory.

The completed pin observations contain the proxy's exact `implementation()`
result. The same read remains required for every later block. Matching a
selector shape or a repository commit does not prove runtime bytecode
provenance.

## Required serialized probe

Run only through the designated RPC owner:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --extra benchmark \
  python scripts/ethena_arm_probe.py 24356381 25896003
```

The script writes `data/discovery-evidence/ethena-arm/<block-hash>.json` and
first records `implementation()` plus both incompatible ABI shapes. It selects
a second stage only after observed responses classify the pin, then derives a
third-stage `activeMarket.maxWithdraw(proxy)` read where the multi-asset ARM
has a nonzero market. The September cached snapshot contains that dependent
read and its successful zero result.

For a legacy classification it records token ordering, traderates,
`getReserves()` without an argument, LP queue counters, direct USDe/sUSDe
balances, `liquidityAmountInCooldown`, active market, and sUSDe conversion at
1 raw unit, 1 token, and 100 tokens. For a multi-asset classification it
records the config-backed adapter, reserves, active market,
`reservedWithdrawLiquidity`, direct balances, `maxWithdraw(proxy)`, adapter
asset identity, and the same conversion ladder. Failed selectors are retained
as ABI evidence; they are never interpreted as zero.

An adapter remains blocked until source-capacity boundary cases are checked
against the saved state. No Lido ARM read, adapter, or evidence file is changed
by this slice.
