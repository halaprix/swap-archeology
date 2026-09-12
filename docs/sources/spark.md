# Spark — label resolution

The brief asked what the aggregator "Spark" swap label means on Ethereum. Two findings.

## 1. PSM3 has no Ethereum mainnet deployment

`sparkdotfi/spark-psm`'s deploy script targets only Arbitrum, Base, Optimism and Unichain. `spark-address-registry/src/Ethereum.sol` has no `PSM3` constant — its `PSM` constant is the Sky LitePSM. The docs state that PSM3 *extends mainnet Sky PSM liquidity to other chains*, so on mainnet there is nothing for it to be.

PSM3 addresses that do exist: Base `0x1601843c5E9bC251A3272907010AFa41Fa18347E`, Arbitrum `0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266`, Optimism `0xe0F9978b907853F354d79188A3dEfbD41978af62`, Unichain `0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f`.

**A trap worth recording.** The Base PSM3 address `0x1601843c…` *does* have bytecode on Ethereum mainnet, and both `totalAssets()` and `name()` revert on it. Copying an address across chains and checking only "has code" would have produced a plausible-looking but entirely wrong deployment record.

## 2. The "Spark" swap label is the sDAI ERC-4626 vault

Evidence is ParaSwap/Velora `dex-lib` `src/dex/spark/config.ts`, whose DEX key is exactly the string rendered in route breakdowns:

```
Spark: { MAINNET: { sdaiAddress 0x83F20F44…BEeA, daiAddress 0x6B175474…71d0F, potAddress 0x197E90f9…7cf7,
                    swapFunctions: deposit / redeem / withdraw / mint } }
sUSDS: { MAINNET: { 0xa3931d71…7fbD over 0xdC035D45…384F } }
```

The live adapter list for network 1 contains `Spark` and `sUSDS` and does **not** contain `SparkPsm`; `SparkPsm` exists only for Arbitrum and Base. Confirmed on chain: `sDAI.pot()` = `0x197E90f9FAD81970bA7976f33CbD77088E5D7cf7`, matching the config.

If the label is specifically "Spark **PSM**", on mainnet that is the Sky `UsdsPsmWrapper 0xA188EEc8F81263234dA3622A406892F3D630f98c` — Etherscan tags it "Spark: Usds Psm Wrapper" and Instadapp's mainnet `spark-psm` connector targets the LitePSM behind it. Verified: `gem` = USDC, `usds` = USDS, `tin = tout = 0`.

## Consequence for this project

**Spark is not an independent liquidity source.** Every pool record in `data/discovery/1/spark.json` is a pointer with a shared `capacity_id`:

- Spark → the `erc4626` sDAI record
- sUSDS → the `erc4626` sUSDS record
- Spark PSM → the `maker_sky_psm` LitePSM records

If the solver treats "Spark" and "ERC-4626 sDAI" as two venues it double-counts one vault and invents depth that does not exist.

**Quote reference:** whatever the `erc4626` note says for sDAI/sUSDS — `previewRedeem`/`previewDeposit` in the traded direction. For the wrapper, the LitePSM arithmetic with `tin = tout = 0`.

SparkLend (Pool `0xC13e21B648A5Ee794902342038FF3aDAB66BE987`, `ADDRESSES_PROVIDER` `0x02c3ea4e…` verified) is a lending market and is explicitly out of scope as a swap venue.

**Remaining.** DefiLlama's server/app repos are no longer public, so LlamaSwap's "Spark" adapter could not be read; the ParaSwap mapping is code-verified and is the best available evidence. If the study cites LlamaSwap specifically, confirm with DefiLlama.
