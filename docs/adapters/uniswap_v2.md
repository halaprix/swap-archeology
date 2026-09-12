# Uniswap V2 adapter — bounded status

This module discovers pairs from the canonical Ethereum factory
`0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f` (creation block `10000835`) and
models one canonical V2 pair with the hard-coded `997/1000` fee. Router02
`0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D` is an arithmetic cross-check only.

`PairCreated` scans use topic0
`0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9`, topic1
and topic2 for the sorted token addresses, from block `10000835`. The discovery
helper emits both requested address orderings and records the filter, creation
block, block hash, transaction hash, and log index. A token added later needs a
new backfill from the factory creation block.

## State and quote semantics

For every pair/block the adapter requests `getReserves()`, `token0()`,
`token1()`, and `balanceOf(pair)` from both underlying tokens. The actual V2
`swap` invariant computes input from post-transfer balances and only then calls
`_update`; Router02's `getAmountOut` calculates from stored reserves. Therefore
a reserve/balance mismatch is `Unsupported`, rather than silently modelling a
donation, a pending `sync`, a rebase, or a transfer-tax token as a normal pool.

The state returns a new pair state after each exact-input swap. It uses
`amountOut = floor(amountIn * 997 * reserveOut /
(reserveIn * 1000 + amountIn * 997))`, updates the input reserve by the full
input amount, and enforces `uint256` intermediate arithmetic plus the pair's
`uint112` post-update reserve bound. Zero input returns zero; a positive input
that rounds to zero output is unsupported because V2 `swap` would reject it.

The adapter requires an explicit `PoolRecord.config["transfer_semantics"]`
mapping that marks both addresses as `"standard"`. Raw stETH is always excluded:
its balance rebases, so matching one block's reserves and balances does not make
it safe to carry as a V2 reserve. This rule does not imply that a wrapper around
stETH is safe; the discovery runner records a curated standard-transfer quote assumption for each named endpoint. It does not attest execution transfers or account permissions.

New discovery records start as `discovered_unsupported` with empty semantics.
The discovery runner marks named non-stETH endpoints with the standard-transfer
quote assumption. Routing admission additionally requires both Router02 references
to match at the requested block hash and reserves to equal token balances.

## Canonical pinned sources

The local manifest pins the five traced files and their hashes:

| Source | Commit | SHA-256 |
| --- | --- | --- |
| `v2-core/contracts/UniswapV2Pair.sol` | `4dd59067c76dea4a0e8e4bfdda41877a6b16dedc` | `43a5421b31415868367b62bfa161ca10bcee03778873faad905f5a3e2cce9cbd` |
| `v2-core/contracts/UniswapV2Factory.sol` | `4dd59067c76dea4a0e8e4bfdda41877a6b16dedc` | `e0cef3e874a68cbcc5986451b1fec180ba5ff5699f27a256b7c10fafefe36b99` |
| `v2-core/contracts/libraries/SafeMath.sol` | `4dd59067c76dea4a0e8e4bfdda41877a6b16dedc` | `4b1c95ff75de7342e0fadff58064820a4eb7c2fcb422a75b4994980ce8e216ae` |
| `v2-periphery/contracts/libraries/UniswapV2Library.sol` | `ed24991304291297c3b4a52818d02f46a17aa9a2` | `4f83e9334f833568fa47b36e9ceca435f6c2962760a0596b043c4e538d0fd9f2` |
| `v2-periphery/contracts/UniswapV2Router02.sol` | `ed24991304291297c3b4a52818d02f46a17aa9a2` | `acabe6d6c9c275aa5c27d8ed59b3d23d21616523071043e89eea7bca474babb8` |

See [manifest.json](../../data/protocol-sources/uniswap_v2/manifest.json) for
the exact raw GitHub URLs. The five files establish factory identity/event
shape, token ordering, pair balance-based invariant and `uint112` update,
checked arithmetic, and Router02's use of the library for path quotes.

Historical qualification is recorded at all five pins: 208 size/direction quotes
matched 416 raw Router02 `getAmountOut` and `getAmountsOut` responses. Ten pairs
qualify at each of the first three pins, eleven at the last two; discovery found
14 pairs. Five adapter reads also matched fresh Multicall/direct requests at calm.
See [independent review](../reports/v2-review.md), raw records in
`data/validation/uniswap_v2/`, and `data/validation/multicall-parity/uniswap_v2/`.
These checks establish reserve quote arithmetic, not settlement or transfer semantics.
