# Bounded crash slices and Chainlink comparison

Delivered 2026-09-09. The full sweep remains stopped. The local app exposes the
new static dataset at http://127.0.0.1:3007/crash-slices.

## Scope and reproduction

16 exact Ethereum block pins, 48 quotes: sell 1,000 or 100,000 **sUSDe tokens**,
or 100 WETH, into USDC. These are fixed token quantities, not dollar notionals.
All supported source models compete under the existing search settings:
10 grid parts, eight steps, beam 128, 5,000 expansions, with baseline incumbent.

| Window | Blocks |
|---|---|
| February 21, 2025 sUSDe event | 21895643, 21895668, 21895692, 21895693, 21895718, 21895743 |
| October 10, 2025 ETH crash | 23549922, 23549972, 23550022, 23550044 |
| November 4, 2025 ETH crash | 23728242, 23728292, 23728342 |
| January 31, 2026 ETH crash | 24356676, 24356725, 24356775 |

The February series spans ten minutes on each side of the known extreme, with
five-minute observations and the preceding block, twelve seconds earlier.
Other series use ten-minute spacing around the previously recorded ETH oracle
minimum. October also includes the previously observed single-pool/oracle gap
at 23550044. Every requested sampling timestamp matched an actual cached block
exactly; no block-number interpolation was used. These selected observations
do not establish the worst case across each crash.

```bash
.venv/bin/python scripts/crash_slices.py --offline --benchmark-scopes
.venv/bin/python -m pytest -q tests/test_crash_slices.py
```

The measured 48-quote offline run took **28.8 seconds**. This uses the existing
optional native benchmark build and its scoped optimizations in one process;
it does not alter the normal CLI/API or restart any sweep worker. A compatible
native build is required for `--benchmark-scopes`; omit it for ordinary Python.
The oracle cache was acquired separately through bounded read-only mainnet calls.
The runner checkpoints after each quote and reuses matching source/settings,
collection evidence and raw-report identities. The verification replay recomputed
zero quotes. Missing cache evidence fails explicitly in offline mode.

## Price definitions and oracle provenance

Execution price is total USDC output / full input token amount, before gas.
Oracle price is token/USD divided by USDC/USD at the **same block hash**.
Deviation is `(execution / oracle - 1) × 10,000` bps: negative means execution
is below the oracle reference. The single-pool series is the best direct pool
in this reconstructed universe, not necessarily the old study's selected pool.

| Feed | Mainnet proxy |
|---|---|
| ETH/USD (WETH uses ordinary 1:1 wrapping) | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419` |
| USDC/USD | `0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6` |
| sUSDe/USD | `0xFF3BC18cCBd5999CE63E788A1c250a88626aD099` |

The [Chainlink directory](https://reference-data-directory.vercel.app/feeds-mainnet.json)
identifies this sUSDe feed as `dex_state_price`, product subtype `Reference`.
It is distinct from the calculated sUSDe/USD feed. Pinned descriptions,
decimals and `latestRoundData` were read successfully for all three feeds at all
16 blocks. Positive answers, nonzero update times, and no future updates are
required. Decimals are normalized independently. The displayed age is the
**older of the two feed updates**, with no freshness cutoff or fresh-price claim.
Current directory heartbeat metadata is not treated as historical configuration.
See the [Chainlink interface documentation](https://docs.chain.link/data-feeds/api-reference).

## Selected observations

| Block and input | Aggregated USDC per input token | Oracle cross-rate | Deviation |
|---|---:|---:|---:|
| 21895693, 100,000 sUSDe | 1.15329297091 | 1.15435686495 | −9.2163 bps |
| 23550044, 100 WETH | 3372.35264328 | 3765.11253374 | −1043.1558 bps |

At the February peak, the model routes through sUSDe/USDT V4, then Curve/V3
USDT/USDC, and returns 115,329.297091 USDC. The oracle age is 46,320 seconds.
Fresh independent V4 quoter calls match the model exactly for the first 30,000
sUSDe hop and a separate 100,000 sUSDe single-hop quote. Those two checks do not
prove the entire split route's atomic settlement or independently validate its
repeated-pool execution sequence.

The earlier rescue-policy study's sUSDe→sDAI→DAI→USDC result remains valid for
its stated route and redemption-par sizing. This new model explores more venues;
its fixed-token inputs are not directly comparable with that study's dollar-par
notionals. Neither a pool quote nor Chainlink alone establishes best execution.

## Validation and artifacts

- All 48 reports pass exact integer balance replay, full-fill checks, pinned hash
  alignment, and independent rational recomputation of prices, oracle cross-rates,
  deviations and both feed ages. Routing made zero RPC requests.
- Two extreme-case full reports match unoptimized Python byte-value semantics.
- Four focused Python tests pass, including non-par USDC cross-rates for both
  WETH and sUSDe. Frontend tests, TypeScript, lint and production build pass.
- Browser checks cover pair/amount/window filters, exact peak output, route
  details and a 390px viewport without page overflow.
- Public dataset: `frontend/public/crash-slices.json`; full reports and raw RPC
  cache: `outputs/crash-slices/`; selection: `selected-pins.json`; independent
  checks: `validation.json`, `v4-peak-quoter-check.json`, `browser-check.json`.

Collection-model-only status is displayed throughout. Unresolved/unavailable
families remain visible; there is no all-venue completeness, global optimality,
fresh oracle, live-trade or complete per-block qualification claim. Historical
browsing packages statically for later Vercel deployment; nothing was deployed.
