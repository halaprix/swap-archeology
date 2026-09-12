# Uniswap V4

The adapter models only hookless (`hooks == address(0)`), static-fee V4 pools. It
computes the singleton PoolId as `keccak256(abi.encode(PoolKey))`, checks it
against the `Initialize` event, reads `StateView`, and performs a bounded
immutable tick walk. Native ETH (`address(0)`) remains distinct from WETH.
Nonzero hooks and `0x800000` dynamic fees are discovered but unsupported.

## Pinned qualification

The cached qualification is five hash-pinned, root-warm replays with zero RPC
requests. Counts are shortlisted candidates after the deterministic positive-
liquidity top five per token pair, followed by StateView and independent
exact-input V4Quoter checks in both directions.

| block | block hash | static candidates | shortlisted | supported | parity |
| ---: | --- | ---: | ---: | ---: | --- |
| 23549991 | `0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623` | 48 | 39 | 27 | 4/4 |
| 23550060 | `0x9fcbc31c9f6018f087160d1733f76997dd57c225170f70651b24a65e80bbad4a` | 48 | 39 | 27 | 4/4 |
| 23728292 | `0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc` | 50 | 40 | 27 | 4/4 |
| 24356381 | `0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb` | 65 | 49 | 28 | 4/4 |
| 25896003 | `0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5` | 88 | 59 | 33 | 4/4 |

The five raw reports are in `data/validation/uniswap_v4/`; their per-pin
coverage records include the exact block hash, bounds, parity artifact, and
zero-request counters. The complete discovery/filter reference is
`data/discovery-evidence/uniswap-v4/0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5.json`:
14 full token-filter queries from block 21688329 through 25896003, 116074
Initialize identities, 89705 hookless/static records, and 69 discovery requests.
This discovery report is reused for earlier pins only after filtering by the
pool's creation block.

The qualification bounds are `min_liquidity = 1` and `max_per_pair = 5`.
They describe the trusted token/filter set and the top-five candidate budget;
they do not establish complete qualified coverage for the full Initialize
universe. The current replay is historical cached evidence, with no settlement
or live transaction claim.

## Math and source scope

V4 uses the V3 tick and sqrt-price algorithms, but its step and fee semantics
are separate. In `slot0`, directional protocol fee uses the low 12 bits for
zero-for-one and high 12 bits for one-for-zero. The effective fee combines
protocol and LP fees as
`protocol + lp - floor(protocol * lp / 1e6)`, with V4's exact-input rounding
and initialized-tick crossing rules. The model reads the current LP fee from
`StateView.getSlot0`; it does not treat a dynamic-fee PoolKey field as static.

Canonical source is the pinned
`<external-repos>/uniswap-v4-core` commit
`e50237c43811bd9b526eff40f26772152a42daba`, specifically
`src/types/PoolId.sol`, `src/libraries/LPFeeLibrary.sol`,
`src/libraries/ProtocolFeeLibrary.sol`, `src/libraries/SwapMath.sol`, and
`src/libraries/Pool.sol`. The adapter's supported scope is hookless static
pools with finite StateView/tick evidence and positive exact-input Quoter
checks. Hook behavior, dynamic fees, native ETH/WETH wrapping, and unbounded
pair discovery remain outside that model.

Discovery is `uv run python scripts/uniswap_v4_run.py <block>` and is offline by
default. `--online` permits cache misses; it scans `Initialize` with pool id in
topic 1 and currencies in topics 2/3. Discovery alone never admits a pool.
Qualification batches StateView static reads, selects the deterministic
positive-liquidity top five per token pair before dependent tick reads, then
requires two positive exact-input V4Quoter matches in each direction and raw-
byte StateView batch-versus-individual parity. The V4Quoter is an individual
reverting `eth_call`, not a staticcall-shaped multicall operation.

The discovery event has exactly three indexed parameters (`id`, `currency0`,
`currency1`); `hooks` is in the data payload. The PoolManager, StateView, and
V4Quoter addresses and event topics remain in the inventory notes.
