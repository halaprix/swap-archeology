# Curve StableSwap-NG plain pools

`CurveNGAdapter` is a bounded local model for the plain StableSwap-NG factory
at `0x6a8cbed756804b16e05e741edabd5cb544ae21bf`.  It implements a pool only
when its exact snapshot hash has a Curve registry observation whose `base_pool`
is zero and whose `base_registries` includes that factory.  The observation's
coins and decimals must equal the `PoolRecord` tokens.  The adapter also reads
the factory's asset types and implementation address at that block; the latter
must be one of the two implementations observed in every five-pin probe
(`0xdcc91f930b42619377c200ba05b7513f2958b202` or
`0x933f4769dcc27fc7345d9d5975ae48ec4d0f829c`).

The initial model accepts two through eight coins where every factory asset type
is `0` and every token has an explicit `transfer_semantics[address] ==
"standard"` entry.  Raw stETH is always rejected, including when a factory
labels it type zero.  Type 1 oracle and type 3 ERC-4626 pools are explicitly
unsupported: `stored_rates()` alone does not establish their transfer/rate
semantics for future state transitions.  Metapools, rebasing pools, and unknown
transfer semantics are also unsupported.

## Reads and arithmetic

Each pool requests ten independent calls: `N_COINS`, `A`, `A_precise`,
`get_balances`, `stored_rates`, `fee`, `offpeg_fee_multiplier`, `admin_fee`,
and the factory's `get_pool_asset_types` and `get_implementation_address`.
Failed or malformed reads are unsupported.  In particular, the three
`stored_rates()` failures in each cached pin remain visible exclusions.

The port uses the pinned NG `get_D`, `get_y`, and dynamic-fee operations with
integer floors in source order.  It uses `A_precise()` for the invariant.  A
pool with `A_precise() != A() * 100` is rejected rather than silently using the
rounded `A()` value from `CurveStableSwapNGViews.get_dy`.

After an exact-input quote, the input balance rises by the full input.  The
output balance falls by user output plus the admin claim.  The admin claim
preserves Curve's two floors: first the output fee times `admin_fee`, then its
conversion from xp to token units.  Thus every hop carries one immutable
multi-coin state and has the pool id as its only capacity id; pair directions
are never independent copies of liquidity.

All state values and intermediates are checked as uint256.  A zero balance,
nonconvergence, underflow, division by zero, or an intermediate outside that
domain raises `Unsupported` rather than extrapolating past the source's
validated arithmetic domain.

## Pinned source

The source is Curve commit `911b5b45e4edafda96a74ec5f464a673c380456c` in
[the local manifest](../../data/protocol-sources/curve/manifest.json):
`CurveStableSwapNG.vy`, `CurveStableSwapNGViews.vy`, and
`CurveStableSwapNGMath.vy`.  The implementation code represents source math;
it does not itself qualify a pool for routing.  Admission still requires a
same-hash pool `get_dy` comparison recorded by the qualifier.
