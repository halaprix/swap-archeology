# Five Binance ETH selloffs: oracle comparison

Completed 725 pinned Ethereum mainnet samples: five 24-hour windows, every 10 minutes (145 points each). RPC collection took 173.52 seconds; every reference returned successfully.

## Selection

Scanned all 18,096 hourly ETHUSDT candles from 2024-08-16 through 2026-09-08. Ranked hourly open-to-low decline and greedily selected five events with UTC dates at least seven days apart. These are the largest independent events under this metric and date range, not the five worst crashes of all time. The start excludes dates before the chosen 1inch deployment.

Raw archives and SHA256 checksums: `outputs/five-crash-oracles/binance/archives/`. Official source: https://github.com/binance/binance-public-data. Independently reproduced selection from all hourly archives and checked all 53 archive checksums.

## Results

Largest sampled shortfall below Chainlink, in percent. Each column can attain its minimum at a different time. Chainlink is ETH/USD divided by USDC/USD; all prices are USDC per ETH/WETH.

| Date UTC | Binance hourly drop | 1inch below CL | UniV3 300s below CL | Binance below CL |
|---|---:|---:|---:|---:|
| 2025-02-03 | 15.00% | -2.21% | -7.00% | -2.12% |
| 2025-02-25 | 6.74% | -0.38% | -0.50% | -0.44% |
| 2025-04-07 | 8.40% | -1.15% | -0.56% | -0.48% |
| 2025-06-21 | 6.60% | -0.57% | -0.96% | -0.47% |
| 2025-10-10 | 11.27% | -4.67% | -6.62% | -0.39% |

At 2025-10-10 21:40:59 UTC, block 23550072: Chainlink 3706.320145, Binance 3704.69, 1inch 3546.600142, UniV3 300s 3460.938887, UniV3 60s 3503.068511. Chainlink ETH feed age 132 seconds; USDC feed age 228 seconds. Binance completed-minute close lag 59 seconds. Binance agreed closely with Chainlink at this sample while on-chain references were lower. This observation alone does not establish the cause of the venue difference.

At 2025-02-03 02:16:59 UTC, block 21762944: Chainlink 2452.104466, Binance 2442.61, 1inch 2487.530005, UniV3 300s 2280.422574. The 5-minute historical average can diverge sharply during a fast rebound; this is not interchangeable with instantaneous execution.

## Timing and interpretation

- Each window spans 12 hours before and after the close-second of the minute containing the selected hourly low; the exact trade time within that minute is unknown.
- Every chosen block satisfies block.timestamp <= target < next.timestamp, with raw headers and hashes retained.
- Binance uses the latest completed minute: openTime + 60 <= block.timestamp. The raw close timestamp is floored for storage, but is not used to admit an unfinished minute. Every matched candle lag is below 60 seconds.
- UniV3 uses the USDC/WETH fee500 pool at 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640, via observe() and canonical mean-tick math. Both 60s and 300s references are exported.
- 1inch is the deployed OffchainOracle getRate(WETH, USDC, false), a composite reference whose configuration can include oracle adapters; it is not an executable quote or guaranteed independent of Chainlink.
- Ten-minute sampling misses brief extremes. In particular, it does not replace the earlier dense October scan or contradict its larger gaps. Sample counts over thresholds are not measured durations.
- These are size-independent references, not simulated ETH sales. ETH/USDC on Binance and WETH/USDC on mainnet are separate markets.

## Verification and artifacts

Root independently checked all 725 block/hash bounds, Binance alignment, raw Chainlink answer ratios and ages, raw 1inch scaling, UniV3 observation deltas against high-precision exponentiation, source statuses, extrema, and 500bps counts. Targeted selector and collector tests: 18 passed. Verification record: `outputs/five-crash-oracles/independent-verification.json`.

- Local chart: http://127.0.0.1:3007/five-crash-oracles/index.html
- Per-event charts: crash-1.html through crash-5.html, chronological.
- Full JSON/CSV, raw multicall evidence, selection and archive provenance: `outputs/five-crash-oracles/`.
- Dataset SHA256: `47182c973e576dc0c07539b00257a15c42009e194ebff1f7dacc8313f85e9879`.
- Old full scan and keeper remain stopped; no transactions, commits or pushes.
