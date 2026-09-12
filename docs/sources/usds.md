# Maker / Sky USDS Connectors — Source Semantics and Integration Reference

Historical research and adapter specification for Maker / Sky USDS connectors on Ethereum mainnet,
calibrated for the October 10 2025 ETH/WETH sell→USDC interval (blocks 23549939..23550192).

## 1. Core Deployments and Identities

Both connectors and their underlying Maker/Sky DSS infrastructure were deployed and verified
live across all historical qualification blocks (23549939, 23550094, 23550192).

| Component / Contract | Ethereum Mainnet Address | Role / Description |
|---|---|---|
| **DaiUsdsConverter** | `0x3225737a9Bbb6473CB4a45b7244ACa2BeFdB276A` | Canonical 1:1, zero-fee DAI↔USDS conversion contract |
| **UsdsPsmWrapper** | `0xA188EEc8F81263234dA3622A406892F3D630f98c` | Wrapper converting USDS↔USDC by delegating to LitePSM |
| **LitePSM (`LITE-PSM-USDC-A`)** | `0xf6e72Db5454dd049d0788e411b06CfAF16853042` | Live DAI↔USDC Peg Stability Module |
| **DaiJoin** | `0x9759A6Ac90977b93B58547b4A71c78317f391A28` | Maker/Sky DSS DaiJoin adapter for converter engine |
| **UsdsJoin** | `0x3c0F895007Ca717AA01C8693E59dF1E8C3777fEb` | Maker/Sky DSS UsdsJoin adapter for converter engine |
| **Pocket (Gem Pocket)** | `0x37305B1cD40574E4C5ce33f8e8306Be057fD7341` | Holds USDC reserve buffer for LitePSM withdrawals |
| **DAI** | `0x6B175474E89094C44Da98b954EedeAC495271d0F` | DAI stablecoin (18 decimals) |
| **USDS** | `0xdC035D45d973E3EC169d2276DDab16f1e407384F` | Sky USDS stablecoin (18 decimals) |
| **USDC** | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | Circle USD Coin (6 decimals) |

---

## 2. ABI Selectors and On-Chain Specification

All getters are public views verified on Ethereum mainnet bytecode:

```
daiJoin():                 0xc11645bc
usdsJoin():                0xfa1e2e86
dai():                     0xf4b9fa75
usds():                    0x4cf282fb
gem():                     0x7bd2bea7
psm():                     0x04bda262
pocket():                  0xcccef9e2
to18ConversionFactor():    0x4010f777
tin():                     0x568d4b6f
tout():                    0xfae036d5
live():                    0x957aa58c  (DSS join liveness check)
wards(address):            0xbf353dbb  (DSS authority check)
balanceOf(address):        0x70a08231
allowance(address,address): 0xdd62ed3e
```

> [!NOTE]
> In Maker/Sky DSS join contracts, the function selector for `live()` is `0x957aa58c`
> (`bytes4(keccak256("live()"))`), not standard ERC pause selectors. Both `live()` and
> `wards(address)` on `dai` and `usds` return `1` (true) across all qualified blocks.

---

## 3. Connector Semantics and Execution Paths

### 3.1 `DaiUsdsConverter` (`0x3225...`)

The canonical converter allows direct conversion between DAI and USDS with zero slippage, zero fee,
and exact integer mass conservation:

- **DAI → USDS**:
  1. Pulls DAI from `msg.sender` into `DaiJoin`.
  2. Burns DAI in the DSS engine.
  3. Mints identical amount of USDS via `UsdsJoin`.
  4. Exits USDS to `usr`.
  5. Mathematical formula:
     $$\text{amountOut} = \text{amountIn}$$
- **USDS → DAI**:
  1. Pulls USDS from `msg.sender` into `UsdsJoin`.
  2. Burns USDS in the DSS engine.
  3. Mints identical amount of DAI via `DaiJoin`.
  4. Exits DAI to `usr`.
  5. Mathematical formula:
     $$\text{amountOut} = \text{amountIn}$$
- **Direction-Specific Domain Gating & Permissions**:
  - `DAI → USDS` (`daiToUsds`):
    - `usds.wards(usdsJoin) == 1` (`UsdsJoin` authorized to mint USDS).
    - Note: `DaiJoin.join` does NOT check `daiJoin.live() == 1`, nor does it require `dai.wards`.
  - `USDS → DAI` (`usdsToDai`):
    - `daiJoin.live() == 1` (engine not caged; required by `DaiJoin.exit`).
    - `dai.wards(daiJoin) == 1` (`DaiJoin` authorized to mint DAI).
    - Note: `UsdsJoin.join` burns USDS and does not require `usds.wards`.
- **Capacity & Arithmetic Limits**:
  - `amountIn <= MAX_CONVERSION_WAD = MAX_UINT256 // 10**27` ($\approx 1.158 \times 10^{50}$ tokens).
  - Any input exceeding `MAX_CONVERSION_WAD` reverts due to `wad * RAY` overflow in `UsdsJoin` and `DaiJoin.mul(ONE, wad)`. Unbounded conversion for `uint256.max` is not executable on-chain.
  - Unbounded by local ERC20 token balances in the converter contract; bounded only by DSS system-wide
    debt ceiling parameters. Does not advertise local reserve capacity IDs.

### 3.2 `UsdsPsmWrapper` (`0xa188...`)

Disassembly of the deployed bytecode reveals that `UsdsPsmWrapper` is an unopinionated pipeline
wrapping `DssLitePsm`:

- **USDC → USDS (`sellGem`)**:
  1. Transfers USDC from caller to wrapper.
  2. Approves and calls LitePSM `sellGem(address(this), gemAmt)`.
  3. LitePSM takes USDC, credits DAI buffer in LitePSM, transfers DAI to wrapper.
  4. Wrapper joins DAI via `DaiJoin` and exits USDS directly to user.
  5. Output:
     $$\text{gross} = \text{gemAmt} \times \text{to18ConversionFactor}$$
     $$\text{fee} = \left\lfloor \frac{\text{gross} \times \text{tin}}{\text{WAD}} \right\rfloor$$
     $$\text{usdsOut} = \text{gross} - \text{fee}$$
  6. Capacity constraint: bounded by LitePSM's DAI buffer (`DAI.balanceOf(litePsm)`).
- **USDS → USDC (`buyGem`)**:
  1. Transfers USDS from caller to wrapper.
  2. Joins USDS to `UsdsJoin`, mints DAI, exits DAI to wrapper.
  3. Wrapper approves LitePSM and calls LitePSM `buyGem(usr, gemAmt)`.
  4. LitePSM burns DAI, pulls USDC from `pocket`, and transfers USDC to `usr`.
  5. Required input:
     $$\text{gross} = \text{gemAmt} \times \text{to18ConversionFactor}$$
     $$\text{fee} = \left\lfloor \frac{\text{gross} \times \text{tout}}{\text{WAD}} \right\rfloor$$
     $$\text{requiredUsdsIn} = \text{gross} + \text{fee}$$
  6. Capacity constraint: bounded by `USDC.balanceOf(pocket)` and `USDC.allowance(pocket, litePsm)`.

---

## 4. Critical Shared Capacity Invariant

> [!WARNING]
> **Do NOT duplicate USDC liquidity.**
> The `UsdsPsmWrapper` has no independent pool of USDC or DAI. Every unit of USDC withdrawn via
> wrapper `buyGem` comes directly out of the LitePSM `pocket` (`0x37305b1c...`), and every unit of
> USDC deposited via `sellGem` credits the LitePSM contract's DAI balance.

### 4.1 Capacity ID Collision in Multi-Venue Evaluator
If `UsdsPsmWrapper` and `LitePSM` were both instantiated as separate pool records with their own IDs,
any routing plan attempting to draw from both would be rejected by `Evaluator._shared_capacity_reason`:
```python
# Evaluator rule: multiple distinct pool_ids claiming the same capacity ID is rejected
if len(pools_using_cap) > 1:
    reasons.append(f"shared capacity not modelled: {cap_id}")
```
To avoid double counting while preserving evaluator correctness:
1. `UsdsPsmWrapperState` advertises the underlying LitePSM capacity IDs:
   - `maker_sky_psm:0xf6e72db5454dd049d0788e411b06cfaf16853042:dai_buffer`
   - `maker_sky_psm:0xf6e72db5454dd049d0788e411b06cfaf16853042:pocket_usdc`
2. **Preferred Integration Architecture**:
   Represent USDS connectivity as a direct graph edge through `DaiUsdsConverter` into `LitePsmState`.
   Because `DaiUsdsConverter` has zero fees and exact 1:1 integer conversion:
   $$\text{USDS} \xrightarrow{1:1} \text{DAI} \xrightarrow{\text{LitePSM}} \text{USDC}$$
   is **mathematically and economically identical** to calling `UsdsPsmWrapper` directly, but updates
   the single underlying `LitePsmState` in place without creating capacity conflicts or phantom liquidity.

### 4.2 Exact Preimage and Sub-Micro Terminal Refunds
Under `buyGem` (USDS→USDC), `gemAmt` is in 6-decimal units, while USDS is in 18-decimal units.
With `tout = 0`, exact integer input must be an exact multiple of $10^{12}$. Any fractional remainder
leaves unspendable wei:
- In strict exact-input mode, non-multiple inputs raise `Unsupported`.
- Under the `allow_psm_dai_refund` policy (established in `docs/reports/october-stablecoin-connectors.md`),
  when routing via the graph edge ($\text{USDS} \to \text{DAI} \to \text{USDC}$), the sub-micro dust
  $(< 10^{12}\text{ wei})$ remains as terminal DAI refund without invalidating the quote.

---

## 5. Historical AMM Discovery (Block 23549939)

A comprehensive scan of Uniswap V3 factory pools and Uniswap V4 hookless pool states was conducted
at the historical interval start block (`23549939`):

### 5.1 Uniswap V3 USDS Candidate Pools
| Pair | Fee Tier | Raw Fee | Pool Address | Active Liquidity at 23549939 | Status |
|---|---|---|---|---|---|
| **USDS / USDC** | 5 bps (0.05%) | 500 | `0x8aee53b873176d9f938d24a53a8ae5cf36276464` | `641,557,830,417,479,316,323` | Active candidate |
| **USDS / USDC** | 1 bps (0.01%) | 100 | `0x4eb5db0134fac94e66da89764d58a9f709d53a8f` | `2,504,075,123,803,858` | Active candidate |
| **USDS / USDC** | 30 bps (0.30%) | 3000 | `0xa66a2770bc0e0c65b63b5a3bb4560e90f95d6146` | `557,249,876,720,826,746` | Active candidate |
| **USDS / DAI** | 30 bps (0.30%) | 3000 | `0xe9f1e2ef814f5686c30ce6fb7103d0f780836c67` | `85,593,150,411,941,631,354,179,755` | Active candidate |
| **USDS / USDT** | 1 bps (0.01%) | 100 | `0x31e29b2b8fd9d6ca57afbac110df2d14cb151d1e` | `0` | Zero active liquidity / Inactive |
| **USDS / WETH** | 5 bps (0.05%) | 500 | `0xe547c6d5039d1902db58102e7f52410dbc5b0708` | `0` | Zero active liquidity / Inactive |
| **USDS / WETH** | 30 bps (0.30%) | 3000 | `0x691c9c856eeec77531750c465450b5e05a74c047` | `0` | Zero active liquidity / Inactive |

> [!NOTE]
> **Fee Units & Liquidity Interpretation**:
> - Uniswap V3 raw fee units are parts per million: raw fee 100 = 1 bps (0.01%), raw fee 500 = 5 bps (0.05%), raw fee 3000 = 30 bps (0.30%).
> - Raw active liquidity $L$ cannot be compared directly across pairs with differing token decimals. For 18-dec / 6-dec pools (USDS/USDC), $L$ scales with $\sqrt{10^{18} \cdot 10^6} = 10^{12}$, whereas for 18-dec / 18-dec pools (USDS/DAI), $L$ scales with $10^{18}$. Raw $L$ does not represent nominal dollar depth.

### 5.2 Uniswap V4 Hookless USDS Candidate Inventory
Candidate hookless pools on Uniswap V4 PoolManager (`0x000000000004444c5dc75cA358380D2e3dE08a90`):
- `USDC / USDS` (5 bps & 1 bps)
- `USDT / USDS` (5 bps & 1 bps)
- `DAI / USDS` (5 bps)

Queried via `StateView.getLiquidity()` at block `23549939`.
**Result**: Candidate inventory queries returned `liquidity = 0` at this historical block.
Zero getter output indicates no active in-range liquidity (uninitialized or empty candidate inventory; not proof of active deployment).

---

## 6. Historical Qualification Pins

Checked across all three historical calibration pins:
- **Block 23549939** (`0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12`):
  - DAI buffer: `404,112,015.808651` DAI
  - Pocket USDC: `2,334,616,341.775318` USDC
  - `tin = tout = 0`
  - `live = 1`, all `wards = 1`
- **Block 23550094** (`0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d`):
  - DAI buffer: `404,013,101.442845` DAI
  - Pocket USDC: `2,338,349,663.298283` USDC
  - `tin = tout = 0`
  - `live = 1`, all `wards = 1`
- **Block 23550192** (`0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c`):
  - DAI buffer: `403,962,790.669528` DAI
  - Pocket USDC: `2,338,400,000.751167` USDC
  - `tin = tout = 0`
  - `live = 1`, all `wards = 1`

All raw RPC outputs, call specifications, and getter traces are persisted in `outputs/source-expansion/usds/pinned_evidence.json`.
