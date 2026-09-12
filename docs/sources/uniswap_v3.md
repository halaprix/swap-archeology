# Uniswap V3 — identity and activation only

**This family's pool discovery and `data/discovery/1/uniswap_v3.json` belong to another worker.** This note records only the identity/activation facts established by the inventory pass, independently of that worker.

**Deployment.** Factory `0x1F98431c8aD98523631AE4a59f267346ea31F984`. Creation block **12369621, verified exactly**: code present at 12369621, absent at 12369620 (`data/discovery-evidence/identity/creation_univ3_factory.json`). This matches the value the adapter worker recorded, arrived at independently.

**Status at the pins.** Code present at all five (`owner()` returned `0xf2371551fe3937db7c750f4dfabe5c2fffdcbf5a` at each).

**Discovery (for reference, not owned here).** `PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee, int24 tickSpacing, address pool)`, topic0 `0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118`, `fromBlock 12369621`. Enumerate `FeeAmountEnabled(uint24,int24)` topic0 `0xc66a3fdf07232cdd185febcc6579d408c241b47ae2f9907d84be655141eeaecc` rather than assuming the four familiar fee tiers are exhaustive.

**Quote reference (for reference, not owned here).** QuoterV2 at the same block, cross-checked against local tick math. Do not reuse this quoter for V4 or Ekubo: V4 has its own quoter and Ekubo's `SqrtRatio` is a uint96 with a scale selector, not a Q64.96 uint160.

**Open for the lead.** Nothing from this pass. Coordinate any change to the shared factory/activation facts with the V3 adapter worker.
