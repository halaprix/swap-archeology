# Lido

**Deployments** — all three have code at all five pins.

| contract | address |
|---|---|
| stETH | `0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84` |
| wstETH | `0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0` |
| WithdrawalQueueERC721 | `0x889edC2eDab5f40e902b864aD4d7AdE8E412F9B1` |

Discovery is curated; Lido has no factory. Activation is recorded as ≤ 23549991, an upper bound — all three long predate pin 1 and activation is not a study constraint.

## What Lido actually contributes

Conversion edges, not AMM depth. **stETH↔WETH is not a Lido product**: it must come from a discovered pool (Curve steth, Uniswap, Balancer, Lista pool 0) or the Origin Lido ARM.

### wstETH ↔ stETH — the useful edge

Trusted reference: `getStETHByWstETH(uint256)` and `getWstETHByStETH(uint256)`. Both are pure functions of `stETH.getTotalPooledEther()` and `stETH.getTotalShares()` at that block, so the state to read per block is exactly those two values.

```
wrap:   wstETHOut = stETHIn * totalShares / totalPooledEther     (floor)
unwrap: stETHOut  = wstETHIn * totalPooledEther / totalShares    (floor)
```

`stEthPerToken()` across the pins: 1.2159889053309785, 1.2159889053309785, 1.2182985823837404, 1.2258030907293411, 1.2431032118818366.

No fee, no price impact. The only modelling risk is **integer rounding** — wrap-then-unwrap loses 1–2 wei, and floats will not reproduce it.

**stETH rebases.** A stETH balance is `shares × rate` and changes at every oracle report. Adapters must carry shares, not stETH amounts, across a rebase boundary.

### ETH → stETH (`submit`) — one-way

`submit(address _referral) payable`, 1:1 minus share rounding, capped by `getCurrentStakeLimit()` (150000e18 at 25896003) and gated by `isStakingPaused()` (false at 25896003). Useful only if the router may spend native ETH; it cannot close a stETH→WETH leg.

### Withdrawal queue — explicitly unavailable

`requestWithdrawals` mints an NFT; ETH arrives only after an oracle report finalises the request, hours to days later. It cannot be a step in an atomic route. Min 100 wei, max 1000e18 per request; `isBunkerModeActive()` (false at 25896003) extends finalisation and is a hard disqualifier.

It is recorded as `unavailable` rather than omitted so the route search explicitly rejects it instead of silently pricing stETH at 1.0 ETH.

**Open.** Exact creation blocks not bisected — deliberately, since they are far outside the study windows.
