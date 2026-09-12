# Uniswap V3 top-five discovery

Calm pin: block `25896003` (`0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5`). The scan covered 67,746 unique pool identities and 67,783 `PoolCreated` logs through 12 endpoint/index-position filters, with 383 successful RPC requests. The result contains 26 unique selected pools across the six study tokens.

Ranking uses the selected study token's raw ERC-20 `balanceOf(pool)` in smallest units. It is a same-token balance ranking; it is not USD TVL, executable depth, or a quote admission decision. Pool state and token metadata were enriched from cached calls. Every selected row has complete metadata and matches its discovery token addresses.

| Study token | Pool | Token balance | Created block | Pool tokens | Positive active liquidity |
| --- | --- | ---: | ---: | --- | :---: |
| WETH | `0x4e68ccd3e89f51c3074ca5072bbac773960dfa36` | 27620779220244587524365 | 12375326 | WETH/USDT | yes |
| WETH | `0xcbcdf9626bc03e24f779434178a73a0b4bad62ed` | 14755477698441435926901 | 12369821 | WBTC/WETH | yes |
| WETH | `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640` | 12909665738366214544228 | 12376729 | USDC/WETH | yes |
| WETH | `0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8` | 8988831870962947399169 | 12370624 | USDC/WETH | yes |
| WETH | `0xc5c134a1f112efa96003f8559dba6fac0ba77692` | 8409831465601947969982 | 18685541 | WHITE/WETH | yes |
| USDC | `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640` | 73237947811701 | 12376729 | USDC/WETH | yes |
| USDC | `0x3416cf6c708da44db2624d63ea0aaef7113527c6` | 17759671725243 | 13609065 | USDC/USDT | yes |
| USDC | `0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d` | 12673748838381 | 20508739 | AUSD/USDC | yes |
| USDC | `0x99ac8ca7087fa4a2a1fb6357269965a2014abc35` | 11024899356261 | 12376048 | WBTC/USDC | yes |
| USDC | `0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8` | 8016433966631 | 12370624 | USDC/WETH | yes |
| USDT | `0x4e68ccd3e89f51c3074ca5072bbac773960dfa36` | 41135331126674 | 12375326 | WETH/USDT | yes |
| USDT | `0x3416cf6c708da44db2624d63ea0aaef7113527c6` | 16312252720410 | 13609065 | USDC/USDT | yes |
| USDT | `0x9db9e0e53058c89e5b94e29621a205198648425b` | 13193379694394 | 12376091 | WBTC/USDT | yes |
| USDT | `0x4a25dbdf9629b1782c3e2c7de3bdce41f1c7f801` | 12399460218357 | 18519904 | USDM/USDT | yes |
| USDT | `0x56534741cd8b152df6d48adf7ac51f75169a83b2` | 10764889804207 | 12601886 | WBTC/USDT | yes |
| DAI | `0xc2e9f25be6257c210d7adf0d4cd6e3e881ba25f8` | 1777071891584083515592671 | 12369854 | DAI/WETH | yes |
| DAI | `0x5777d92f208679db4b9778590fa3cab3ac9e2168` | 707447893047087667168669 | 13605124 | DAI/USDC | yes |
| DAI | `0xae750560b09ad1f5246f3b279b3767afd1d79160` | 701221539006716167853689 | 18772088 | PEAS/DAI | yes |
| DAI | `0x48da0965ab2d2cbf1c17c09cfb5cbe67ad5b1406` | 613247776795135375941339 | 15142784 | DAI/USDT | yes |
| DAI | `0x60594a405d53811d3bc4766596efd80fd545a270` | 513306060161391720049636 | 12375738 | DAI/WETH | yes |
| wstETH | `0x109830a1aaad605bbf02a9dfa7b0b92ec2fb7daa` | 1593759038615641713324 | 15384250 | wstETH/WETH | yes |
| wstETH | `0x703a177fcb4def281d180d3619a5edbae67ec7b5` | 3519855939141018495 | 23506288 | boxETH/wstETH | yes |
| wstETH | `0x4622df6fb2d9bee0dcdacf545acdb6a2b2f4f863` | 1600723102760244278 | 16065412 | wstETH/USDC | yes |
| wstETH | `0x58ee92c366f197e434a1267a38e28e8c08cd27e7` | 1209343770979291457 | 18729031 | wstETH/HARAMBE | yes |
| wstETH | `0x526389df2dcc8c5f7af69e93ad9e0d8fc21799f6` | 839284593917776383 | 16478531 | wstETH/fstETH-B8C6 | yes |
| sUSDe | `0x7c45f7ff7ddeac1af333e469f4b99bbd75ee5495` | 660907291266430352382 | 19543819 | wstETH/sUSDe | no |
| sUSDe | `0x7eb59373d63627be64b42406b108b602174b4ccc` | 144523332224809302280 | 21876736 | sUSDe/USDT | yes |
| sUSDe | `0x8609b6a1f4c57f9ecc26144b362880fe73da91c3` | 42381094858771021833 | 19663768 | wstETH/sUSDe | no |
| sUSDe | `0x867b321132b18b5bf3775c0d9040d1872979422e` | 29784414421829128041 | 19582555 | sUSDe/USDT | yes |
| sUSDe | `0x4e0f19d310c31be732bae1a53b465087fdbdeab8` | 1025369229702152608 | 20164252 | sUSDe/USDC | no |

Three selected sUSDe pools have zero active liquidity at the pin despite positive token balances. This is a state observation only; it does not remove those pools from the balance ranking or establish executable depth.

Evidence is in [`top5-25896003 report`](../../data/discovery-evidence/uniswap-v3-top5/top5-25896003-0xf2c9645adf0b9f757541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5.json) and the cached enrichment report beside it. Offline validation reran:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python scripts/uniswap_v3_top_pools_enrich.py --report data/discovery-evidence/uniswap-v3-top5/top5-25896003-0xf2c9645adf0b9f757541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5.json
```

No missing metadata or address mismatches were present in the 26 enriched rows. The canonical inventory was not modified.
