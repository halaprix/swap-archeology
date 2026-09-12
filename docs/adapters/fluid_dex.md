# Fluid DEX T1 adapter

This adapter covers Fluid DEX T1 pools enumerated by the historical
`FluidDexReservesResolver`. It does not cover Fluid DEX Lite or any later DEX
product.

At pins before 23881741 the runner uses resolver
`0xc93876c0eed99645dd53937b25433e311881a27c`; at and after that block it uses
`0x05bd8269a20c472b148246de20e6852091bf16ff`. Enumeration is
`getAllPools()`. Every discovered record retains a `deployed_by_block` upper
bound rather than claiming an unknown pool creation block.

T1 is not a pair balance curve. Its exact-input path normalizes input to the
resolver's 1e12 scale, applies the packed 1e6 fee, then routes across enabled
smart-collateral and smart-debt imaginary-reserve curves. Each leg is rounded
before denormalization and summing. The adapter checks the source input range,
half-imaginary-reserve limit, per-leg current withdrawable and borrowable
Liquidity limits, real-reserve floor, packed oracle price movement, same-block
center-price guard, pause flag, and integer precision ratios.

The resolver's `estimateSwapIn` simulates `swapIn(..., ADDRESS_DEAD)`. It
returns before Liquidity `operate`, utilization verification, and oracle
persistence. Consequently the adapter's returned state rejects another
positive Fluid swap: it does not fabricate the post-trade virtual reserves,
Liquidity exchange-price rounding, or expanding limits. Fluid states declare
the actual Liquidity owner plus each token capacity identity, so different
Fluid pools sharing a Liquidity token remain explicitly refused by the shared
capacity rule until a route-level owner transition exists.

`scripts/fluid_dex_run.py` is offline by default. The designated RPC owner
uses `--online`; `--write-inventory` is the separate, explicit inventory
mutation switch. The runner saves resolver quotes and raw snapshot calls for
integer checks in both directions. A match establishes only the pre-operation
quote model, not transfer or atomic-settlement behavior.

## Current bounded evidence

The five block-hash-keyed validation files contain complete `getAllPools()`
enumerations for the resolver selected at each pin. The resolver epoch boundary
is block `23881741`: `0xc93876c0eed99645dd53937b25433e311881a27c` is used before
it, and `0x05bd8269a20c472b148246de20e6852091bf16ff` at and after it.

| block | resolver | enumerated pools | quote-qualified pools |
| ---: | --- | ---: | ---: |
| 23549991 | legacy | 39 | 27 |
| 23550060 | legacy | 39 | 28 |
| 23728292 | legacy | 42 | 29 |
| 24356381 | current | 43 | 27 |
| 25896003 | current | 50 | 30 |

All five pins have four successful batch/direct parity methods, for 20/20
matching raw comparisons. The aggregate contains 562 positive exact matches,
201 resolver-zero exclusions, and one positive resolver result refused by the
local oracle-move guard (USDC to BEEF at block 24356381); there are no unequal
successful outputs. The latest pin therefore has 50 unique enumerated pools,
30 pre-operation quote-qualified under this bounded model.

Qualification is explicitly single-use. The returned state must not be reused
for a second positive Fluid swap, and plans sharing a Liquidity-layer/token
capacity are refused until a route-level owner/state transition is modeled.
These checks establish only the resolver-normalized `ADDRESS_DEAD`
pre-operation quote. They do not establish Liquidity operation, utilization,
oracle persistence, transfer, atomic settlement, or quote-adapter readiness for
Fluid DEX Lite or the separate DEX V2 product.

The offline CLI smoke selected Fluid for 1,000 USDC and returned
1,000.305244 USDT through pool
`0x667701e51b4d1ca244f17c78f7ab8744b4c99f9b` with zero network requests. The
saved result path is `data/results/pre-phase8/fluid-usdc-usdt-25896003.json`.
The combined eight-adapter result is saved alongside it; both include the
current per-hash inventory and explicit remaining source exclusions.
