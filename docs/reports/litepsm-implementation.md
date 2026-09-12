# LitePSM implementation report

Implemented a bounded `DssLitePsm` adapter with no RPC calls. It describes a
two-phase snapshot: source getters first, then the DAI balance at the LitePSM
and gem balance at its `pocket`.

The model follows canonical `_sellGem` and `_buyGem` integer arithmetic,
including floor fees, `HALTED` direction flags, finite ERC-20 inventories, and
immutable state updates. DAI exact-input is deliberately restricted to values
that `buyGem` consumes exactly. A partial DAI transfer would violate the
evaluator's full-fill rule, so it raises `Unsupported` instead of presenting a
floored USDC output.

The model reads pocket USDC allowance because `buyGem` uses
`gem.transferFrom(pocket, usr, gemAmt)`. Circle FiatToken's inherited
`transferFrom` decrements allowance even from `uint256.max`, so the state does
too. State fields and inputs reject booleans, floats, negative values, and
values above `uint256.max`; only records with `config.model == "dss-lite-psm"`
are admitted.

Offline checks in `tests/test_litepsm.py` cover selectors, source getter and
dependent-read shapes, fee rounding, inventories and allowance, state updates,
halts, attainability, malformed ABI, failed/missing reads, protocol
conformance, trust-boundary failures, and wrong-pair rejection. No historical
raw call bundle or fill cross-check was
created here; the inventory remains `discovered_unsupported` until that
evidence is acquired and qualified by the lead.
