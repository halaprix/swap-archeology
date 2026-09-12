"""Fluid DEX T1 exact-input, pre-operation quote model.

Fluid T1 routes an input across distinct smart-collateral and smart-debt
imaginary-reserve curves.  It is deliberately not a constant-product pair.
The public resolver's ``estimateSwapIn`` uses ``ADDRESS_DEAD``: it returns the
swap result before Liquidity ``operate``, utilization verification, and oracle
persistence.  This adapter therefore quotes one pre-operation swap exactly and
refuses reuse of its returned state rather than inventing post-trade reserves.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isqrt

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import NATIVE_ETH, Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

WAD_6 = 10**6
WAD_8 = 10**8
WAD_27 = 10**27
UINT96_MAX = (1 << 96) - 1
UINT128_MAX = (1 << 128) - 1
UINT256_MAX = (1 << 256) - 1
MIN_INPUT_RAW = 100
MIN_INPUT_ADJUSTED = WAD_6
ORACLE_LIMIT = 5 * 10**16
MINIMUM_LIQUIDITY_SWAP = 10**4
NATIVE_SENTINEL = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
EARLIEST_RESOLVER = "0xb387f9c2092cf7c4943f97842887ebff7ae96eb3"
EARLIEST_RESOLVER_CREATED_BLOCK = 21596670
OLD_RESOLVER = "0xc93876c0eed99645dd53937b25433e311881a27c"
OLD_RESOLVER_CREATED_BLOCK = 22487434
NEW_RESOLVER = "0x05bd8269a20c472b148246de20e6852091bf16ff"
NEW_RESOLVER_CREATED_BLOCK = 23881741


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def protocol_token(address: str) -> str:
    """Map Fluid's on-chain native sentinel to the shared native-token identity."""
    address = norm_address(address)
    return NATIVE_ETH if address == NATIVE_SENTINEL else address


def resolver_for_block(block_number: int) -> str:
    if block_number >= NEW_RESOLVER_CREATED_BLOCK:
        return NEW_RESOLVER
    if block_number >= OLD_RESOLVER_CREATED_BLOCK:
        return OLD_RESOLVER
    if block_number >= EARLIEST_RESOLVER_CREATED_BLOCK:
        return EARLIEST_RESOLVER
    raise Unsupported(f"Fluid DEX resolver is unproven before block {EARLIEST_RESOLVER_CREATED_BLOCK}")


SEL_POOL_RESERVES_ADJUSTED = _selector("getPoolReservesAdjusted(address)")
SEL_CONSTANTS_VIEW_2 = _selector("constantsView2()")
SEL_READ_STORAGE = _selector("readFromStorage(bytes32)")
assert (SEL_POOL_RESERVES_ADJUSTED, SEL_CONSTANTS_VIEW_2, SEL_READ_STORAGE) == (
    "0xbd964d38", "0x1595cbd3", "0xb5c736e4",
), "Fluid DEX selector table drifted"

TAG_RESERVES = "fluid_dex:poolReservesAdjusted"
TAG_CONSTANTS_2 = "fluid_dex:constantsView2"
TAG_VARIABLES = "fluid_dex:variables"
TAG_VARIABLES_2 = "fluid_dex:variables2"

# Resolver ``PoolWithReserves`` returned by ``getPoolReservesAdjusted``.
POOL_RESERVES_TYPES = [
    (
        "(address,address,address,uint256,uint256,(uint256,uint256,uint256,uint256),"
        "(uint256,uint256,uint256,uint256,uint256,uint256),"
        "((uint256,uint256,uint256),(uint256,uint256,uint256),(uint256,uint256,uint256),(uint256,uint256,uint256)))"
    )
]


def _uint(value: object, what: str, maximum: int = UINT256_MAX) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise Unsupported(f"Fluid DEX {what} must be a uint256 integer")
    return value


def _checked_product(*values: int, what: str) -> int:
    product = 1
    for value in values:
        if value and product > UINT256_MAX // value:
            raise Unsupported(f"Fluid DEX {what} exceeds the supported uint256 range")
        product *= value
    return product


def _trunc_div(numerator: int, denominator: int) -> int:
    """Solidity signed division truncates toward zero, unlike Python ``//``."""
    if denominator <= 0:
        raise Unsupported("Fluid DEX routing denominator is not positive")
    return numerator // denominator if numerator >= 0 else -((-numerator) // denominator)


@dataclass(frozen=True)
class FluidDexState:
    """One T1 pool's resolver-normalized, 1e12 adjusted curve state."""

    record: PoolRecord
    token0: Token
    token1: Token
    token0_numerator_precision: int
    token0_denominator_precision: int
    token1_numerator_precision: int
    token1_denominator_precision: int
    fee: int
    center_price: int
    dex_variables: int
    dex_variables2: int
    block_timestamp: int
    collateral: tuple[int, int, int, int]
    debt: tuple[int, int, int, int, int, int]
    withdrawable: tuple[int, int]
    borrowable: tuple[int, int]
    smart_collateral_enabled: bool
    smart_debt_enabled: bool
    paused: bool
    executed: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("token0 numerator precision", self.token0_numerator_precision),
            ("token0 denominator precision", self.token0_denominator_precision),
            ("token1 numerator precision", self.token1_numerator_precision),
            ("token1 denominator precision", self.token1_denominator_precision),
            ("fee", self.fee),
            ("center price", self.center_price),
            ("dex variables", self.dex_variables),
            ("dex variables2", self.dex_variables2),
            ("block timestamp", self.block_timestamp),
            *( ("collateral reserve", value) for value in self.collateral),
            *( ("debt reserve", value) for value in self.debt),
            *( ("withdrawable limit", value) for value in self.withdrawable),
            *( ("borrowable limit", value) for value in self.borrowable),
        ):
            _uint(value, name)
        if not self.token0_numerator_precision or not self.token0_denominator_precision:
            raise Unsupported("Fluid DEX token0 precision must be positive")
        if not self.token1_numerator_precision or not self.token1_denominator_precision:
            raise Unsupported("Fluid DEX token1 precision must be positive")
        if self.fee >= WAD_6:
            raise Unsupported("Fluid DEX fee must be below 1e6")
        if not self.center_price:
            raise Unsupported("Fluid DEX center price must be positive")
        if not (self.smart_collateral_enabled or self.smart_debt_enabled):
            raise Unsupported("Fluid DEX has neither smart collateral nor smart debt enabled")

    def tokens(self) -> tuple[Token, ...]:
        return (self.token0, self.token1)

    def capacity_ids(self) -> tuple[str, ...]:
        liquidity = self.record.config.get("liquidity")
        if not isinstance(liquidity, str):
            raise Unsupported(f"{self.record.pool_id} has no Fluid Liquidity owner identity")
        liquidity = norm_address(liquidity)
        return (
            self.record.pool_id,
            f"fluid_liquidity:{liquidity}:{self.token0.address}",
            f"fluid_liquidity:{liquidity}:{self.token1.address}",
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return None

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        return self._quote(token_in, token_out, amount_in)[0]

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, FluidDexState]:
        amount_out, _details = self._quote(token_in, token_out, amount_in)
        # The resolver quote exits through ADDRESS_DEAD before Liquidity.operate,
        # utilization checks, and oracle persistence; do not synthesize a false
        # post-trade T1/Liquidity state from the pre-operation curve data.
        return amount_out, replace(self, executed=True)

    def _direction(self, token_in: Address, token_out: Address) -> bool:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if (token_in, token_out) == (self.token0.address, self.token1.address):
            return True
        if (token_in, token_out) == (self.token1.address, self.token0.address):
            return False
        raise Unsupported(
            f"pair {token_in}->{token_out} is not Fluid DEX {self.token0.address}/{self.token1.address}"
        )

    def _quote(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, tuple[int, int]]:
        zero_for_one = self._direction(token_in, token_out)
        _uint(amount_in, "exact input")
        if self.executed:
            raise Unsupported("Fluid DEX post-trade curve/Liquidity state is not reconstructed")
        if self.paused:
            raise Unsupported("Fluid DEX swap and arbitrage are paused")
        if amount_in < MIN_INPUT_RAW or amount_in > UINT128_MAX:
            raise Unsupported("Fluid DEX input is outside source swap limits")
        numerator, denominator = self._precision(zero_for_one)
        adjusted = _checked_product(amount_in, numerator, what="input normalization") // denominator
        if not MIN_INPUT_ADJUSTED <= adjusted <= UINT96_MAX:
            raise Unsupported("Fluid DEX normalized input is outside source swap limits")

        col_in, col_out, col_real_in, col_real_out = self._curve_values(self.collateral, zero_for_one, False)
        debt_in, debt_out, debt_real_in, debt_real_out = self._curve_values(self.debt, zero_for_one, True)
        if adjusted > (col_in + debt_in) // 2:
            raise Unsupported("Fluid DEX input exceeds half of combined imaginary input reserves")
        col_input, debt_input = self._route(adjusted, col_out, col_in, debt_out, debt_in)
        col_output = self._amount_out(col_input, col_in, col_out)
        debt_output = self._amount_out(debt_input, debt_in, debt_out)

        output_numerator, output_denominator = self._precision(not zero_for_one)
        withdrawable = _checked_product(self.withdrawable[1 if zero_for_one else 0], output_numerator,
                                        what="withdrawable normalization") // output_denominator
        borrowable = _checked_product(self.borrowable[1 if zero_for_one else 0], output_numerator,
                                      what="borrowable normalization") // output_denominator
        if col_output > withdrawable:
            raise Unsupported("Fluid DEX collateral output exceeds current withdrawable limit")
        if debt_output > borrowable:
            raise Unsupported("Fluid DEX debt output exceeds current borrowable limit")
        if col_input:
            self._verify_real_reserves(zero_for_one, col_real_in, col_real_out, col_input, col_output)
        if debt_input:
            self._verify_real_reserves(zero_for_one, debt_real_in, debt_real_out, debt_input, debt_output)
        self._verify_oracle_move(zero_for_one, col_in, col_out, debt_in, debt_out,
                                 col_input, col_output, debt_input, debt_output)

        # Solidity converts each route component independently before summing.
        amount_out = (
            _checked_product(col_output, output_denominator, what="collateral output denormalization") // output_numerator
            + _checked_product(debt_output, output_denominator, what="debt output denormalization") // output_numerator
        )
        if not amount_out:
            raise Unsupported("Fluid DEX exact input rounds to zero output")
        return amount_out, (col_output, debt_output)

    def _precision(self, token0: bool) -> tuple[int, int]:
        return ((self.token0_numerator_precision, self.token0_denominator_precision)
                if token0 else (self.token1_numerator_precision, self.token1_denominator_precision))

    def _curve_values(self, curve: tuple[int, ...], zero_for_one: bool, debt: bool) -> tuple[int, int, int, int]:
        if debt:
            real0, real1, imaginary0, imaginary1 = curve[2:]
            enabled = self.smart_debt_enabled
        else:
            real0, real1, imaginary0, imaginary1 = curve
            enabled = self.smart_collateral_enabled
        if not enabled:
            return 0, 0, 0, 0
        return ((imaginary0, imaginary1, real0, real1) if zero_for_one
                else (imaginary1, imaginary0, real1, real0))

    def _route(self, total: int, col_out: int, col_in: int, debt_out: int, debt_in: int) -> tuple[int, int]:
        if not self.smart_collateral_enabled:
            return 0, total
        if not self.smart_debt_enabled:
            return total, 0
        if not all((col_out, col_in, debt_out, debt_in)):
            raise Unsupported("Fluid DEX enabled curve has zero imaginary reserves")
        root_col = isqrt(_checked_product(col_out, col_in, 10**18, what="collateral routing root"))
        root_debt = isqrt(_checked_product(debt_out, debt_in, 10**18, what="debt routing root"))
        denominator = root_col + root_debt
        routing = _trunc_div(debt_in * root_col + total * root_col - col_in * root_debt, denominator)
        if routing <= 0:
            return 0, total
        if routing >= total:
            return total, 0
        return routing, total - routing

    def _amount_out(self, amount_in: int, reserve_in: int, reserve_out: int) -> int:
        if not amount_in:
            return 0
        if not reserve_in or not reserve_out:
            raise Unsupported("Fluid DEX enabled curve has zero imaginary reserves")
        after_fee = _checked_product(amount_in, WAD_6 - self.fee, what="fee adjustment") // WAD_6
        return _checked_product(after_fee, reserve_out, what="output numerator") // (reserve_in + after_fee)

    def _verify_real_reserves(
        self, zero_for_one: bool, real_in: int, real_out: int, amount_in: int, amount_out: int
    ) -> None:
        if amount_out > real_out:
            raise Unsupported("Fluid DEX output exceeds real reserves")
        if zero_for_one:
            # Source _verifyToken1Reserves(token0After, token1After, center, 1e4).
            minimum = ((real_in + amount_in) * self.center_price) // (WAD_27 * MINIMUM_LIQUIDITY_SWAP)
            if real_out - amount_out < minimum:
                raise Unsupported("Fluid DEX token1 real reserves would be too low")
        else:
            # Source _verifyToken0Reserves(token0After, token1After, center, 1e4).
            minimum = ((real_in + amount_in) * WAD_27) // (self.center_price * MINIMUM_LIQUIDITY_SWAP)
            if real_out - amount_out < minimum:
                raise Unsupported("Fluid DEX token0 real reserves would be too low")

    def _verify_oracle_move(
        self, zero_for_one: bool, col_in: int, col_out: int, debt_in: int, debt_out: int,
        col_input: int, col_output: int, debt_input: int, debt_output: int,
    ) -> None:
        revenue_cut = WAD_8 - (self._variables2_revenue_percent() * self.fee)
        credited_collateral = col_input * revenue_cut // WAD_8
        credited_debt = debt_input * revenue_cut // WAD_8
        if credited_collateral > credited_debt:
            reserve_in, reserve_out = col_in, col_out
            amount_in = credited_collateral
            amount_out = col_output
        else:
            reserve_in, reserve_out = debt_in, debt_out
            amount_in = credited_debt
            amount_out = debt_output
        if not reserve_in or not reserve_out or amount_out >= reserve_out:
            raise Unsupported("Fluid DEX dominant curve has invalid post-swap reserves")
        if zero_for_one:
            new_price = (reserve_out - amount_out) * WAD_27 // (reserve_in + amount_in)
        else:
            new_price = (reserve_in + amount_in) * WAD_27 // (reserve_out - amount_out)
        old_oracle_price = self._previous_oracle_price()
        if not old_oracle_price:
            raise Unsupported("Fluid DEX packed prior oracle price is zero")
        if self.block_timestamp == ((self.dex_variables >> 121) & ((1 << 33) - 1)):
            previous_center = self._packed_price(81)
            if not previous_center:
                raise Unsupported("Fluid DEX packed prior center price is zero")
            if (self.center_price < (WAD_8 - 1) * previous_center // WAD_8
                    or self.center_price > (WAD_8 + 1) * previous_center // WAD_8):
                raise Unsupported("Fluid DEX center price is outside same-block source range")
        price_diff = 10**18 - old_oracle_price * 10**18 // new_price
        if price_diff > ORACLE_LIMIT or price_diff < -ORACLE_LIMIT:
            raise Unsupported("Fluid DEX swap would exceed source oracle move limit")

    def _variables2_revenue_percent(self) -> int:
        # Captured from variables2 in load_state, packed at bits 19..25.
        return (self.dex_variables2 >> 19) & ((1 << 7) - 1)

    def _previous_oracle_price(self) -> int:
        packed = self.dex_variables
        # BigMathMinified.fromBigNumber(value, 8, 0xff): coefficient << exponent.
        last_interaction = (packed >> 121) & ((1 << 33) - 1)
        shift = 1 if self.block_timestamp == last_interaction else 41
        return self._packed_price(shift)

    def _packed_price(self, shift: int) -> int:
        value = (self.dex_variables >> shift) & ((1 << 40) - 1)
        return (value >> 8) << (value & 0xff)


class FluidDexAdapter:
    """Snapshot adapter for resolver-normalized Fluid DEX T1 state only."""

    family = "fluid_dex"

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        resolver = self._resolver(pool, block)
        dex = norm_address(pool.pool)
        encoded_pool = abi_encode(["address"], [dex]).hex()
        return [
            CallSpec(resolver, SEL_POOL_RESERVES_ADJUSTED + encoded_pool, TAG_RESERVES),
            CallSpec(dex, SEL_CONSTANTS_VIEW_2, TAG_CONSTANTS_2),
            CallSpec(dex, SEL_READ_STORAGE + abi_encode(["bytes32"], [bytes(32)]).hex(), TAG_VARIABLES),
            CallSpec(dex, SEL_READ_STORAGE + abi_encode(["bytes32"], [(1).to_bytes(32, "big")]).hex(), TAG_VARIABLES_2),
        ]

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> FluidDexState:
        if len(pool.tokens) != 2:
            raise Unsupported(f"{pool.pool_id} must have exactly two tokens")
        liquidity = pool.config.get("liquidity")
        if not isinstance(liquidity, str):
            raise Unsupported(f"{pool.pool_id} has no Fluid Liquidity owner identity")
        _ = norm_address(liquidity)
        resolver = self._resolver(pool, snapshot.block)
        dex = norm_address(pool.pool)
        reserves_spec = CallSpec(resolver, SEL_POOL_RESERVES_ADJUSTED + abi_encode(["address"], [dex]).hex(), TAG_RESERVES)
        constants_spec = CallSpec(dex, SEL_CONSTANTS_VIEW_2, TAG_CONSTANTS_2)
        variables_spec = CallSpec(dex, SEL_READ_STORAGE + abi_encode(["bytes32"], [bytes(32)]).hex(), TAG_VARIABLES)
        variables2_spec = CallSpec(dex, SEL_READ_STORAGE + abi_encode(["bytes32"], [(1).to_bytes(32, "big")]).hex(), TAG_VARIABLES_2)
        reserves = self._decode(snapshot, reserves_spec, POOL_RESERVES_TYPES, "pool reserves", pool)[0]
        constants = self._decode(snapshot, constants_spec, ["uint256", "uint256", "uint256", "uint256"], "constantsView2", pool)
        _variables = self._decode(snapshot, variables_spec, ["uint256"], "dex variables", pool)[0]
        variables2 = self._decode(snapshot, variables2_spec, ["uint256"], "dex variables2", pool)[0]
        _pool, token0, token1, fee, center_price, collateral, debt, limits = reserves
        token0, token1 = protocol_token(token0), protocol_token(token1)
        if (token0, token1) != (pool.tokens[0].address, pool.tokens[1].address):
            raise Unsupported(f"{pool.pool_id} resolver token identity disagrees with discovery record")
        if fee != ((variables2 >> 2) & ((1 << 17) - 1)):
            raise Unsupported(f"{pool.pool_id} resolver fee disagrees with dex variables2")
        return FluidDexState(
            record=pool,
            token0=pool.tokens[0], token1=pool.tokens[1],
            token0_numerator_precision=constants[0], token0_denominator_precision=constants[1],
            token1_numerator_precision=constants[2], token1_denominator_precision=constants[3],
            fee=fee, center_price=center_price,
            dex_variables=_variables, dex_variables2=variables2, block_timestamp=snapshot.block.timestamp,
            collateral=tuple(collateral), debt=tuple(debt),
            withdrawable=(limits[0][0], limits[1][0]), borrowable=(limits[2][0], limits[3][0]),
            smart_collateral_enabled=bool(variables2 & 1), smart_debt_enabled=bool(variables2 & 2),
            paused=bool(variables2 >> 255),
        )

    @staticmethod
    def _resolver(pool: PoolRecord, block: BlockRef) -> str:
        resolver = resolver_for_block(block.number)
        observations = pool.config.get("resolver_observations", {})
        observed = observations.get(block.hash) if isinstance(observations, dict) else None
        if observed is not None and (not isinstance(observed, str) or norm_address(observed) != resolver):
            raise Unsupported(f"{pool.pool_id} recorded resolver observation disagrees with historical block bound")
        bounds = pool.config.get("resolver_bounds")
        legacy_bounds = {"old_through": NEW_RESOLVER_CREATED_BLOCK - 1,
                         "new_from": NEW_RESOLVER_CREATED_BLOCK}
        historical_bounds = {
            "earliest_from": EARLIEST_RESOLVER_CREATED_BLOCK,
            "earliest_through": OLD_RESOLVER_CREATED_BLOCK - 1,
            "old_from": OLD_RESOLVER_CREATED_BLOCK,
            "old_through": NEW_RESOLVER_CREATED_BLOCK - 1,
            "new_from": NEW_RESOLVER_CREATED_BLOCK,
        }
        if bounds is not None and bounds not in (legacy_bounds, historical_bounds):
            raise Unsupported(f"{pool.pool_id} has unsupported Fluid resolver block bounds")
        return resolver

    @staticmethod
    def _decode(snapshot: Snapshot, spec: CallSpec, types: list[str], what: str, pool: PoolRecord):
        if not snapshot.has(spec):
            raise Unsupported(f"missing Fluid DEX {what} for {pool.pool_id} at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed Fluid DEX {what} for {pool.pool_id} at {snapshot.block.number}")
        try:
            return abi_decode(types, bytes.fromhex(result.raw.removeprefix("0x")))
        except (TypeError, ValueError, DecodingError) as exc:
            raise Unsupported(f"malformed Fluid DEX {what} for {pool.pool_id}: {exc}") from None
