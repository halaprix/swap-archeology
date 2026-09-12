# Independent Lido wrapper review

2026-09-08. Scope: the canonical Ethereum wstETH/stETH wrapper adapter,
its offline tests, source provenance, historical quote qualification and
read parity. The reviewer did not implement the adapter. Only this report is
reviewer-owned; no RPC, transactions, production changes, commits, pushes,
remote knowledge writes or `evidence/` edits were performed.

Verdict: PASS for the bounded getter-quote and wrapper-backing model at the
five qualified hashes. Five-pin getter comparisons, fresh read parity, source
arithmetic and immutable backing changes passed independently. Actual stETH
recipient balances and atomic route settlement remain unverified.

## Source and conclusions

The copied Lido sources in `data/protocol-sources/lido/manifest.json` are pinned
to commit `ea6fa222004b88e6a24b566a51e5b56b0079272d`. The reviewer checked all
three saved SHA-256 digests and read the complete
[WstETH contract](https://github.com/lidofinance/core/blob/ea6fa222004b88e6a24b566a51e5b56b0079272d/contracts/0.6.12/WstETH.sol),
plus the share conversion and transfer paths in the saved StETH contract.
WstETH source SHA-256 is
`254a8bfab14c30ac30d36fbe0174fe424798a67276abb1c52e139af28899cae0`;
StETH SHA-256 is
`2079d4f2c046a4f202a98612741f56855cf037a392052e43afdb2e2160aa0bf7`.

Let `P` be total pooled ether and `S` total shares. Wrap returns and mints
`floor(stETH_in*S/P)` wstETH. The subsequent stETH transfer moves exactly that
many underlying shares to the wrapper. Supply and backing therefore increase
by the minted amount. Unwrap burns the wstETH input and returns
`q = floor(wstETH_in*P/S)` as its quote, then transfers
`floor(q*S/P)` actual shares from the wrapper. The adapter correctly decreases
backing by this second floor, rather than by the burned wstETH amount.

Finite wstETH supply and actual backing shares constrain unwrap separately.
The state is immutable, carries both changes forward, and checks multiplication
and addition against `uint256` overflow. It rejects zero denominators as an
unsupported model state. Zero input returns the view-getter quote zero; the
contract's actual wrap and unwrap entrypoints reject zero, as documented.

Wrapper transfers do not change `P` or `S`. Removing the read-only share-rate
capacity ID is correct: merely reading the same global exchange rate is not
consuming shared liquidity. The mutable wrapper backing retains one singleton
capacity ID. Other contracts that actually use the same backing must still
share the corresponding state.

The adapter validates chain, family, canonical wrapper deployment and pool ID,
token addresses/order, model discriminator, token decimals, the snapshot chain,
and the wrapper's stETH getter. Failed, missing, malformed-hex and malformed-ABI
responses become `Unsupported`, as do unsupported directions and invalid
integer amounts. ETH staking and asynchronous withdrawals are excluded.

## Independent offline checks

```text
UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q tests/test_lido.py
6 passed in 0.12s
```

The reviewer recomputed all five getter selectors using Keccak of their ABI
signatures: five of five match. A separate `uv run python` check used
`random.Random(20260908)` and exact `Fraction` expressions for 2,000 independent
rate/amount combinations. It checked wrap and unwrap quotes, supply/backing
deltas, the double floor, wrap/unwrap round-trip dust and unchanged global
totals. Four additional cases checked overflow of share multiplication, pooled
ether multiplication, wrapper supply addition and backing-share addition.
All passed. Source digests also matched all three manifest entries.

All five historical bundles under `data/validation/lido/<hash>.json` were
independently decoded into new states and matched against persisted snapshots.
Every bundle has five successful state reads and eight historical getter
comparisons: both directions at inputs 1, `10**9`, `10**18` and `100*10**18` wei.
The reviewer independently generated every getter selector/argument, checked
its raw per-hash RPC cache entry, decoded the result, and recomputed the quote
using `Fraction`. All 25 state reads and 40 getter comparisons passed.

| Block | Exact snapshot hash |
| --- | --- |
| 23549991 | `0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623` |
| 23550060 | `0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a` |
| 23728292 | `0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc` |
| 24356381 | `0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb` |
| 25896003 | `0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5` |

At every pin, a separate reviewer check accepted a full-supply unwrap and
rejected supply plus one, and rejected a synthetic zero-backing state for a
positive unwrap. It checked immutable supply/backing deltas for all getter
comparisons and accepted `MAX_SUPPORTED_INPUT-1` while rejecting
`MAX_SUPPORTED_INPUT`. RPC calls were explicitly forbidden during this local
reproduction; network requests remained zero. These capacity checks concern
the adapter's source-derived state model, not executed token transfers.

Fresh calm-block parity is saved in
`data/validation/multicall-parity/lido/result.json` with separate batch and
individual raw caches. The reviewer decoded the actual `aggregate3` calldata
and returned tuples, then compared all five success/return byte strings with
the independently issued `eth_call` cache entries. Five of five match; all
raw calls use the same calm `blockHash`. The saved acquisition counts are
three batch-client and seven individual-client network requests. This review
performed only local decoding and did not issue those calls.

## Findings and quote boundary

The initial tests and adapter documentation still listed the removed share-rate
capacity ID; the lead synchronized them during review.

The initial input bound rejected `2**128-1` while its diagnostic and docs
described `<2**128`. The pinned StETH conversion source uses checked
multiplication/division and contains no `uint128` guard. The reviewer asked the
lead to align the implemented bound and wording. The final diagnostic and docs
now say `<2**128-1`, and the source comment describes a conservative model
limit, rather than a proven guard in that historical implementation.

The returned unwrap amount is a getter/return value, not an observed recipient
balance delta. stETH represents balances through shares, so another floor and
the recipient's existing shares can affect that delta. Consequently, evaluator
feasibility using this quote is quote composition only; it does not prove
actual token conservation at a downstream settlement contract. Caller balances,
allowances, transfer pause state, recipient accounting, gas and atomic route
execution remain unverified. The copied repository commit alone does not prove
which stETH proxy implementation ran at each historical block.
