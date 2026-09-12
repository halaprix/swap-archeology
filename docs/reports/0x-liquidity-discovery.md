# 0x liquidity discovery — reviewed shortlist

Source coverage was checked against actual loaded states and historical factory
reads. Research only: no adapters added, no chart/sweep rerun, no transactions.

## What 0x actually showed

The authenticated `/sources?chainId=1` request advertised **58 source labels**.
Twelve `/swap/allowance-holder/price` responses were collected: ETH/WETH sells of
1/10/100/1000 into USDC, and 100,000-unit USDT→DAI, DAI→USDC, USDT→USDC and
USDS→USDC requests. Their reported block is **25939998**, retrieved 2026-09-09.
These are current indicative routes, not October quotes. `route.fills` exposes
venue labels and token hops, not the identity of individual pools. Fill shares
across successive hops must not be summed as independent input allocations.

- 10/100 ETH/WETH routes include **PancakeSwap V3**.
- 100/1000 ETH/WETH routes include **WBTC** as an intermediary.
- The 1000-token routes include **USDS** and Maker PSM.
- USDT→DAI uses **Uniswap V4 USDT→USDS, then Sky Migration USDS→DAI**.
- USDT→USDC uses **Uniswap V4 USDT→USDS, then Maker PSM USDS→USDC**.
- Native ETH requests produce WETH-based fills, indicating a wrapping step.
- Ekubo V3 and Lista Stable appear in current routes, but the probed deployments
  had no code at the start of the historical interval.

Key-free responses: [API evidence](../../outputs/0x-discovery/).
API definitions: [sources](https://docs.0x.org/api-reference/evm-ap-is/sources/getsources),
[indicative prices](https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getprice).

## Ranked follow-ups

### 1. USDS connections: converter and PSM wrapper

Both contracts have code at block **23549939** (the first block of our window):

- DAI↔USDS converter: `0x3225737a9bbb6473cb4a45b7244aca2befdb276a`.
- USDS↔USDC LitePSM wrapper: `0xa188eec8f81263234da3622a406892f3d630f98c`.

Both are already discovered but unsupported in the local inventory. Actual
loaded October states contain no USDS. Add converter semantics, USDS-connected
pool discovery, and wrapper support that shares the existing LitePSM inventory.
**Do not count the wrapper as a second independent USDC reserve.** Historical
existence of the converters does not establish which current USDS AMM pools
existed or had useful depth in October; those pool identities still need discovery.

Primary semantics: [DaiUsds](https://github.com/sky-ecosystem/usds/blob/master/README.md),
[LitePSM wrapper](https://developers.skyeco.com/protocol/liquidity/litepsm/).

### 2. WBTC intermediary pools using the existing V3 family

WBTC is absent from actual loaded October states. Fresh factory queries found
**12 pools** across WETH/WBTC, WBTC/USDT and WBTC/USDC at block 23549939, each
with nonzero current-tick liquidity at that block. Representative identities:

| Pair | Fee | Pool |
|---|---:|---|
| WETH/WBTC | 5 bps | `0x4585fe77225b41b697c938b018e2ac67ac5a20c0` |
| WETH/WBTC | 30 bps | `0xcbcdf9626bc03e24f779434178a73a0b4bad62ed` |
| WBTC/USDT | 5 bps | `0x56534741cd8b152df6d48adf7ac51f75169a83b2` |
| WBTC/USDT | 30 bps | `0x9db9e0e53058c89e5b94e29621a205198648425b` |
| WBTC/USDC | 5 bps | `0x9a772018fbd77fcd2d25657e5c547baff3fd7d16` |
| WBTC/USDC | 30 bps | `0x99ac8ca7087fa4a2a1fb6357269965a2014abc35` |

Next: include WBTC in token scope, collect these pools' tick coverage for the
bounded interval, and test their contribution. Nonzero active liquidity is not
proof that a requested trade can fully execute. Factory identity and fee evidence
are in the pinned raw responses, not inferred from the current 0x route.

### 3. PancakeSwap V3: new venue, concrete historical pools

The [official factory](https://developer.pancakeswap.finance/contracts/v3/addresses)
is `0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865`.
Historical factory queries found **9 pools**: four WETH/USDC, four WETH/USDT,
and one WETH/WBTC. Seven had nonzero active liquidity at the first block.
Useful candidates for depth checks include:

- WETH/USDC 5 bps: `0x1ac1a8feaaea1900c4166deeed0c11cc10669d36`.
- WETH/USDT 5 bps: `0x6ca298d2983ab03aa1da7679389d955a4efee15c`.
- WETH/WBTC 25 bps: `0x9b5699d18dff51fc65fb8ad6f70d93287c36349f`.

Next: qualify a Pancake-specific V3 adapter/read plan against its contracts before
reusing Uniswap math. Protocol-fee encoding, deployment identity and partial-fill
behavior must be checked; “a V3 fork” is not independent qualification. Saved
QuoterV2 responses at block 23550094 are leads only: output divided by requested
input is not a verified execution price when the quoter can partially fill.

### 4. Join native ETH and WETH routing

The local graph still lacks the WETH9 deposit/withdraw connection at
`0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2`. This is not a new venue, but it lets
ETH and WETH requests use each other's pools. Add explicit wrapping semantics
with integer balance conservation, then compare the unified result against both
existing aggregate curves. [WETH9 source](https://etherscan.io/address/0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2#code).

### 5. Historical Ekubo V2, not current Ekubo V3

Fresh code checks at block 23549939 confirm V2 core
`0xe0e0e08a6a4b9dc7bd67bcb7aade5cf48157d444` existed, while V3 core
`0x00000000000014aa86c5d3c41765bb24e11bd701` did not. The local inventory already
contains V2 pool leads but lacks an executable adapter. Next: sample the V2
ETH/stable and stable/stable pool states before committing to an adapter.
[Versioned contract reference](https://docs.ekubo.org/reference/contracts/).

## Exclusions and draft corrections

- **Fluid USDC/USDT pools are already loaded.** Addresses `0x667701e51b4d1ca244f17c78f7ab8744b4c99f9b`
  and `0xea734b615888c669667038d11950f44b177f15c0` are not new missing sources.
  The chart lists direct ETH pools separately from the full routing universe;
  the first Flash draft confused those views.
- **Lista factory** `0xf6c9ffa64bd0ae8a068dd7b7d954c654a3e7f8a6` had no code at
  block 23549939. Its current appearance does not justify adding it to October.
- **RFQ:** the API advertises `0x_RFQ`, but none of these 12 returned routes uses
  it. Current indicative requests do not reconstruct historical offers; this
  also does not establish that RFQ was unavailable to real traders then.
- Balancer and other advertised families remain broader research leads; the
  sampled routes do not establish additional concrete historical pool depth.

## Evidence and reproduction

- `scripts/zeroex_discovery.py`: current source/price GETs, key from environment
  or a private key file; credentials are not embedded in code or responses.
- `scripts/zeroex_historical_leads.py`: bounded historical code, factory,
  liquidity and quoter reads; `outputs/0x-discovery/historical-leads.json` retains
  exact block hashes and raw responses. Successful calls establish existence
  **by** that block, not exact deployment dates or identities of 0x's live pools.
The next highest-value bounded implementation is USDS connections plus WBTC
pool coverage, followed by ETH/WETH wrapping and PancakeSwap qualification.
Actual historical price improvement remains unmeasured for these additions.
