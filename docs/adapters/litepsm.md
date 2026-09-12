# LitePSM adapter

`src/swaparch/adapters/litepsm.py` models the permissionless `DssLitePsm`
singleton at `0xf6e72db5454dd049d0788e411b06cfaf16853042`.  It is qualified as a source-derived reserve quote model at all five historical
pins. See [independent review](../reports/litepsm-review.md): 45 pinned checks,
6,000 independent arithmetic cases, and nine fresh batch/direct reads matched.
This is not a historical settlement or transfer-execution verdict.

## Canonical source

- Local canonical checkout: `<external-repos>/dss-lite-psm`
- Commit: `dbf0022225f645f5697e5517d0cf00810471bccf`
- Contract: `src/DssLitePsm.sol`
- SHA-256: `502eed38778ac29758959cadbb3d2f36aa3af21144e7374483795581a6279ce8`
- Relevant functions: `_sellGem` / `_buyGem` (lines 336–401 in this checkout)

The adapter reads the contract's source-exact getters `gem()`, `dai()`,
`pocket()`, `to18ConversionFactor()`, `tin()`, and `tout()` in its first
batch. Its dependent batch reads `DAI.balanceOf(litePsm)`,
`gem.balanceOf(pocket)`, and `gem.allowance(pocket, litePsm)`. There is no
`usdc()` or `pocketUsdc()` LitePSM getter.

## Exact-input and capacity semantics

`sellGem` is USDC exact-input: it transfers the full USDC amount into the
pocket, then returns `gross - floor(gross * tin / WAD)` DAI. It is capped by
the LitePSM's ERC-20 DAI balance.

`buyGem` is USDC exact-output: it transfers exactly
`gross + floor(gross * tout / WAD)` DAI and sends the chosen USDC amount from
the pocket. Core routes require exact input, so DAI→USDC is accepted only when
the supplied DAI equals this source formula for an integer USDC amount. Other
inputs raise `Unsupported` with their residual; the adapter never reports a
floor while burning or hiding DAI.

`buyGem` calls `gem.transferFrom(pocket, usr, gemAmt)`, so pocket balance alone
is insufficient. The adapter requires and carries forward the pocket's USDC
allowance to LitePSM. Circle's deployed FiatToken implementation inherits
`FiatTokenV1.transferFrom`, which decreases allowance even from
`type(uint256).max`; the model follows that behavior. The adapter accepts only
`config.model == "dss-lite-psm"`, so it cannot price a legacy PSM or wrapper
under the shared family label.

Amounts and state inventories must be non-boolean `uint256` integers. A zero
amount is an evaluator-level no-op except that a halted LitePSM direction still
raises, matching the contract's pre-transfer halt check. Token pause,
blacklist, and user-approval conditions remain execution dependencies outside
this quote model.

The state carries both inventories forward after each swap and exposes the
`dai_buffer` and `pocket_usdc` capacity identifiers keyed by the LitePSM
address. Wrapper/converter aliases
are deliberately outside this adapter; when admitted they must use the same
capacity identities.

The adapter restricts records and decoded immutables to the Ethereum DAI/USDC
LitePSM and its known pocket. The USDC allowance transition follows pinned
[Circle FiatTokenV1.transferFrom](https://github.com/circlefin/stablecoin-evm/blob/fc85788bc7c23cefe3df1a757133048bfddadeaa/contracts/v1/FiatTokenV1.sol#L258),
which subtracts even an allowance initially equal to uint256.max. Files and hashes
are saved in `data/protocol-sources/circle/manifest.json`. Historical proxy
implementation matching and settlement remain unverified; this is a declared
source-model assumption. Unknown gas stays unknown.
