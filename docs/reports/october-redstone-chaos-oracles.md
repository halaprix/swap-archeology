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

## Added Avalanche Chaos and Ethereum Chronicle references

Chaos's Avalanche feed `0xC33FD9cC294371398a6C7880A05F6B039F3a138C`
reports `WETH / USD`, eight decimals, and is listed in
[BENQI's official price-feed registry](https://docs.benqi.fi/resources/contracts/price-feeds).
This is a separate series from the unresolved Ethereum Chaos feed.
For each Ethereum chart timestamp, select the latest Avalanche block at or before
that timestamp. Contiguous parent-linked headers and the following block bracket
that selection, preventing use of future chain state. Both block identities are
retained. Divide the Avalanche USD price by the existing Ethereum Chainlink
USDC/USD observation to preserve the chart's USDC-per-ETH units. This is a
cross-chain, mixed-provider reference, not a feed available to an Ethereum
contract at that block. The source describes WETH and may reflect its provider's
asset assumptions; no equivalence to Ethereum executable liquidity is asserted.

Chronicle's Ethereum feed `0x46ef0071b1E2fF6B42d36e5A177EA43Ae5917f4E`
reports `ETH/USD`, with 18 decimals. Its historical identity is also supported by
[Maker's March 2025 oracle migration](https://vote.makerdao.com/executive/template-executive-vote-eth-and-wsteth-oracle-migration-rate-changes-smart-burn-engine-parameter-update-bug-bounty-payout-aligned-delegate-compensation-atlas-core-development-payments-integration-boost-top-up-spark-proxy-spell-march-20-2025).
We call `readWithAge()` at each canonical Ethereum block and divide by same-block
Chainlink USDC/USD. The ScribeOptimistic implementation returns its finalized
current value: a newer optimistic update becomes readable after its challenge
period. This is neither the pending optimistic price nor the downstream Maker
OSM value. The returned age refers to the contract's stored update time, not
necessarily the original market observation time. `opChallengePeriod()` is
retained per block. Historical direct and Multicall reads succeeded without
altering caller permissions or contract state.

Regenerate in this order after the base reference and RedStone collectors:

```bash
uv run python -m scripts.collect_october_chaos_avalanche
uv run python -m scripts.collect_october_chronicle
```

Both accept `--offline` once their local caches are complete. Avalanche uses its
public C-chain RPC with twelve concurrent batches and bounded retries; Chronicle
uses the existing private Ethereum RPC configuration and Multicall cache. Raw
responses remain in ignored `outputs/`. Public exports contain only public feed
data and provenance. PancakeSwap's direct family line is hidden by default on
`/october-gap`, while its data and toggle remain available.

In the collected Chronicle window, the challenge period is 600 seconds throughout;
returned read ages range from 600 to 5376 seconds. The endpoints expose
$4017.80371455 and $3632.89 per ETH before USDC conversion. These older exposed
values should not be mistaken for contemporaneous executable market prices.

## Binance and CoinGecko market references

The October chart also includes two off-chain market references, styled as bold,
partly transparent dashed lines to distinguish them from on-chain oracles:

- **Binance ETH/USDC:** one-minute candle closes from the official public spot
  market-data API. Select the most recent *completed* candle at each Ethereum
  timestamp, never the closing price of a minute still in progress. Retain OHLC,
  close/completion timestamps and lag. No ETH/USDT substitution or dollar peg.
- **CoinGecko:** the public historical range API returned hourly ETH/USD samples
  at 20:00, 21:00 and 22:00 UTC. Only the latter two are selected in this window.
  Carry the latest observation forward and divide by same-block Chainlink
  USDC/USD. Do not treat the repeated per-block values as high-resolution market
  observations. Recorded timestamps do not establish when historical API data
  became available in real time. The hourly sampling cannot resolve this crash's
  minute-by-minute trough.

Regenerate after RedStone with
`uv run python -m scripts.collect_october_market_references`; `--offline` reuses
raw responses in ignored `outputs/october-market-references/`. No API key is
required by the public endpoints used for this export. All earlier series are
preserved.
