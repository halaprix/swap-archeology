"""Bounded exact-input model for Curve StableSwap-NG plain pools.

The port follows the pinned ``CurveStableSwapNG`` source for a standard-token,
plain pool only.  It deliberately does not model oracle, ERC-4626, rebasing,
or metapool asset paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

FACTORY = "0x6a8cbed756804b16e05e741edabd5cb544ae21bf"
ZERO = "0x0000000000000000000000000000000000000000"
STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
PLAIN_IMPLEMENTATIONS = frozenset({
    "0xdcc91f930b42619377c200ba05b7513f2958b202",
    "0x933f4769dcc27fc7345d9d5975ae48ec4d0f829c",
})
UINT256_MAX = (1 << 256) - 1
MAX_COINS = 8
PRECISION = 10**18
A_PRECISION = 100
FEE_DENOMINATOR = 10**10
GAS_ESTIMATE = 180_000


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_N_COINS = _selector("N_COINS()")
SEL_A = _selector("A()")
SEL_A_PRECISE = _selector("A_precise()")
SEL_BALANCES = _selector("get_balances()")
SEL_RATES = _selector("stored_rates()")
SEL_FEE = _selector("fee()")
SEL_OFFPEG = _selector("offpeg_fee_multiplier()")
SEL_ADMIN_FEE = _selector("admin_fee()")
SEL_ASSET_TYPES = _selector("get_pool_asset_types(address)")
SEL_IMPLEMENTATION = _selector("get_implementation_address(address)")

TAGS = (
    "curve-ng:n-coins", "curve-ng:a", "curve-ng:a-precise", "curve-ng:balances",
    "curve-ng:rates", "curve-ng:fee", "curve-ng:offpeg-fee-multiplier", "curve-ng:admin-fee",
    "curve-ng:asset-types", "curve-ng:implementation",
)


def _uint(value: int, what: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT256_MAX:
        raise Unsupported(f"Curve NG {what} is outside uint256")
    return value


def _add(a: int, b: int, what: str) -> int:
    _uint(a, what)
    _uint(b, what)
    return _uint(a + b, what)


def _mul(a: int, b: int, what: str) -> int:
    _uint(a, what)
    _uint(b, what)
    return _uint(a * b, what)


def _sub(a: int, b: int, what: str) -> int:
    _uint(a, what)
    _uint(b, what)
    if b > a:
        raise Unsupported(f"Curve NG underflow computing {what}")
    return a - b


def _div(a: int, b: int, what: str) -> int:
    _uint(a, what)
    _uint(b, what)
    if b == 0:
        raise Unsupported(f"Curve NG division by zero computing {what}")
    return a // b


def get_D(xp: tuple[int, ...], amp: int) -> int:
    """Pinned NG ``get_D`` with checked uint256 intermediates."""
    n = len(xp)
    if not 2 <= n <= MAX_COINS:
        raise Unsupported("Curve NG requires 2..8 coins")
    if any(_uint(x, "xp") == 0 for x in xp):
        raise Unsupported("Curve NG invariant is undefined with an empty balance")
    amp = _uint(amp, "A_precise")
    total = 0
    for x in xp:
        total = _add(total, x, "xp sum")
    if total == 0:
        return 0
    ann = _mul(amp, n, "Ann")
    if ann < A_PRECISION:
        raise Unsupported("Curve NG A_precise is below A_PRECISION")
    d = total
    n_pow_n = n**n
    for _ in range(255):
        d_p = d
        for x in xp:
            d_p = _div(_mul(d_p, d, "D_P"), x, "D_P")
        d_p = _div(d_p, n_pow_n, "D_P")
        previous = d
        numerator = _mul(
            _add(_div(_mul(ann, total, "D numerator"), A_PRECISION, "D numerator"),
                 _mul(d_p, n, "D numerator"), "D numerator"),
            d,
            "D numerator",
        )
        denominator = _add(
            _div(_mul(_sub(ann, A_PRECISION, "D denominator"), d, "D denominator"),
                 A_PRECISION, "D denominator"),
            _mul(n + 1, d_p, "D denominator"),
            "D denominator",
        )
        d = _div(numerator, denominator, "D")
        if abs(d - previous) <= 1:
            return d
    raise Unsupported("Curve NG get_D did not converge within 255 iterations")


def get_y(i: int, j: int, x: int, xp: tuple[int, ...], amp: int, d: int) -> int:
    """Pinned NG ``get_y`` with checked uint256 intermediates."""
    n = len(xp)
    if not (0 <= i < n and 0 <= j < n) or i == j:
        raise Unsupported("Curve NG coin indexes are invalid")
    x, amp, d = _uint(x, "x"), _uint(amp, "A_precise"), _uint(d, "D")
    ann = _mul(amp, n, "get_y Ann")
    if ann == 0:
        raise Unsupported("Curve NG A_precise is zero")
    total, c = 0, d
    for k in range(n):
        current = x if k == i else xp[k]
        if k == j:
            continue
        current = _uint(current, "get_y xp")
        total = _add(total, current, "get_y sum")
        c = _div(_mul(c, d, "get_y c"), _mul(current, n, "get_y c"), "get_y c")
    c = _div(_mul(_mul(c, d, "get_y c"), A_PRECISION, "get_y c"),
             _mul(ann, n, "get_y c"), "get_y c")
    b = _add(total, _div(_mul(d, A_PRECISION, "get_y b"), ann, "get_y b"), "get_y b")
    y = d
    for _ in range(255):
        previous = y
        numerator = _add(_mul(y, y, "get_y iteration"), c, "get_y iteration")
        denominator = _sub(_add(_mul(2, y, "get_y iteration"), b, "get_y iteration"),
                           d, "get_y iteration")
        y = _div(numerator, denominator, "get_y iteration")
        if abs(y - previous) <= 1:
            return y
    raise Unsupported("Curve NG get_y did not converge within 255 iterations")


def dynamic_fee(xpi: int, xpj: int, fee: int, multiplier: int) -> int:
    if multiplier <= FEE_DENOMINATOR:
        return fee
    xps2 = _mul(_add(xpi, xpj, "dynamic fee sum"), _add(xpi, xpj, "dynamic fee sum"), "dynamic fee")
    denominator = _add(
        _div(_mul(_mul(_mul(_sub(multiplier, FEE_DENOMINATOR, "dynamic fee"), 4, "dynamic fee"),
                            xpi, "dynamic fee"), xpj, "dynamic fee"), xps2, "dynamic fee"),
        FEE_DENOMINATOR,
        "dynamic fee",
    )
    return _div(_mul(multiplier, fee, "dynamic fee"), denominator, "dynamic fee")


@dataclass(frozen=True)
class CurveNGState:
    record: PoolRecord
    balances: tuple[int, ...]
    rates: tuple[int, ...]
    amp: int
    fee: int
    offpeg_fee_multiplier: int
    admin_fee: int
    gas: int = GAS_ESTIMATE

    def __post_init__(self) -> None:
        n = len(self.record.tokens)
        if not 2 <= n <= MAX_COINS or len(self.balances) != n or len(self.rates) != n:
            raise Unsupported("Curve NG state has inconsistent coin count")
        for value in (*self.balances, *self.rates, self.amp, self.fee,
                      self.offpeg_fee_multiplier, self.admin_fee):
            _uint(value, "state value")
        if any(rate == 0 for rate in self.rates):
            raise Unsupported("Curve NG stored rate is zero")

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._indexes(token_in, token_out)
        return self.gas

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if type(amount_in) is not int:
            raise Unsupported("Curve NG amount_in must be an integer")
        if amount_in == 0:
            self._indexes(token_in, token_out)
            return 0
        return self.swap(token_in, token_out, amount_in)[0]

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, CurveNGState]:
        i, j = self._indexes(token_in, token_out)
        if type(amount_in) is not int:
            raise Unsupported("Curve NG amount_in must be an integer")
        if amount_in == 0:
            return 0, self
        _uint(amount_in, "amount_in")
        xp = tuple(_div(_mul(rate, balance, "xp"), PRECISION, "xp")
                   for rate, balance in zip(self.rates, self.balances, strict=True))
        d = get_D(xp, self.amp)
        x = _add(xp[i], _div(_mul(amount_in, self.rates[i], "input xp"), PRECISION, "input xp"),
                 "input xp")
        y = get_y(i, j, x, xp, self.amp, d)
        raw_out = _sub(_sub(xp[j], y, "raw output"), 1, "raw output")
        fee_xp = _div(_mul(raw_out, dynamic_fee((xp[i] + x) // 2, (xp[j] + y) // 2,
                                                  self.fee, self.offpeg_fee_multiplier), "fee"),
                      FEE_DENOMINATOR, "fee")
        amount_out = _div(_mul(_sub(raw_out, fee_xp, "output after fee"), PRECISION, "output"),
                          self.rates[j], "output")
        if amount_out == 0:
            raise Unsupported("Curve NG exact input rounds to zero output")
        # Curve records the output fee in xp, then computes the admin claim in xp
        # before converting it to token units: both floors are protocol semantics.
        admin_out = _div(_mul(_div(_mul(fee_xp, self.admin_fee, "admin fee"),
                                      FEE_DENOMINATOR, "admin fee"), PRECISION, "admin fee"),
                         self.rates[j], "admin fee")
        new_balances = list(self.balances)
        new_balances[i] = _add(new_balances[i], amount_in, "post-swap input balance")
        new_balances[j] = _sub(new_balances[j], _add(amount_out, admin_out, "post-swap output balance"),
                               "post-swap output balance")
        return amount_out, CurveNGState(
            self.record, tuple(new_balances), self.rates, self.amp, self.fee,
            self.offpeg_fee_multiplier, self.admin_fee, self.gas,
        )

    def _indexes(self, token_in: Address, token_out: Address) -> tuple[int, int]:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        addresses = tuple(token.address for token in self.record.tokens)
        try:
            i, j = addresses.index(token_in), addresses.index(token_out)
        except ValueError:
            raise Unsupported(f"Curve NG direction {token_in}->{token_out} is not in {self.record.pool_id}") from None
        if i == j:
            raise Unsupported("Curve NG cannot swap a token for itself")
        return i, j


class CurveNGAdapter:
    """``SourceAdapter`` for factory-confirmed, plain StableSwap-NG pools."""

    family = "curve"

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._record_identity(pool, block)
        address = norm_address(pool.pool)
        factory_arg = abi_encode(["address"], [address]).hex()
        return [
            CallSpec(address, selector, tag)
            for selector, tag in zip((SEL_N_COINS, SEL_A, SEL_A_PRECISE, SEL_BALANCES, SEL_RATES,
                                      SEL_FEE, SEL_OFFPEG, SEL_ADMIN_FEE), TAGS[:8], strict=True)
        ] + [
            CallSpec(FACTORY, SEL_ASSET_TYPES + factory_arg, TAGS[8]),
            CallSpec(FACTORY, SEL_IMPLEMENTATION + factory_arg, TAGS[9]),
        ]

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> CurveNGState:
        self._record_identity(pool, snapshot.block)
        address = norm_address(pool.pool)
        factory_arg = abi_encode(["address"], [address]).hex()
        n = self._one(snapshot, CallSpec(address, SEL_N_COINS, TAGS[0]), "uint256", pool, "N_COINS")
        a = self._one(snapshot, CallSpec(address, SEL_A, TAGS[1]), "uint256", pool, "A")
        amp = self._one(snapshot, CallSpec(address, SEL_A_PRECISE, TAGS[2]), "uint256", pool, "A_precise")
        balances = self._array(snapshot, CallSpec(address, SEL_BALANCES, TAGS[3]), "uint256[]", pool, "get_balances")
        rates = self._array(snapshot, CallSpec(address, SEL_RATES, TAGS[4]), "uint256[]", pool, "stored_rates")
        fee = self._one(snapshot, CallSpec(address, SEL_FEE, TAGS[5]), "uint256", pool, "fee")
        offpeg = self._one(snapshot, CallSpec(address, SEL_OFFPEG, TAGS[6]), "uint256", pool, "offpeg_fee_multiplier")
        admin = self._one(snapshot, CallSpec(address, SEL_ADMIN_FEE, TAGS[7]), "uint256", pool, "admin_fee")
        asset_types = self._array(snapshot, CallSpec(FACTORY, SEL_ASSET_TYPES + factory_arg, TAGS[8]),
                                  "uint8[]", pool, "factory asset types")
        implementation = self._one(snapshot, CallSpec(FACTORY, SEL_IMPLEMENTATION + factory_arg, TAGS[9]),
                                   "address", pool, "factory implementation")
        if not 2 <= n <= MAX_COINS or n != len(pool.tokens) or n != len(balances) or n != len(rates):
            raise Unsupported(f"Curve NG coin-count disagreement for {pool.pool_id}")
        if len(asset_types) != n or any(asset_type != 0 for asset_type in asset_types):
            raise Unsupported("Curve NG supports only factory asset_types 0; oracle/ERC4626 paths are unqualified")
        if norm_address(implementation) not in PLAIN_IMPLEMENTATIONS:
            raise Unsupported("Curve NG factory implementation is not a pinned plain-pool implementation")
        if amp != a * A_PRECISION:
            raise Unsupported("Curve NG A() view loses A_precise rounding; cannot qualify this block")
        if any(balance == 0 for balance in balances):
            raise Unsupported("Curve NG pool has an empty balance")
        if admin > FEE_DENOMINATOR or fee > FEE_DENOMINATOR:
            raise Unsupported("Curve NG fee is outside the configured denominator")
        return CurveNGState(pool, balances, rates, amp, fee, offpeg, admin)

    @staticmethod
    def _record_identity(pool: PoolRecord, block: BlockRef) -> None:
        if pool.chain != 1 or block.chain != 1 or any(t.chain != 1 for t in pool.tokens):
            raise Unsupported("Curve NG requires Ethereum pool, tokens, and snapshot")
        if pool.family != "curve":
            raise Unsupported(f"{pool.pool_id} is not a Curve record")
        observation = pool.config.get("registry_observations", {}).get(block.hash.lower())
        if not isinstance(observation, dict):
            raise Unsupported("Curve NG has no registry observation at this exact block hash")
        if observation.get("base_pool") != ZERO or FACTORY not in observation.get("base_registries", []):
            raise Unsupported("Curve pool is not a plain StableSwap-NG factory pool at this block")
        coins = observation.get("coins")
        decimals = observation.get("decimals")
        if coins != [token.address for token in pool.tokens] or decimals != [token.decimals for token in pool.tokens]:
            raise Unsupported("Curve NG registry coin identity disagrees with pool record")
        if any(token.address == STETH for token in pool.tokens):
            raise Unsupported("raw stETH Curve NG pools are excluded: rebasing transfer semantics are unsupported")
        semantics = pool.config.get("transfer_semantics")
        if not isinstance(semantics, dict) or any(semantics.get(token.address) != "standard" for token in pool.tokens):
            raise Unsupported("Curve NG transfer semantics are unqualified; require every token marked standard")

    @staticmethod
    def _one(snapshot: Snapshot, spec: CallSpec, abi: str, pool: PoolRecord, what: str):
        values = CurveNGAdapter._decode(snapshot, spec, [abi], pool, what)
        return values[0]

    @staticmethod
    def _array(snapshot: Snapshot, spec: CallSpec, abi: str, pool: PoolRecord, what: str) -> tuple[int, ...]:
        values = CurveNGAdapter._one(snapshot, spec, abi, pool, what)
        if not isinstance(values, (tuple, list)):
            raise Unsupported(f"malformed {what} for {pool.pool_id}")
        return tuple(values)

    @staticmethod
    def _decode(snapshot: Snapshot, spec: CallSpec, types: list[str], pool: PoolRecord, what: str):
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for {pool.pool_id} at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for {pool.pool_id} at {snapshot.block.number}")
        try:
            return abi_decode(types, bytes.fromhex(result.raw.removeprefix("0x")))
        except (TypeError, ValueError, DecodingError) as exc:
            raise Unsupported(f"malformed {what} for {pool.pool_id}: {exc}") from None
