"""Bounded exact-input model for Curve's deployed DAI/USDC/USDT 3pool.

This is deliberately separate from StableSwap-NG.  Its fixed rates and
per-step invariant divisions follow ``StableSwap3Pool.vy``.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address
from .curve_ng import CurveNGAdapter

POOL = "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7"
REGISTRY = "0x90e00ace148ca3b23ac1bc8c240c2a7dd9c2d7f5"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ZERO = "0x0000000000000000000000000000000000000000"
RATES = (10**18, 10**30, 10**30)
PRECISION = 10**18
FEE_DENOMINATOR = 10**10
UINT256_MAX = (1 << 256) - 1
GAS_ESTIMATE = 180_000


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_A = _selector("A()")
SEL_FEE = _selector("fee()")
SEL_ADMIN_FEE = _selector("admin_fee()")
SEL_BALANCE = _selector("balances(uint256)")
TAGS = ("curve-legacy-3pool:a", "curve-legacy-3pool:fee", "curve-legacy-3pool:admin-fee",
        "curve-legacy-3pool:balance:0", "curve-legacy-3pool:balance:1", "curve-legacy-3pool:balance:2")


def _uint(value: int, what: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT256_MAX:
        raise Unsupported(f"Curve legacy 3pool {what} is outside uint256")
    return value


def _div(a: int, b: int, what: str) -> int:
    _uint(a, what)
    _uint(b, what)
    if b == 0:
        raise Unsupported(f"Curve legacy 3pool division by zero computing {what}")
    return a // b


def get_D(xp: tuple[int, int, int], amp: int) -> int:
    """Literal source-order ``StableSwap3Pool.get_D`` arithmetic."""
    if any(_uint(value, "xp") == 0 for value in xp):
        raise Unsupported("Curve legacy 3pool invariant is undefined with an empty balance")
    amp = _uint(amp, "A")
    total = sum(xp)
    _uint(total, "xp sum")
    if total == 0:
        return 0
    d = total
    ann = amp * 3
    _uint(ann, "Ann")
    if ann <= 1:
        raise Unsupported("Curve legacy 3pool A is too small")
    for _ in range(255):
        d_p = d
        for value in xp:
            d_p = _div(d_p * d, value * 3, "D_P")
        previous = d
        d = _div((ann * total + d_p * 3) * d, (ann - 1) * d + 4 * d_p, "D")
        if abs(d - previous) <= 1:
            return d
    raise Unsupported("Curve legacy 3pool get_D did not converge within 255 iterations")


def get_y(i: int, j: int, x: int, xp: tuple[int, int, int], amp: int) -> int:
    if not (0 <= i < 3 and 0 <= j < 3) or i == j:
        raise Unsupported("Curve legacy 3pool coin indexes are invalid")
    d = get_D(xp, amp)
    ann = amp * 3
    c, total = d, 0
    for index in range(3):
        if index == j:
            continue
        value = x if index == i else xp[index]
        _uint(value, "get_y xp")
        total += value
        c = _div(c * d, value * 3, "get_y c")
    c = _div(c * d, ann * 3, "get_y c")
    b = total + _div(d, ann, "get_y b")
    y = d
    for _ in range(255):
        previous = y
        denominator = 2 * y + b - d
        if denominator <= 0:
            raise Unsupported("Curve legacy 3pool invalid get_y denominator")
        y = _div(y * y + c, denominator, "get_y iteration")
        if abs(y - previous) <= 1:
            return y
    raise Unsupported("Curve legacy 3pool get_y did not converge within 255 iterations")


@dataclass(frozen=True)
class CurveLegacy3PoolState:
    record: PoolRecord
    balances: tuple[int, int, int]
    amp: int
    fee: int
    admin_fee: int
    gas: int = GAS_ESTIMATE

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._indexes(token_in, token_out)
        return self.gas

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        i, j = self._indexes(token_in, token_out)
        _uint(amount_in, "amount_in")
        if amount_in == 0:
            return 0
        xp = tuple(_div(rate * balance, PRECISION, "xp") for rate, balance in zip(RATES, self.balances, strict=True))
        y = get_y(i, j, xp[i] + _div(amount_in * RATES[i], PRECISION, "input xp"), xp, self.amp)
        dy = _div((xp[j] - y - 1) * PRECISION, RATES[j], "output")
        return dy - _div(self.fee * dy, FEE_DENOMINATOR, "fee")

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, CurveLegacy3PoolState]:
        i, j = self._indexes(token_in, token_out)
        _uint(amount_in, "amount_in")
        if amount_in == 0:
            return 0, self
        xp = tuple(_div(rate * balance, PRECISION, "xp") for rate, balance in zip(RATES, self.balances, strict=True))
        x = xp[i] + _div(amount_in * RATES[i], PRECISION, "input xp")
        y = get_y(i, j, x, xp, self.amp)
        if xp[j] <= y:
            raise Unsupported("Curve legacy 3pool exact input rounds to zero output")
        dy_xp = xp[j] - y - 1
        fee_xp = _div(dy_xp * self.fee, FEE_DENOMINATOR, "fee")
        amount_out = _div((dy_xp - fee_xp) * PRECISION, RATES[j], "output")
        if amount_out == 0:
            raise Unsupported("Curve legacy 3pool exact input rounds to zero output")
        admin = _div(_div(fee_xp * self.admin_fee, FEE_DENOMINATOR, "admin fee") * PRECISION,
                     RATES[j], "admin fee")
        updated = list(self.balances)
        updated[i] = _uint(updated[i] + amount_in, "post-swap input balance")
        if updated[j] < amount_out + admin:
            raise Unsupported("Curve legacy 3pool post-swap balance underflow")
        updated[j] -= amount_out + admin
        return amount_out, CurveLegacy3PoolState(self.record, tuple(updated), self.amp, self.fee, self.admin_fee, self.gas)

    def _indexes(self, token_in: Address, token_out: Address) -> tuple[int, int]:
        addresses = tuple(token.address for token in self.record.tokens)
        try:
            i, j = addresses.index(norm_address(token_in)), addresses.index(norm_address(token_out))
        except ValueError:
            raise Unsupported(f"Curve legacy 3pool direction {token_in}->{token_out} is not in {self.record.pool_id}") from None
        if i == j:
            raise Unsupported("Curve legacy 3pool cannot swap a token for itself")
        return i, j


class CurveLegacy3PoolAdapter:
    family = "curve"

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._identity(pool, block)
        return [CallSpec(pool.pool, SEL_A, TAGS[0]), CallSpec(pool.pool, SEL_FEE, TAGS[1]),
                CallSpec(pool.pool, SEL_ADMIN_FEE, TAGS[2]),
                *(CallSpec(pool.pool, SEL_BALANCE + abi_encode(["uint256"], [i]).hex(), TAGS[3 + i]) for i in range(3))]

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> CurveLegacy3PoolState:
        self._identity(pool, snapshot.block)
        specs = self.read_requests(pool, snapshot.block)
        amp, fee, admin = (self._one(snapshot, spec, pool, tag) for spec, tag in zip(specs[:3], ("A", "fee", "admin_fee"), strict=True))
        balances = tuple(self._one(snapshot, spec, pool, spec.tag) for spec in specs[3:])
        if any(balance == 0 for balance in balances):
            raise Unsupported("Curve legacy 3pool has an empty balance")
        if fee > FEE_DENOMINATOR or admin > FEE_DENOMINATOR:
            raise Unsupported("Curve legacy 3pool fee is outside the configured denominator")
        return CurveLegacy3PoolState(pool, balances, amp, fee, admin)

    @staticmethod
    def _identity(pool: PoolRecord, block: BlockRef) -> None:
        if pool.family != "curve" or pool.chain != 1 or block.chain != 1 or pool.pool != POOL:
            raise Unsupported("Curve legacy adapter supports only Ethereum's canonical 3pool")
        if tuple(token.address for token in pool.tokens) != (DAI, USDC, USDT):
            raise Unsupported("Curve legacy 3pool token order is not DAI/USDC/USDT")
        observation = pool.config.get("registry_observations", {}).get(block.hash.lower())
        if not isinstance(observation, dict) or observation.get("coins") != [DAI, USDC, USDT]:
            raise Unsupported("Curve legacy 3pool has no matching registry observation at this exact block hash")
        if observation.get("base_pool") != ZERO or REGISTRY not in observation.get("base_registries", []):
            raise Unsupported("Curve legacy 3pool registry identity is not the canonical plain pool")
        semantics = pool.config.get("transfer_semantics")
        if not isinstance(semantics, dict) or any(semantics.get(token.address) != "standard" for token in pool.tokens):
            raise Unsupported("Curve legacy 3pool transfer semantics are unqualified")

    @staticmethod
    def _one(snapshot: Snapshot, spec: CallSpec, pool: PoolRecord, what: str) -> int:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for {pool.pool_id} at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for {pool.pool_id} at {snapshot.block.number}")
        try:
            return abi_decode(["uint256"], bytes.fromhex(result.raw.removeprefix("0x")))[0]
        except (TypeError, ValueError, DecodingError) as exc:
            raise Unsupported(f"malformed {what} for {pool.pool_id}: {exc}") from None


class CurveAdapter:
    """Dispatch Curve records by the pinned legacy pool identity, else StableSwap-NG."""

    family = "curve"

    @staticmethod
    def _adapter(pool: PoolRecord):
        return CurveLegacy3PoolAdapter() if norm_address(pool.pool) == POOL else CurveNGAdapter()

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        return self._adapter(pool).read_requests(pool, block)

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        return self._adapter(pool).dependent_requests(pool, block, snapshot)

    def load_state(self, pool: PoolRecord, snapshot: Snapshot):
        return self._adapter(pool).load_state(pool, snapshot)
