# Maker / Sky PSM

**Deployments.** All four have code at all five pins.

| contract | address | role |
|---|---|---|
| LitePSM (`LITE-PSM-USDC-A`) | `0xf6e72Db5454dd049d0788e411b06CfAF16853042` | the live DAI↔USDC PSM |
| PSM-USDC-A (original `dss-psm`) | `0x89B78CfA322F6C5dE0aBcEecab66Aee45393cC5A` | retired |
| UsdsPsmWrapper | `0xA188EEc8F81263234dA3622A406892F3D630f98c` | USDS↔USDC over the LitePSM |
| DaiUsdsConverter | `0x3225737a9Bbb6473CB4a45b7244ACa2BeFdB276A` | DAI↔USDS 1:1 |

Verified on chain: LitePSM `gem` = USDC, `pocket` = `0x37305b1cd40574e4c5ce33f8e8306be057fd7341`, `vat` = `0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B`, `to18ConversionFactor` = 1e12. Wrapper `psm()` = the LitePSM. Converter `dai`/`usds` correct, `daiJoin` = `0x9759a6ac…`, `usdsJoin` = `0x3c0f8950…`.

**Discovery is curated** — there is no PSM factory to enumerate.

## Quote semantics

`tin = tout = 0` at **all five pins**, so both directions are exactly 1:1 modulo the decimal conversion. `buf` moved from 400,000,000e18 (pins 1–4) to 800,000,000e18 (pin 5).

```
sellGem(usr, gemAmt):  gross = gemAmt * to18ConversionFactor; daiOut = gross - floor(gross * tin / WAD)
buyGem (usr, gemAmt):  gross = gemAmt * to18ConversionFactor; daiIn = gross + floor(gross * tout / WAD)
```

There is no on-chain quoter; the reference is this arithmetic plus the `dss-lite-psm` source. **A fixed rate is not infinite capacity**, and the two directions have wildly different capacity. At 25896003:

- `buyGem` (DAI→USDC) is bounded by the **pocket's USDC balance and allowance to LitePSM**: 3,969,071,805.97 USDC.
- `sellGem` (USDC→DAI) is bounded by the **DAI ERC20 balance of the LitePSM itself**: 807,293,358.36 DAI. A keeper `fill()` tops that up toward `buf`, bounded by `vat` line − Art headroom of 774,460,864.79 DAI. `rush()` = 0 (nothing to fill) and `gush()` = 7,293,322.54 DAI (excess above `buf`), `cut()` = 35.82 DAI of fees.

State per block: `tin`, `tout`, `buf`, `DAI.balanceOf(psm)`, `USDC.balanceOf(pocket)`, and `vat.ilks(ilk) -> (Art, rate, spot, line, dust)`.

**PSM-USDC-A is dead.** At 25896003 `vat.ilks("PSM-USDC-A")` gives `Art = 0, line = 0`, and its gemJoin holds 99.328577 USDC. `sellGem` capacity is zero (no line, so no DAI can be minted) and `buyGem` is capped at ~99 USDC with `Art = 0` making the repayment path unusable. It stays in the inventory so the runner records a zero-capacity venue instead of silently omitting one.

**User-accessible vs permissioned.** `sellGem`/`buyGem` are open. `sellGemNoFee`/`buyGemNoFee` are `bud`-gated and must never appear in a route.

**Double-counting.** The UsdsPsmWrapper consumes exactly the same DAI buffer and pocket USDC as the LitePSM, and the `spark` family points at it a third time. All three share the capacity ids `maker_sky_psm:0xf6e7…:dai_buffer` and `…:pocket_usdc`.

**Open.** DaiUsdsConverter per-block capacity (daiJoin DAI balance, USDS mint authority) not yet read. LitePSM creation block not established — activation is recorded as ≤ 23549991, an upper bound.
