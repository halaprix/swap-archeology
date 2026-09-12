# Lido wstETH wrapper adapter

`src/swaparch/adapters/lido.py` prices only the Ethereum mainnet wstETH wrapper
at `0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0`, between canonical stETH
`0xae7ab96520de3a18e5e111b5eaab095312d7fe84` and wstETH. It rejects the Lido
`submit` and withdrawal-queue records; neither is this conversion edge.

## Source and reads

The canonical source is Lido's public
[`WstETH.sol`](https://github.com/lidofinance/core/blob/ea6fa222004b88e6a24b566a51e5b56b0079272d/contracts/0.6.12/WstETH.sol)
at commit `ea6fa222004b88e6a24b566a51e5b56b0079272d` (SHA-256
`254a8bfab14c30ac30d36fbe0174fe424798a67276abb1c52e139af28899cae0`).
It delegates the public quote getters to stETH share conversion. The adapter
reads, pinned to one block:

- `stETH.getTotalPooledEther()` and `stETH.getTotalShares()` for the rate;
- `wstETH.stETH()` to verify the immutable stETH identity;
- `wstETH.totalSupply()` and `stETH.sharesOf(wstETH)` for finite wrapper backing.

For input in the model's conservative `< 2**128 - 1` domain, returned quotes are
the getter formulas exactly:

```
wstETH = floor(stETH * totalShares / totalPooledEther)
stETH  = floor(wstETH * totalPooledEther / totalShares)
```

The global share rate is read-only during wrapping. The wrapper exposes
`lido:<wstETH>:wrapper_backing`; this is a singleton backing state,
not an AMM reserve. Wrap adds minted shares to supply and backing. Unwrap
requires both outstanding wstETH supply and enough wrapper-held stETH shares,
then burns the input and removes the shares actually transferred.

## Quote versus settlement

The unwrap quote remains `getStETHByWstETH`'s requested transfer amount. In
the same transaction, `WstETH.unwrap` calls `stETH.transfer(quotedAmount)`, and
stETH converts that token amount back to shares with another floor. A recipient
may therefore receive a balance delta below the quote after the second
share/token conversion. The state carries that second-floor share consumption
for wrapper backing, but deliberately does not relabel the public getter quote
as an observed recipient balance.

No execution or atomic-settlement claim follows from this quote. Trader
balances, approvals, stETH transfer pause state, and the recipient's actual
pre/post share balance are outside the snapshot model and remain to be
validated by the route/execution layer. Zero quote inputs return zero to match
the view getters, although `wrap(0)` and `unwrap(0)` themselves revert.

The withdrawal queue is not modeled as ETH output: it mints a withdrawal NFT
and finalizes asynchronously. There is no instantaneous stETH-to-ETH edge.
