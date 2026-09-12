# Curve

**Deployment.** MetaRegistry `0xF98B45FA17DE75FB1aD0e7aFD971b0ca00e379fC`, confirmed on chain by *both* address providers: classic `0x0000000022D53366457F9d5E68Ec105046FC4383.get_address(7)` and NG `0x5ffe7FB82894076ECB99A30D6A32e969e6e35E98.get_address(7)` each return it.

**Status at the pins.** Code present at all five. `registry_length()` = 8 at every pin. `pool_count()` = 1800 / 1800 / 1858 / 1967 / 2452.

**Registry map** — every `base_registry` read on chain at 25896003, every name read from `AddressProviderNG.get_id_info` (`data/discovery-evidence/curve/probe_probe_curve3_25896003.json`):

| h | handler | base registry | name | pools | `get_dy` |
|---|---|---|---|---|---|
| 0 | `0x46a8a9cf…` | `0x90e00ace…` | Stableswap Custom Pool Registry | 49 | int128 |
| 1 | `0x127db66e…` | `0xb9fc1573…` | Stableswap Metapool Factory | 381 | int128 |
| 2 | `0x5f493fee…` | `0x9a32af1a…` | Cryptoswap Registry | 8 | uint256 |
| 3 | `0xc4f38902…` | `0xf18056bb…` | Twocrypto Factory | 401 | uint256 |
| 4 | `0x538e984c…` | `0x4f8846ae…` | Stableswap crvUSD Factory | 29 | int128 |
| 5 | `0xcbc1be39…` | `0x0c0e5f2f…` | TricryptoNG Factory | 125 | uint256 |
| 6 | `0xe06eba9c…` | `0x6a8cbed7…` | StableswapNG Factory | 1056 | int128 |
| 7 | `0x33c5252f…` | `0x98ee851a…` | TwocryptoNG Factory | 403 | uint256 |

Sum = 2452 = `pool_count()`. That equality is the coverage check.

Handler 2's base registry `0x9a32af1a…` is **not** the AddressProvider id-5 "Cryptoswap Custom Pool Registry" `0x8f942c20…`. Three distinct crypto registries exist on mainnet; three of my own initial guesses at these names were wrong before the on-chain read, which is why the table is sourced rather than inferred.

**Discovery.** Two paths, not equivalent.

- *MetaRegistry union* — best for lookup and enrichment: `pool_count()`, `pool_list(uint256)`, `get_coins`, `get_underlying_coins`, `get_n_coins`, `get_base_pool`, `get_registry_handlers_from_pool`, `get_coin_indices`. Deduplicate: one pool can surface through several handlers.
- *Per-factory enumeration* — best for a resumable stream. The factories are append-only, but the two hand-curated registries (handlers 0 and 2) expose `remove_pool`, and `update_registry_handler(index, handler)` replaces a handler **in place** (this has already happened at index 2). So MetaRegistry indices are not a safe resume cursor, and handler addresses must be re-read at each pinned block.

## Quote semantics

The trusted reference is the pool's own `get_dy`, and **the argument types differ by family**:

- Stableswap: `get_dy(int128,int128,uint256)` `0x5e0d443f`; metapools also `get_dy_underlying(int128,int128,uint256)` `0x07211ef7`
- Cryptoswap (Twocrypto / Tricrypto / legacy crypto): `get_dy(uint256,uint256,uint256)` `0x556d6e9f`, and **no** `get_dy_underlying` at all

Calling the wrong selector reverts. Dispatch on the pool's registry family, or better, use `MetaRegistry.get_coin_indices(pool, from, to) -> (int128 i, int128 j, bool is_underlying)` — the bool answers "plain or underlying" directly. Tricrypto-NG's `get_dy` delegates to the factory's `views_implementation()`, so a mis-set views contract makes quotes revert rather than return a wrong number.

State for local math: balances, `A` (plus `future_A`/`initial_A` and the ramp timestamps if mid-ramp), `fee`, `admin_fee`, stored rates / rate multipliers, and for metapools the base pool's virtual price. **A ramping `A` means the amplification at the historical block is not today's `A`.**

An underlying-token match is not a usable market: `get_underlying_coins` can list a token reachable only via `exchange_underlying`, and some pools do not implement it.

Shortcut worth benchmarking: the Spot Rate Provider `0xA834f3d23749233c9B61ba723588570A1cCA0Ed7` (`get_quotes(src,dst,amount_in)`) walks the MetaRegistry and type-sniffs pools itself.

**Open.** Pool list not enumerated. LLAMMA/crvUSD lending AMMs (AddressProvider ids 16 and 17) are **not** among the 8 handlers — MetaRegistry coverage is complete only for those 8, so LLAMMA is either out of scope or a separate family with different math. Per-pool creation blocks are unknown; recording `pool_list` membership per pinned block is cheaper and sufficient.
