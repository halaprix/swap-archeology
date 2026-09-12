# Phase 1 — source inventory: final report

Worker: source-inventory engineer. Date 2026-09-07. Chain 1 only. Research-only; no transactions, no git, no edits outside owned paths.

## Files changed

**Created (all owned by this worker):**

- `docs/SOURCE_STATUS.md` — the 15-family table plus the three targeted research items.
- `docs/sources/*.md` — 15 files: `uniswap_v2`, `uniswap_v3`, `uniswap_v4`, `curve`, `fluid_dex`, `balancer_v3`, `ekubo`, `maker_sky_psm`, `spark`, `lista_stable`, `erc4626`, `lido`, `origin_arm`, `bebop`, `zerox_rfq`.
- `data/discovery/1/*.json` — 14 files (every family **except** `uniswap_v3`, which belongs to the V3 adapter worker and was **not touched** — verified by mtime and by its `status: "supported"` and its own `data/adapters-evidence/` paths).
- `data/discovery-evidence/` — 48 raw-response JSON files across 12 subdirectories, plus `.rpc_call_count`.
- `scripts/inv_rpc.sh`, `scripts/inv_probe.py`, `scripts/inv_run_probe.sh`, `scripts/inv_write.py` — the verification harness. If `scripts/` turns out to be another worker's, these four move wherever the lead wants; nothing else depends on them.

**Not touched:** `src/`, `evidence/`, `docs/INTERFACES.md`, `docs/PLAN.md`, `data/discovery/1/uniswap_v3.json`, `data/snapshots/`, `data/rpc-cache/`.

Note: `docs/INTERFACES.md` changed on disk mid-session (credential resolution now prefers `ETH_RPC_URL` from a project `.env`). My harness uses the still-supported `RPC_MAINNET` fallback path; nothing needs changing, but new code should follow the new order.

## RPC budget

**57 calls** against the archive node, against a cap of 250. The saving came from batching through Multicall3 `aggregate3(allowFailure=true)`: 27 identity probes across 6 blocks cost 6 calls, not 162. Code presence was proven inside those batches — a call to an address with no code succeeds returning `0x`, while a contract either returns data or reverts, so `success && data != 0x` or `!success` proves code. `cast code` was reserved for the few cases with no known view function and for the two exact creation-block checks.

Only two `eth_getLogs` were run, both deliberate and bounded: the complete `PoolInitialized` history of each Ekubo core, to settle the waEthWETH question. No other log scan was executed; every other family got a written filter spec instead.

Representative commands (RPC URL always piped through `sed -E 's#https?://[^ "]+#<rpc-url>#g'`):

```
cast call --rpc-url "$RPC_MAINNET" --block 25896003 0xcA11bde05977b3631167028862bE2a173976CA11 \
  'aggregate3((address,bool,bytes)[])((bool,bytes)[])' "[(0x5C69…,true,0x574f2ba3),…]"
cast code --rpc-url "$RPC_MAINNET" --block 10000835 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f | wc -c   # 27720 vs 2 at 10000834
cast call --rpc-url "$RPC_MAINNET" --block 21895170 --from 0x…01 0x9D39A5DE… 'redeem(uint256,address,address)(uint256)' 0 0x…01 0x…01
  -> execution reverted: OperationNotAllowed (0xf50a3b52)
cast logs --rpc-url "$RPC_MAINNET" --from-block 21900000 --to-block 25896003 \
  --address 0xe0e0e08A6A4b9Dc7bD67BCB7aadE5cF48157d444 0x5e4688b3…154d      # 242 events
```

Validation: all 14 JSON files pass a schema check (chain/family/schema/status enum, list shapes, lowercase 42-char addresses everywhere, every `unresolved` entry carrying `what`/`why`/`next_step`).

## Family verdicts

**No family is `supported`,** and none can be from this phase: `docs/INTERFACES.md` requires an implemented adapter cross-checked against the protocol's own quoter at the same block, and no adapters exist yet. The honest split is therefore identity-ready vs not.

**Identity pinned, discovery spec written, ready for an adapter (11):** `uniswap_v2`, `uniswap_v4`, `curve`, `fluid_dex`, `balancer_v3`, `ekubo`, `maker_sky_psm`, `lista_stable`, `lido`, `erc4626`, `origin_arm` (Lido ARM only). `uniswap_v3` identity and activation are confirmed independently but its record belongs to another worker.

**Alias, not a venue (1):** `spark`. Every record is a pointer into `erc4626` or `maker_sky_psm` with a shared `capacity_id`.

**Unavailable — not reconstructable from chain (2):** `bebop`, `zerox_rfq`. Both had real depth at every pin. RFQ quotes are EIP-712 structs signed off chain; unfilled size leaves no trace. 0x is the harder of the two because Settler emits no fill event at all, and both legacy Exchange Proxy fill events are dead before pin 5.

**Unresolved within an otherwise pinned family (2 records):** the Ethena ARM (exists, funded, but its struct layouts are not decoded against the verified ABI) and `waEthLidoWETH`/`waEthLidowstETH` (the Aave Lido-market Pool address is not pinned, so their redemption caps are unknown).

**Ekubo pools are the only populated pool set from a log scan:** 126 records (84 v2, 42 v3) whose both tokens are in the endpoint/connector set. Curated singleton pools are populated for `maker_sky_psm` (4), `lido` (3), `erc4626` (6), `lista_stable` (8, registry-enumerated), `origin_arm` (2) and `spark` (3 aliases).

## Findings that change how the runner must behave

1. **Version traps that fail silently.** Fluid's current `DexReservesResolver 0x05Bd8269…` and `DexResolver 0x11D80CfF…` have **no code** at pins 1–3; the runner must select `0xC93876C0…` there or every early-pin Fluid read returns empty rather than erroring. Balancer's StablePool v3, WeightedPool v2, StableSurge v3 and GyroECLP v2 factories likewise have no code at pin 1, and ReClamm v3 none even at pin 4.
2. **The same proxy can change ABI mid-study.** Origin's Lido ARM `getReserves()` reverts at pins 1–3 and succeeds at pins 4–5. Dispatch on block, not address.
3. **The Origin Lido ARM has almost no tradeable depth.** At 25896003 `WETH.balanceOf(arm)` is 19.293 but `getReserves().reserve0` is **0.00298** — the rest is reserved for the LP withdrawal queue. Using the raw balance overstates depth ~6500×. A trader swap never pulls from Morpho or the Lido queue.
4. **sUSDe is not an atomic hop at any pin.** `redeem()` reverts `OperationNotAllowed` at 25896003 *and* at 21895170 inside the stress window, while `previewRedeem` returns a healthy 1.2463. `cooldownDuration` was 604800 at pins 1–4 and 86400 at pin 5.
5. **PSM-USDC-A is dead** (`line = 0`, `Art = 0`, 99.33 USDC in gemJoin) while the LitePSM is 1:1 with `tin = tout = 0` at all five pins and direction-asymmetric capacity: 3.969 B USDC out via `buyGem` vs 807 M DAI out via `sellGem`.
6. **Curve `get_dy` argument types differ by family** — int128 for stableswap, uint256 for cryptoswap, wrong selector reverts. Three of my own initial guesses at the registry names were wrong before reading `AddressProviderNG.get_id_info`; the table in `docs/sources/curve.md` is sourced, not inferred.
7. **0x has a per-block identity oracle.** `Deployer.ownerOf(feature)` at the pinned block returns the Settler live then — a different instance at every pin. Never hardcode one.
8. **Cross-chain address copying is unsafe.** Base's PSM3 address has bytecode on mainnet; `totalAssets()` and `name()` both revert. A has-code check alone would have produced a wrong record.
9. **Double-counting risks are concrete, not theoretical:** Spark ↔ sDAI/sUSDS, UsdsPsmWrapper ↔ LitePSM inventory, Balancer boosted pools ↔ ERC-4626 buffers ↔ the `erc4626` family, and Bebop's `eventId` appearing on two contracts for one fill.

## Open questions for the lead

1. **Ekubo waEthWETH/WETH — where did the claim come from?** Complete scans of both mainnet cores find no such pool. Balancer V3 *is* a confirmed waEthWETH venue. Different chain, aggregator label, or a Balancer pool read as Ekubo?
2. **Is LLAMMA / crvUSD lending in scope?** AddressProvider ids 16 and 17 are not among the 8 MetaRegistry handlers, so MetaRegistry coverage is complete only for those 8. LLAMMA would be a separate family with different math.
3. **Are the other four mainnet ARMs in scope** (WETH, USDC, ETH, EtherFi)? Each is a separate pair and possibly a separate ABI generation.
4. **Is Fluid DEX Lite in scope?** `0x12a47cEB…` has code at all five pins but is not counted by the DexFactory and does not share DEX T1 math.
5. **Do `bebop` and `zerox_rfq` stay in the study as explicitly unavailable rows,** or does the lead want to source an external quote archive? They should not be silently dropped, and they must not be double-counted against the AMMs they route through.
6. **Where should `scripts/inv_*` live?** They were placed in `scripts/`; if another worker owns that directory, say where to move them.
7. **Window 4 (25760917–25768095) has no pin.** I probed 25760917 for the identity and Fluid sets and everything present at pin 5 was already present there, but the lead may want a pin inside that window for consistency with the other three.

## Limitations

- No pool enumeration was run for `uniswap_v2`, `uniswap_v4`, `curve`, `fluid_dex` or `balancer_v3`. Each has an exact filter spec (address, topic0, from_block, decoding rules) in its `docs/sources/*.md` and in the record's `notes`, plus a per-pin count bound so the runner can check completeness.
- Only two creation blocks are **exact** (Uniswap V2 10000835, V3 12369621). Every other activation is either an upper bound from code presence at pin 1, a bracket from two probes (Ekubo v3, Lista, Ethena ARM, Fluid's new resolver), or a documented block that was **not** re-verified on chain — each is labelled as such in its record and never presented as verified.
- Ethena ARM struct decodes are heuristic and flagged `unresolved`.
- Lista `fee_raw`/`A_raw` are raw; their denominators are unconfirmed.
- Pins 2 and 3 were not probed for the 0x Settler; nonce 12 is the near-certain answer but is stated as an assumption to confirm, not a fact.
- No adapter was written, so no quote was cross-checked against a protocol quoter. Every "quote reference" in these docs is a *specification* for the adapter worker, not a verified implementation.
