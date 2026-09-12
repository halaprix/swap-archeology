# October sell-side stablecoin connectors

The request is sell ETH/WETH collateral for USDC debt repayment. Existing
October quotes already run in that direction: fixed ETH input, maximize USDC
output. The relevant shortfall is `1 - (USDC received / ETH sold) /
(ETH oracle USD / USDC oracle USD)`. Reversing a buy quote is not equivalent
because fees and price impact are directional. No keeper policy or full sweep
was resumed for this change.

## Added routes

Curve's canonical 3pool (`0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7`)
now supplies all six DAI/USDC/USDT directions in the same shared pool state.
The legacy adapter is separate from NG: legacy fee rounding and admin-fee
balance accounting differ. `quote_exact_in` reproduces legacy `get_dy`; ordered
`swap` evaluation follows `exchange`, which can pay one USDC/USDT raw unit less
than that view. All reported routed output uses ordered `swap` evaluation.

The direct NG DAI/USDT inventory candidate is not established in this historical
window, so it was not substituted for 3pool. Exactly 254 block-hash-pinned
supplemental snapshots live under `outputs/october-connectors/`, separately
from the original collection. Identity comes from same-hash canonical Curve
registry `get_coins/get_decimals` and MetaRegistry `get_base_pool` responses.
`collection_supplement.py` rejects duplicate loaded pool capacities and combines
both evidence identities for cache fingerprints. No collection data were edited.

Maker/Sky LitePSM DAI→USDC was present, enabled, and had about 2.338 billion USDC
of spendable capacity at block 23550094. The problem was routing semantics:
`buyGem` requests exact USDC output, so arbitrary upstream 18-decimal DAI
amounts usually cannot all be consumed. The previous strict exact-input rule
therefore excluded most such PSM routes.

The supplemental October run opts into a narrowly bounded refund policy:
exactly one positive zero-fee LitePSM DAI→USDC swap may leave less than 1e12 DAI
wei (0.000001 DAI). That DAI is reported as `terminal_refund`, never included in
USDC proceeds. All original ETH/WETH input must still be spent. A zero-input
PSM step, later DAI production, unrelated intermediate balances, or a nonzero
PSM output fee cannot create this exception. Ordinary quote requests keep the
strict default. The chart reports the selected route's returned DAI explicitly.

At block 23550094, 1 WETH → DAI → PSM produces 3,807.041091 USDC and returns
211,768,636,185 wei DAI. The previous aggregate was 3,806.262932 USDC. This is
less than one USDC improvement, not an explanation of the broader ETH/oracle
gap. Stablecoin connector completeness and ETH-side depth are separate issues.

## Reproduce

- `.venv/bin/python scripts/october_connectors.py`
- Rebuild existing optional native helpers after core changes using
  `uv run --offline --with cython --with setuptools python scripts/build_perf_native.py`.
- `.venv/bin/python scripts/october_source_prices.py --supplement outputs/october-connectors --output-dir outputs/october-sources-connectors`
- `.venv/bin/python scripts/validate_october_connectors.py`

Subset `--blocks` runs fill per-block raw caches only. The final command assembles
all 254 observations before public export. The baseline October export remains
in `outputs/october-sources/` for comparison.

## Validation and limits

- 54/54 same-hash 3pool `get_dy` matches: three blocks, six directions, three
  sizes. Evidence: `outputs/october-connectors/qualification/curve-legacy-3pool.json`.
- Legacy source pinned to Curve commit
  `574f44027d089de0eac765f5a74ea5ae96aba968`; source SHA recorded with qualification.
- Native/reference parity of the real PSM route is stored in
  `outputs/october-connectors/native-parity.json`.
- Focused adapter, evaluator, solver, CLI and performance tests passed.

This remains historical model research with bounded search, not a promise of
atomic settlement or complete market coverage. Refund means retained/returned
DAI in the model; no live executor implementation is claimed. Selected contract
quotes qualify selected pool math; they do not prove deployed bytecode/source
compilation equivalence or qualify every route transaction.

Final export: 254 blocks × 6 cases = 1,524 quotes. 164 improved, none
regressed; 93 winners use PSM and 71 use 3pool. Maximum improvement is
2.1911683673 bps at block 23550121 for 1 ETH (3,739.854099 → 3,740.673564
USDC). All 93 PSM refunds are explicitly accounted for. See
`outputs/october-sources-connectors/validation.json`. The public October chart
now serves this supplemented export.
