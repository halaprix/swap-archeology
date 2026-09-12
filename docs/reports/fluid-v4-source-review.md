# Fluid DEX T1 / Uniswap V4 bounded source review

Date: 2026-09-08. This is a read-only source and cached-evidence review of
the current adapters. It does not establish live RPC or executable settlement;
all support conclusions below are limited to their stated historical scope.

## Provenance

| source | pinned commit |
| --- | --- |
| `external/fluid-contracts-public` | `a9949b48ba1247d4f478cd0acb40896b5c8bf3f8` |
| `external/uniswap-v4-core` | `e50237c43811bd9b526eff40f26772152a42daba` |

CodeGraph was used for each external repository before direct source reading.
The Fluid review covers T1 `coreModule/core/main.sol`, its core helpers, and
the DEX-reserves resolver. The V4 review covers `PoolManager`, `Pool`,
`SwapMath`, `ProtocolFeeLibrary`, `LPFeeLibrary`, and `Slot0`.

## Fluid T1: bounded `ADDRESS_DEAD` quote, not settlement

T1 is two coupled virtual curves, rather than a constant-product pair. Smart
collateral derives its curve from the Liquidity supply position and smart debt
derives its curve from the debt position. Where both are enabled, source
`_swapRoutingIn` splits input by the square roots of each imaginary-reserve
product; fee and output floors apply independently to each leg before their
outputs are summed. `getPoolReservesAdjusted` supplies the resolver-normalized
1e12 data used for those curves and the current withdraw/borrow limits.

The current adapter reconstructs this bounded curve quote, uses the
revenue-cut bits of `dexVariables2`, applies the packed old-price and
same-block-center oracle checks, preserves per-leg flooring, and returns a
single-use post-quote state. That restriction is appropriate: it avoids
claiming a second swap can be priced without advancing Liquidity and oracle
state.

An immutable execution transition would additionally require the whole packed
DEX state (including price/oracle and range-shift fields), both token Liquidity
records and exchange-price words, the affected global/user limits and balances,
and exact post-operation utilization. Actual `swapIn` deposits/repays its
input through `Liquidity.operate`, withdraws/borrows its output through another
operation, validates utilization, and persists the oracle. Those are shared
across DEX pools that use the same Liquidity layer/token. The evaluator's
`fluid_liquidity:<layer>:<token>` capacity identity is conservative because it
rejects a multi-state plan instead of fabricating that shared post-state.

The resolver's `estimateSwapIn(..., ADDRESS_DEAD)` deliberately exits after
curve routing and real-reserve checks but before both Liquidity operations,
utilization validation, and oracle persistence. It is therefore evidence for
the initial curve quote only. The adapter's oracle-move guard is a stricter
execution-oriented admission condition than the resolver quote; it cannot be
expected to match every successful `ADDRESS_DEAD` result.

### Five cached pins

All reports declare the same limited scope: “T1 resolver-normalized
`ADDRESS_DEAD` quote checks. No Liquidity operation, utilization, oracle
persistence, or post-trade settlement admission.” The cached comparison has no
unequal successful local outputs.

| block | hash prefix | checks | exact positive matches | resolver-zero/local refusal | nonzero resolver/local refusal |
| ---: | --- | ---: | ---: | ---: | ---: |
| 23,549,991 | `0x0a9587d3` | 140 | 105 | 35 | 0 |
| 23,550,060 | `0x9fcbc31c` | 140 | 107 | 33 | 0 |
| 23,728,292 | `0x31531ed6` | 144 | 110 | 34 | 0 |
| 24,356,381 | `0x386830fe` | 156 | 115 | 40 | 1 |
| 25,896,003 | `0xf2c9645a` | 184 | 125 | 59 | 0 |
| **total** |  | **764** | **562** | **201** | **1** |

The sole nonzero reference/local-refusal row is block 24,356,381, pool
`0xd0fd46555eeb69fad117db59a1b6713cf234097c`, USDC to BEEF, input 1,000,000.
The resolver returned `1002936054866000000`; the local state refused “swap
would exceed source oracle move limit.” This is a false negative for the
resolver-estimator comparison, explained by the execution-time guard above; it
must remain visible in qualification totals rather than being counted as a
match. It does not establish that the full on-chain swap would settle, because
the missing Liquidity and utilization transition can independently reject.

Verdict: Fluid can receive bounded quote qualification at the five listed
historical pins, but only as a one-swap candidate. It must retain the explicit
limitations on repeated swaps, shared-liquidity allocation, and settlement.

### Resolver coverage and qualification inventory

The five raw validation files now contain the complete `getAllPools()` result
for the selected resolver at each pin. The resolver epoch changes at block
`23881741`; the legacy resolver is used before that boundary and the current
resolver at and after it.

| pin | block hash prefix | resolver epoch | resolver | unique pools from `getAllPools()` | quote-qualified pools |
|---:|---|---|---|---:|---:|
| 23,549,991 | `0x0a9587d3` | legacy | `0xc93876c0…881a27c` | 39 | 27 |
| 23,550,060 | `0x9fcbc31c` | legacy | `0xc93876c0…881a27c` | 39 | 28 |
| 23,728,292 | `0x31531ed6` | legacy | `0xc93876c0…881a27c` | 42 | 29 |
| 24,356,381 | `0x386830fe` | current | `0x05bd8269…1bf16ff` | 43 | 27 |
| 25,896,003 | `0xf2c9645a` | current | `0x05bd8269…1bf16ff` | 50 | 30 |

Each pin has four independent batch-versus-individual raw-call parity checks
(`poolReservesAdjusted`, `constantsView2`, `variables`, and `variables2`), and
all 20 checks matched. Across the five reports there are 562 positive exact
resolver/local quote matches and 201 resolver-zero exclusions. One additional
positive resolver result was refused locally at pin 24,356,381 for pool
`0xd0fd46555eeb69fad117db59a1b6713cf234097c` (USDC to BEEF, input 1,000,000)
because of the local oracle-move guard; it is retained as an excluded false
negative. There were no unequal successful outputs.

The family inventory now records this as `supported` for the bounded T1 model.
Fluid DEX Lite and the separate DEX V2 product remain unresolved products, not
silently folded into T1. The model remains single-use: repeated swaps and
plans touching shared Liquidity-layer/token capacity are refused until a
route-level state transition exists. No result here establishes transfer,
atomic settlement, or a global Fluid pool universe claim.

## Uniswap V4: source-aligned bounded static-pool walk

For a zero-hook, static-LP-fee pool, the state walk tracks the relevant core
semantics: directional packed protocol fees (low 12 bits for 0-to-1), the
combined protocol/LP fee, exact-input fee rounding and partial-step remainder,
initialized-tick crossings, and the zero-for-one `tickNext - 1` update. It
also rejects a finite bitmap boundary rather than treating loaded words as
physical capacity. Zero current liquidity is not by itself invalid: core may
cross initialized ticks into a later active range.

The admission gates match source limits for this bounded implementation:
nonzero hooks and the dynamic-LP-fee flag are excluded; tick spacing is limited
to PoolManager's positive `int16` range; static LP fees are at most 1,000,000.
Hook callbacks and dynamic fees can change execution and need an execution
environment, so no source-equivalence claim applies to them.

The qualification script and `UniV4State.swap` both bound exact input to
`uint128.max`, so every positive local exact-input quote is in the V4Quoter
ABI domain. Malformed hexadecimal and malformed ABI StateView replies now map
to `Unsupported`: this includes slot 0, liquidity, bitmap words, and tick
liquidity. The regression fixture with a valid `getSlot0` and one-byte
`getTickBitmap` (`0x00`) now raises `Unsupported` as intended.

The 100%-LP-fee boundary is valid source input. `SwapMath` consumes the whole
exact input as a fee and returns zero output. If price begins at an initialized
tick, core may still cross/preemptively shift the tick while price is unchanged;
the adapter's walk has the same behavior. This was confirmed by the pinned
source path and a local fee-boundary exercise.

The immutable `UniV4State` constructor itself only verifies contiguous bitmap
coverage. The loader currently validates price/tick bounds, configured LP fee,
and `uint128` liquidity, so this is not a raw-StateView path today; any
additional constructor entry point should enforce those invariants or stay
private.

### Five cached V4 pins

The qualification reports at `data/validation/uniswap_v4/` use the anchored,
complete later `Initialize` discovery report, filter it by creation block, then
limit candidates to trusted-token hookless/static pools. They first screen
StateView liquidity and take a deterministic maximum of five pools per token
pair. This is not a claim of global V4 coverage.

| block | hash prefix | active trusted candidates | shortlisted | quote-qualified pools | quote checks | exact local/Quoter outputs | local refusal before Quoter |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 23,549,991 | `0x0a9587d3` | 48 | 39 | 27 | 234 | 208 | 26 |
| 23,550,060 | `0x9fcbc31c` | 48 | 39 | 27 | 234 | 208 | 26 |
| 23,728,292 | `0x31531ed6` | 50 | 40 | 27 | 240 | 215 | 25 |
| 24,356,381 | `0x386830fe` | 65 | 49 | 28 | 294 | 253 | 41 |
| 25,896,003 | `0xf2c9645a` | 88 | 59 | 33 | 354 | 306 | 48 |
| **total** |  |  |  | **142 pin-qualified rows** | **1,356** | **1,190** | **166** |

Every successful Quoter result matched the local quote exactly: 742 were
positive and 448 were zero; there were no numerical mismatches and no snapshot
acquisition failures. Each pin also has four individual-versus-batched
StateView raw-byte checks (slot 0, liquidity, one bitmap word, and one tick),
for 20 passing checks in total.

The 166 local refusals occurred before a V4Quoter request, so they are bounded
coverage exclusions rather than Quoter failures: 164 require a bitmap word
outside the loaded window and two exhaust the loaded state. The state must
continue to refuse those amounts rather than extrapolating liquidity.

Verdict: the hookless/static adapter receives bounded historical quote
qualification for the listed pin-qualified rows. It remains unsupported for
hooked or dynamic-fee pools, untrusted-token or unshortlisted records, inputs
outside `uint128`, and swaps that require unloaded tick words. This does not
establish settlement behavior, hook semantics, or a global V4 pool universe.

## Local verification

`UV_CACHE_DIR=/tmp/swaparch-uv-review uv run pytest -q tests/test_fluid_dex.py
tests/test_uniswap_v4.py` passed: `10 passed in 0.22s`.

The verification above used only local source and cached evidence. No RPC
calls, external writes, commits, or implementation edits were made by this
review.
