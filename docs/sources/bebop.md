# Bebop

**Status `unavailable` is a statement about reconstructability, not about liquidity.** Bebop had real depth at every pin; it simply cannot be rebuilt from chain.

**Deployments** — all settlement contracts have code at all five pins; the Router only from pin 5.

| contract | address | role | pins |
|---|---|---|---|
| BebopSettlement (Etherscan "Bebop: Blend") | `0xbbbbbBB520d69a9775E85b458C58c648259FAD5F` | current PMM/RFQ | all 5 |
| BebopSettlement v2 | `0xbeb09000fa59627dc02bb55448ac1893eaa501a5` | legacy PMM/RFQ | all 5 |
| JamSettlement (current) | `0xbeb0b0623f66bE8cE162EbDfA2ec543A522F4ea6` | JAM/solver | all 5 |
| JamSettlement (old) | `0xbEbEbEb035351f58602E0C1C8B59ECBfF5d5f47b` | JAM | all 5 |
| BebopRouter ("Bebop: RFQ-A") | `0xBeb0009ACa35087ce7cCF11637E24dd1Aad3bf2A` | current RFQ entrypoint | **only 25896003** |

**PMM/RFQ and JAM are separate, and "Blend" is not a third contract.** The docs' `BebopSettlement` and the `BebopBlend` contract are the same address, `0xbbbbbBB5…`. Distinct `DOMAIN_SEPARATOR` values confirm the two settlement generations are different contracts: `0x31e9fc52…` (Blend) vs `0x27d409b9…` (legacy), identical across all probed pins.

`JamBalanceManager` (current) `0xC5a350853E4e36b73EB0C24aaA4b8816C9A3579a`. The **old** JAM's balance manager is `0xfe96910cf84318d1b8a5e2a6962774711467c0be` — deployed inside its constructor and not documented publicly; read here on chain via `balanceManager()`.

## Why unavailable

Bebop RFQ prices are EIP-712 orders signed off chain and returned by the API. Nothing is written on chain at quote time. **Executed** fills are recoverable; **offered-but-unfilled** size is not, and a depth curve needs the latter. Mark `requires_external_quote_archive`, never zero liquidity.

## What can be recovered (fills only — calibration or an upper bound on realised size, never depth)

| contract | event | topic0 | carries amounts? |
|---|---|---|---|
| Blend `0xbbbbbBB5…` | `BebopOrder(uint128 indexed eventId)` | `0xadd7095becdaa725f0f33243630938c861b0bba83dfd217d4055701aa768ec2e` | **no** — 2 topics, empty data |
| legacy `0xbeb09000…` | `AggregateOrderExecuted(bytes32 order_hash)` | `0xc59522161f93d59c8c4520b0e7a3635fb7544133275be812a4ea970f4f14251b` | no |
| JAM `0xbeb0b062…` | `BebopJamOrderFilled(uint256 indexed nonce, address indexed user, address[], address[], uint256[], uint256[])` | `0x9659aee63163c5924fca8de09494faaf9169aef36315127310d6e86596eb83dd` | **yes** |
| JAM | `BebopBlendSingleOrderFilled` | `0xca180a308256a6e9607c256dd145d700f18b1ee21e9bfa2919258b31a211bb8a` | yes |
| JAM | `BebopBlendMultiOrderFilled` | `0xa3f15217206817f5e14f76307e5f65cfdc211516553c6638ef4fc79c4d6a0f0a` | yes |
| JAM | `BebopBlendAggregateOrderFilled` | `0x85272ea7cbfb5ccba50d6cfaad14ff2ea471614d48c96ed5ecef53f11e566579` | yes |
| old JAM `0xbEbEbEb0…` | `Settlement(uint256 indexed nonce)` | `0x7a70845dec8dc098eecb16e760b0c1569874487f0459ae689c738e281b28ed38` | no |
| Router `0xBeb0009A…` | `BebopPmmSwap` | `0x7199b4a4e868be23324b6c68ae97411fef576cbaac785d28fcd301e2a8b0715a` | yes |
| Router | `BebopRouterSwap` | `0x58073e86a8de5387bf042457ea63e46957d2ef816d9d85c90aff5a3661681ef1` | yes |

A **direct** Blend fill emits only the id, so tokens and amounts must come from the transaction calldata (`swapSingle`/`swapMulti`/`swapAggregate`) or correlated ERC-20 `Transfer` logs.

**The `uint128 eventId` is the join key** across Blend, Router and JAM logs. The same fill can appear on two contracts — do not count it twice.

**Open.** A pre-2023-07 Bebop settlement is not found in any public source; do not assume one exists.
