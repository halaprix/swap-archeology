# Repeated 0x source sampling

2026-09-09T13:29:00.183141+00:00 to 2026-09-09T13:30:56.850690+00:00: 24 rounds, four sizes, 96/96 HTTP200 liquid responses. Median per-size request intervals 4.99–5.04 seconds. Reported blocks 25940117–25940126. Requests within a round are sequential, not an atomic block snapshot.

Current indicative WETH→USDC routes only. No transactions. `fills` contains from/to token addresses, source labels and proportionBps; no pool address or fee tier. Counts below mean route presence, not volume share. Consecutive-hop proportions are not independent allocations. A two-minute sample is bounded discovery evidence, not exhaustive source coverage.

| WETH | Sources (appearances / 24) | Intermediaries |
|---|---|---|
| 1 | Uniswap_V3 24/24, Ekubo 15/24, Uniswap_V4 13/24, PancakeSwap_V3 10/24, Cypher_V4 5/24 | USDT |
| 10 | Uniswap_V3 24/24, PancakeSwap_V3 21/24, Uniswap_V4 19/24, Cypher_V4 19/24, Lista_Stable 19/24, Ekubo_V3 5/24 | USDT, WBTC |
| 100 | Uniswap_V3 24/24, Uniswap_V4 24/24, Lista_Stable 24/24, Fluid 17/24, PancakeSwap_V3 14/24 | WBTC, USDT |
| 1000 | Uniswap_V4 24/24, Curve 24/24, Lista_Stable 24/24, Uniswap_V3 24/24, Fluid 24/24, Maker_PSM 24/24 | WBTC, USDT, USDS |

## Priorities

1. WBTC connectors: WBTC appears in every 100/1000-WETH sample. Twelve historical Uniswap V3 connector pools were verified in the earlier discovery report; collect and qualify them.
2. USDS connections: USDS appears in every 1000-WETH sample. Add converter/wrapper and discover relevant historical AMM pools while sharing the existing PSM reserve.
3. PancakeSwap V3: present in 21/24 10-WETH and 14/24 100-WETH samples. Nine historical pool identities already verified; adapter qualification remains.
4. Ekubo and Cypher_V4: additional labels exposed by repeated sampling. Exact version/deployment/pool mapping and October eligibility unresolved. Do not identify the generic Ekubo label with V2 solely from its name.
5. Current Lista_Stable and Ekubo_V3 are useful current-route leads, but the specific deployments checked previously had no code at the October start block. Do not add those deployments to October replay.

No Bebop or 0x_RFQ fills appeared in these 96 indicative responses; that does not establish their absence from firm quotes or other routing products.

Bebop is not an active historical source adapter.

Raw responses and summary: `outputs/0x-sampling-20260909/`. Reusable sampler: `scripts/zeroex_sample.py --key-file PRIVATE_PATH --output NEW_DIRECTORY --rounds 24 --interval 5`. Authentication stays local; temporary credential file removed after artifact redaction check.

Validation: `pytest -q tests/test_zeroex_sample.py` — 1 passed; Ruff passed. Check covers four request sizes per round, raw-response retention/redaction and stopping on HTTP429 without retries. Existing adapter registry imports with Bebop absent.

[0x getPrice schema](https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getprice). See also [historical discovery](0x-liquidity-discovery.md).
