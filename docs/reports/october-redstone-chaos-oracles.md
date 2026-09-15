# October 10, 2025: RedStone and Chaos references

The October chart includes RedStone for all 254 canonical Ethereum blocks
23549939–23550192 (21:14:11–22:04:59 UTC). Reads use the canonical block hashes;
these are end-of-block oracle answers, not executable swap quotes.

## RedStone

ETH/USD: `0x67F6838e58859d612E4ddF04dA396d6DABB66Dc4`.
The [official manifest pinned before the crash](https://github.com/redstone-finance/redstone-oracles-monorepo/blob/816de619f59f5b69d9dcc86a804ba479bfe3306d/packages/relayer-remote-config/main/relayer-manifests-multi-feed/ethereumMultiFeed.json)
identifies this Ethereum feed. Historical calls confirm eight decimals and
`RedStone Price Feed for ETH`. The manifest specifies a 0.5% deviation trigger
and a 24-hour heartbeat; neither promises a fresh answer every block.

For comparison in USDC per ETH, divide by same-block Chainlink USDC/USD at
`0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6`. The label explicitly names both
providers. USDC is not assumed to equal one dollar. Raw answers, timestamps,
update ages and the exact integer ratio accompany each chart value. Either feed
older than 24 hours yields a null price; this is a collection guard, not a claim
that younger data is suitable for trading.

| UTC | Block | RedStone ETH / Chainlink USDC | Existing Chainlink ETH / USDC | RedStone age |
|---|---:|---:|---:|---:|
| 21:14:11 | 23549939 | 3721.88 | 3716.85 | 24s |
| 21:35:23 | 23550044 | 3562.00 | 3765.11 | 504s |
| 22:04:59 | 23550192 | 3913.29 | 3906.91 | 12s |

There are 20 distinct RedStone updates in this window; update ages span
0–1140 seconds. The difference between feeds alone does not establish which
best tracked the contemporaneous executable market. Three independent direct
`latestRoundData` reads matched the collected answers and timestamps.

## Chaos / Edge: unresolved for ETH

No canonical Ethereum ETH/USD or USDC/USD Chaos address was verified for this
window. This is an unresolved discovery result, not proof no feed existed.
The chart includes an unavailable row with no numerical series.

Historical `description()` reads at both endpoints identify
`0x9Cf01269e491375DBe3C725927Aa025BAc47bEeB` as **ETHFI/USD**, eight decimals.
It is a different asset and must not be plotted as ETH/USD. See the
[verified implementation](https://etherscan.io/address/0xa17887fd35b14a4c6e6ec87458591934d444c)
and [official push-feed documentation](https://chaoslabs-c6cb6984.mintlify.app/oracles/price/push).
Chaos's [oracle CLI consumer](https://github.com/ChaosLabsInc/chainlink-oracle-cli/blob/62eded26346c50fe0331583699f43a289040f7fb/contracts/PriceConsumerV3.sol)
uses Chainlink ETH/USD; it is not a Chaos feed. Risk-parameter oracles likewise
are not substitutes for historical price rounds.

To complete Chaos coverage we need a provider-verified historical Ethereum
ETH/USD deployment, or authenticated historical price reports and their schema.

Follow-up checks on September 15, 2026:

- The [Edge Oracle Analyzer on Dune](https://dune.com/chaoslabsadmin/edge-oracle-analyzer)
  returned HTTP 403 for its dashboard data in a fresh browser. No query SQL or
  deployment addresses could be extracted; this does not establish feed absence.
- The Aave automation address `0x5CFc96c396325724D49dfF95Da103feDdbf48E05`
  is an automation wrapper. The actual hub is
  `0x95E3015c67EF62B866cC28ca5A9AB5017A55e336`, according to the
  [address book](https://github.com/aave-dao/aave-address-book/blob/main/src/MiscEthereum.sol).
  Both returned empty code at block 23549939.
- The configured Edge risk oracle `0x7ABB46C690C52E919687D19ebF89C81A6136C1F2`
  had code at that block, but the
  [agent interface](https://github.com/ChaosLabsInc/chaos-agents/blob/main/src/contracts/AgentConfigurator.sol)
  exposes per-agent `getRiskOracle(uint256)` for risk updates, not an ETH/USD
  price-feed getter.

## Reproduce

After generating the original October reference sidecar, run:

```bash
uv run python -m scripts.collect_october_external_oracles
# With an existing complete local RPC cache:
uv run python -m scripts.collect_october_external_oracles --offline
```

The extension preserves existing reference series and writes the bundled
`frontend/public/october-oracle-references.json`. A `--blocks` comma-separated
subset writes local evidence only. Raw responses remain under ignored
`outputs/october-external-oracles/`; RPC configuration stays local. Re-running
the original reference collector requires re-running this extension afterward.
