# October 1 ETH spikes and stablecoin connectors

At block 23550094 (2025-10-10 21:45:23 UTC), the 1 ETH aggregate is
3,792.660179 USDC via V4 ETH/USDT pool
0x50b00c1a5a8e582ec808e97e71598cd135206a9f9c548eab2ed73659e7ee5fa8,
then Curve USDT/USDC 0x4f493b7de8aac7d55f71853688b1f7c8f0243c85.
The 1 WETH aggregate is 3,806.262932 USDC via V3 WETH/DAI
0x60594a405d53811d3bc4766596efd80fd545a270, then V3 DAI/USDC
0x5777d92f208679db4b9778590fa3cab3ac9e2168. Chainlink cross-rate is
3,802.2334362347024. Direct USDC pools stay below these aggregate quotes.

The ETH/USDT first leg returns, per ETH, 3,790.461456 USDT for 1 ETH,
3,755.3324 for 10 ETH, and 3,194.9346 for 100 ETH. Its 1 ETH quote was
3,510.9786 in the preceding block and 3,558.7111 in the following block.
This is a transient pool-state price change with limited depth at the higher
price, not merely a chart interpolation. The exact transaction causes are not
established here.

At block 23550060, the winning ETH route instead uses V4 ETH/DAI
0x042a5b2c816d473338d6d1fff7d4e2f74a4bf6d9517e74538f3778d0693aef69
and the same V3 DAI/USDC pool. The first leg averages 3,686.4905 DAI/ETH
at 1 ETH but 2,070.5087 at 100 ETH. Aggregation redirects larger trades.

Ran scripts/october_spike_check.py: **30/30 exact integer matches** against
same-hash mainnet V3/V4 Quoter or Curve get_dy calls, using 10 network requests.
Checks cover both legs of the three routes above and first-leg sizes 1/10/100
at each spike and adjacent blocks. Evidence: outputs/october-spikes/checks.json
and rpc-cache/. Distinct pool calls, not full atomic transaction settlement;
this does not qualify every spike or explain why arbitrage did not persist.

At 23550094, loaded source states already include DAI/USDC LitePSM
0xf6e72db5454dd049d0788e411b06cfaf16853042; USDT/USDC Curve and two Fluid
pools; DAI/USDT and other stable pairs on Uniswap V2/V3/V4. These are eligible
intermediaries despite not having standalone ETH-price lines on the direct
source chart. No loaded state has USDS as a token; Spark is discovered but
unsupported. Only four Curve pools are usable, despite 432 discovered records.
USDS conversion routes and broader Curve coverage remain genuine gaps, but
missing connectors do not explain away the independently reproduced spikes.
