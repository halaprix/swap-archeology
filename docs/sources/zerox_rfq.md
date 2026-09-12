# 0x RFQ

**Status `unavailable` = not reconstructable, not zero liquidity.**

## Discovery — 0x is the one family with a clean per-block identity oracle

The Settler Deployer `0x00000000000004533Fe15556B1E086BB1A72cEae` is an ERC-721 whose `name()` is `"0x Settler"` and whose **token owner for a feature id *is* the live Settler at that block**. So `eth_call ownerOf(uint256 feature)` *at the pinned block* answers "which Settler was live then" in one call. Verified:

| feature | 23549991 | 24356381 | 25896003 |
|---|---|---|---|
| 2 taker | `0x207e1074858a7e78f17002075739ed2745dbaece` | `0xf48a3f7c0575c85cf4529aa220caf3c055773f1c` | `0x0889e9327b98d7d1be3c301a4585ff3330502c9a` |
| 3 metatxn | `0x1e1ed00f1048c99240bb56defc20de44a0a005cb` | `0x4ffe5502630d7d4a0eb490d526728b6d9e6bb902` | `0x82cc35ea953c87dbe61d6b6feabd91371201d9bb` |
| 4 intent | `0x25233ddd68dac9ac40e1e8d80c58d36b524032b2` | `0xf9ae60c0cb90cb6869bcd8bde2f18528b26b5d6d` | `0x59da89baee7c773f991731fca0a8bba58d511c05` |
| 5 bridge | `0x3decc6d88c2f0fa5cfa3b4f0aceb3fe60afc2b30` | `0xd1be573eb30cb1e1f3dd768e467f0b57a6329a4b` | `0xd7185c486dd88eb9f3573b878a1469485644091f` |

A different instance at every pin. `ownerOf(1)` reverts `ERC721NonexistentToken(1)` at all three — feature 1, the original monolithic Settler, was removed at block 19935958. `prev(uint128)` gives the previous instance (still valid during the dwell window), `next(uint128)` the CREATE2-predicted next one. **Never hardcode a Settler address**; the repo README says so explicitly and the table above is why.

Full history: `Deployed(uint128 indexed feature, uint32 indexed nonce, address indexed instance)`, topic0 `0xaa94c583a45742b26ac5274d230aea34ab334ed5722264aa5673010e612bc0b2` — all three indexed, empty data, 4 topics. Note `nonce` is **uint32**. Companion `Removed(uint128,uint32,address)` topic0 `0x14771144127988a0427a9c8e58dcb697adccdb5eadb2821e26d8a03a622b951b`.

Fixed addresses: AllowanceHolder `0x0000000000001fF3684f28c67538d4D072C22734` (code at all five pins), Permit2 `0x000000000022D473030F116dDEE9F6B43aC78BA3`, legacy Exchange Proxy `0xDef1C0ded9bec7F1a1670819833240f027b25EfF` (`owner()` = `0x618f9c67ce7bf1a50afa1e7e0238422601b0ff6e` at all probed pins).

## Why unavailable

RFQ/OTC orders are EIP-712 structs signed off chain and returned by the 0x API. Nothing is committed on chain at quote time. Unfilled, losing and expired quotes are permanently unrecoverable.

It is worse than for Bebop: **Settler emits no fill event at all.** Post-Exchange-Proxy 0x archaeology needs transaction calldata plus ERC-20 `Transfer` correlation. The legacy Exchange Proxy did emit `RfqOrderFilled(bytes32,address,address,address,address,uint128,uint128,bytes32)` topic0 `0x829fa99d94dc4636925b38632e625736a614c154d55006b7ab6bea979c210c32` (no indexed params) and `OtcOrderFilled` topic0 `0xac75f773e3a92f1a02b12134d65e1f47f8a14eabe4eaf1e24624918e6a8b269f` — but researched log counts put the last `RfqOrderFilled` at block 24063982 and `OtcOrderFilled` at zero from ~23.9M. **Both are dead before pin 25896003.** Even where fills exist they give executed size, not offered depth.

## Double-counting

The 0x API routes through the same Uniswap / Curve / Balancer pools this project discovers directly. Do not add a "0x" row on top of them.

**Open.** Pins 23550060 and 23728292 were not probed for `ownerOf(2)`. Nonce 12 (`0x207e1074…`) was deployed at 23469839 and nonce 13 at 23934928, so both almost certainly resolve to nonce 12 — confirm with one call each rather than assume.
