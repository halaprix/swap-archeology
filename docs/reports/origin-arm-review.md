# Independent Origin Lido ARM review

2026-09-08. Verdict: **PASS for the bounded source-rate quote model at the five
qualified hashes**, with the conservative capacity and settlement limits below.
The reviewer independently checked historical source, raw identity/ABI evidence,
63 state reads, 38 qualification cases, fresh 14-call read parity and offline
stateful arithmetic. No historical bytecode/source equivalence or atomic
settlement verdict is claimed.

Only this report was edited by the reviewer. No RPC, production changes,
transactions, commits, pushes, external knowledge writes or `evidence/` edits
were performed. The adapter, tests, acquisition and qualification scripts were
implemented by other contexts.

## Historical source and identity

Canonical repository: `<external-repos>/arm-oeth`. Its CodeGraph
command failed with `unable to open database file`, so the review used exact
historical Git objects rather than the incompatible current implementation.

| Historical source | Commit | SHA-256 |
| --- | --- | --- |
| `src/contracts/AbstractARM.sol` | `4d7dc50f7e661d4d9e40900c3e0e782e96396775` | `e1dc11fce4c3f5f8379a1a114fb7e104f8dc750703bbcc6f156de51e18ecb89e` |
| `src/contracts/AbstractARM.sol`, adds reserve getter | `0a4b1769e43c1c3bf0c4b129a373295e4a10fb11` | `7e6d53532351014fe45144ed7058d6f4352752e1675516560f1e2eb9f1245987` |
| `src/contracts/LidoARM.sol` | `4d7dc50f7e661d4d9e40900c3e0e782e96396775` | `72009f4aa3c37aa35a74c486df8458e8bf05a7cd0b106e85d522a0769256f1a7` |

The inheritance and proxy paths were also inspected. `LidoARM` selects WETH as
the liquidity asset/token0 and stETH as base asset/token1. The proxy's public
`implementation()` getter returns its EIP-1967 implementation slot.

The raw files under `data/discovery-evidence/origin-arm-layout/<hash>.json`
match the discovery record's exact block hash, number, implementation address
and getter-success observations:

| Block | Implementation | `getReserves()` | `paused()` | State reads / checks |
| --- | --- | --- | --- | --- |
| 23549991 | `0xec6fdcc3904f8dd6a9cbbbcc41b741df5963b42e` | unavailable | unavailable | 12 / 8 |
| 23550060 | `0xec6fdcc3904f8dd6a9cbbbcc41b741df5963b42e` | unavailable | unavailable | 12 / 8 |
| 23728292 | `0xec6fdcc3904f8dd6a9cbbbcc41b741df5963b42e` | unavailable | unavailable | 12 / 8 |
| 24356381 | `0xc0297a0e39031f09406f0987c9d9d41c5dfbc3df` | available | unavailable | 13 / 6 |
| 25896003 | `0x850da2e21f1f71479e2a307edab114777d9f6217` | available | false | 14 / 8 |

Unavailable getters remain failed raw observations, not invented zero values.
The adapter requests only getters observed at that exact hash and checks the
live saved implementation result against the mapping. Unknown hashes, wrong
block numbers/chains, malformed implementation mappings, mismatched tokens,
incorrect token chain/decimals and noncanonical pool identities fail closed.
These observations establish historical identities and ABI availability, not
a compiled-bytecode match to the source commits above.

## Arithmetic, queue capacity and ordering

Both historical source versions compute exact-input output as
`floor(amountIn * traderate / 1e36)`, using `traderate0` for WETH→stETH and
`traderate1` for stETH→WETH. The read rate already includes the operator's price
conversion; applying another inversion would be wrong. The LP performance fee
is not a per-swap fee. Multiplication and inventory additions retain checked
`uint256` rejection behavior.

The source transfers input before output. For outgoing WETH, outstanding LP
withdrawals are reserved: `queued - claimed`. `_requireLiquidityAvailable`
requires `output + outstanding <= raw WETH balance` when outstanding is
nonzero. Neither external withdrawal queues nor lending-market balances are
automatically available to this swap path. The adapter carries raw WETH and
the queue counters independently, so an earlier WETH input can fund a later
outgoing swap without releasing the reserved amount.

stETH inventory is carried in actual shares. Outgoing nominal stETH removes
`floor(output*S/P)` shares; incoming nominal stETH adds `floor(input*S/P)`.
The next balance is derived from updated shares. Global Lido totals remain
unchanged during these transfers, and are not labeled consumed capacity.

The nominal stETH reserve cap is deliberately conservative. For example, at
`P=121`, `S=100`, two ARM shares show a balance of two nominal units. A source
transfer requested for three units moves only two shares and can fit, while
this model rejects three against its nominal balance cap. Acceptance therefore
does not establish exhaustive maximum executable depth at rounding boundaries.

## Findings resolved during review

1. A raw WETH deficit against queued withdrawals previously admitted a zero
   output because the displayed reserve was clamped to zero. The source rejects
   even zero output in that state. The final adapter rejects this direction;
   WETH→stETH can still replenish the raw WETH balance.
2. A missing `implementation` key previously escaped as `KeyError`. It now
   raises `Unsupported`, retaining per-pool acquisition failure isolation.
3. Unconditionally reading `paused()` was incompatible with the first four
   pins. The final adapter uses explicit per-hash `paused_getter` observations.
   Pause presence is not itself a proven swap gate: commit
   `7ba96553b626d07125abdebd15572bb5fa4b51d8` first adds a pause modifier to LP
   deposit/redemption requests, while later incompatible `647d5ea` adds swap
   gating. Rejecting a true pause value is labeled a conservative model
   restriction. The observed fifth-pin value is false.

No remaining implementation blocker was found within this declared scope.

## Independent validation

```text
UV_CACHE_DIR=/tmp/swaparch-review-uv uv run pytest -q tests/test_origin_arm.py
6 passed in 0.14s
```

Source digests above were reproduced with
`git -C <external-repos>/arm-oeth show COMMIT:PATH | sha256sum`.
A separate `uv run python` check recomputed all 13 ABI selectors and used
`random.Random(20260908)` with exact `Fraction` arithmetic for 2,000 states in
both directions: 4,000 rate/output and immutable inventory checks passed.
Three explicit overflow boundaries passed. A funded three-step synthetic plan
replenished a queue-deficit ARM, sold back only newly available WETH, then
re-spent that WETH; the evaluator accepted the funded order and rejected the
unfunded order. Missing implementation and wrong block/chain cases failed as
`Unsupported`.

The reviewer decoded every `data/validation/origin-arm/<hash>.json` into a
fresh state, matched every call against the corresponding saved snapshot,
independently recomputed all 38 source-rate/capacity expectations and checked
actual share/raw-WETH deltas and unchanged queue obligations. All passed.
Block 24356381 has only one wei of nominal stETH reserve, hence six distinct
boundary cases rather than eight. The exact hashes are:

```text
23549991 0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623
23550060 0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a
23728292 0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc
24356381 0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb
25896003 0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5
```

Fresh calm parity is recorded at
`data/validation/multicall-parity/origin-arm/result.json`. The reviewer decoded
the actual aggregate3 request and response and matched all 14 successful raw
return values against separate individual-call caches. Every raw request uses
the same calm block hash. The recorded acquisition used three batch-client and
16 individual-client network requests; this review only read those artifacts.

## Quote versus settlement

There is no public ARM quoter in the reviewed source. Qualification is an
independent source-expression comparison over pinned historical state. Nominal
stETH output is the requested transfer/returned route amount, not a proven
recipient balance delta. Input transfer rounding likewise changes ARM shares
by the floored share amount. Caller balances/approvals, stETH transfer pause,
recipient preexisting shares, external token behavior, gas and atomic
downstream settlement are outside this model. A feasible composed plan must
not be presented as a proven atomic execution or recipient-balance guarantee.
