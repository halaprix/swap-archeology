"""Exact, offline Uniswap V2 pair state and snapshot adapter.

The adapter models only canonical Ethereum Uniswap V2 pairs from
``FACTORY``.  It fetches both stored reserves and the two ERC-20 balances of
the pair: V2's ``swap`` invariant uses balances, while Router02's library
quote uses stored reserves.  A mismatch is therefore deliberately unsupported
until an adapter models its cause (donation, rebase, or non-standard transfer).
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
ROUTER02 = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
FEE_NUMERATOR = 997
FEE_DENOMINATOR = 1000
UINT112_MAX = (1 << 112) - 1
UINT256_MAX = (1 << 256) - 1
STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
GAS_ESTIMATE = 100_000


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_GET_RESERVES = _selector("getReserves()")
SEL_TOKEN0 = _selector("token0()")
SEL_TOKEN1 = _selector("token1()")
SEL_BALANCE_OF = _selector("balanceOf(address)")
assert (SEL_GET_RESERVES, SEL_TOKEN0, SEL_TOKEN1, SEL_BALANCE_OF) == (
    "0x0902f1ac", "0x0dfe1681", "0xd21220a7", "0x70a08231"
), "Uniswap V2 selector table drifted"

TAG_RESERVES = "univ2:getReserves"
TAG_TOKEN0 = "univ2:token0"
TAG_TOKEN1 = "univ2:token1"
TAG_BALANCE0 = "univ2:balance0"
TAG_BALANCE1 = "univ2:balance1"


def _checked_add(a: int, b: int, what: str) -> int:
    result = a + b
    if result > UINT256_MAX:
        raise Unsupported(f"uint256 overflow computing {what}")
    return result


def _checked_mul(a: int, b: int, what: str) -> int:
    result = a * b
    if result > UINT256_MAX:
        raise Unsupported(f"uint256 overflow computing {what}")
    return result


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    """Canonical ``UniswapV2Library.getAmountOut`` with uint256 checks."""
    if type(amount_in) is not int or type(reserve_in) is not int or type(reserve_out) is not int:
        raise Unsupported("Uniswap V2 amounts and reserves must be integers")
    if amount_in <= 0:
        raise Unsupported("Uniswap V2 requires a positive exact input")
    if not 0 <= amount_in <= UINT256_MAX:
        raise Unsupported("amount_in is outside uint256")
    if not 0 < reserve_in <= UINT112_MAX or not 0 < reserve_out <= UINT112_MAX:
        raise Unsupported("Uniswap V2 has insufficient liquidity or invalid uint112 reserves")
    amount_in_with_fee = _checked_mul(amount_in, FEE_NUMERATOR, "amountInWithFee")
    numerator = _checked_mul(amount_in_with_fee, reserve_out, "amountOut numerator")
    denominator = _checked_add(
        _checked_mul(reserve_in, FEE_DENOMINATOR, "amountOut denominator"),
        amount_in_with_fee,
        "amountOut denominator",
    )
    return numerator // denominator


@dataclass(frozen=True)
class UniV2State:
    """Immutable canonical V2 reserves at one block."""

    record: PoolRecord
    reserve0: int
    reserve1: int
    token0: Token
    token1: Token
    gas: int = GAS_ESTIMATE

    def __post_init__(self) -> None:
        if type(self.reserve0) is not int or type(self.reserve1) is not int:
            raise Unsupported("Uniswap V2 reserves must be integers")
        if not 0 <= self.reserve0 <= UINT112_MAX or not 0 <= self.reserve1 <= UINT112_MAX:
            raise Unsupported("Uniswap V2 reserve is outside uint112")

    def tokens(self) -> tuple[Token, ...]:
        return (self.token0, self.token1)

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._reserves_for(token_in, token_out)
        return self.gas

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if type(amount_in) is not int:
            raise Unsupported("Uniswap V2 amount_in must be an integer")
        if amount_in == 0:
            self._reserves_for(token_in, token_out)
            return 0
        return self.swap(token_in, token_out, amount_in)[0]

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, UniV2State]:
        if type(amount_in) is not int:
            raise Unsupported("Uniswap V2 amount_in must be an integer")
        if amount_in == 0:
            self._reserves_for(token_in, token_out)
            return 0, self
        reserve_in, reserve_out, zero_for_one = self._reserves_for(token_in, token_out)
        amount_out = get_amount_out(amount_in, reserve_in, reserve_out)
        if amount_out <= 0:
            raise Unsupported("Uniswap V2 exact input rounds to zero output")
        new_reserve_in = _checked_add(reserve_in, amount_in, "post-swap input reserve")
        if new_reserve_in > UINT112_MAX:
            raise Unsupported("Uniswap V2 post-swap input reserve overflows uint112")
        new_reserve_out = reserve_out - amount_out
        if new_reserve_out < 0:  # defensive; canonical formula cannot do this
            raise Unsupported("Uniswap V2 output exceeds reserve")
        if zero_for_one:
            return amount_out, UniV2State(
                self.record, new_reserve_in, new_reserve_out, self.token0, self.token1, self.gas
            )
        return amount_out, UniV2State(
            self.record, new_reserve_out, new_reserve_in, self.token0, self.token1, self.gas
        )

    def _reserves_for(self, token_in: Address, token_out: Address) -> tuple[int, int, bool]:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if token_in == self.token0.address and token_out == self.token1.address:
            return self.reserve0, self.reserve1, True
        if token_in == self.token1.address and token_out == self.token0.address:
            return self.reserve1, self.reserve0, False
        raise Unsupported(
            f"pair {token_in}->{token_out} is not this pool's pair "
            f"({self.token0.address}/{self.token1.address}) for {self.record.pool_id}"
        )


class UniswapV2Adapter:
    """``SourceAdapter`` for the canonical mainnet Uniswap V2 factory."""

    family = "uniswap_v2"

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        pair = norm_address(pool.pool)
        token0, token1 = self._record_tokens(pool)
        balance_data = lambda token: SEL_BALANCE_OF + abi_encode(["address"], [pair]).hex()
        return [
            CallSpec(pair, SEL_GET_RESERVES, TAG_RESERVES),
            CallSpec(pair, SEL_TOKEN0, TAG_TOKEN0),
            CallSpec(pair, SEL_TOKEN1, TAG_TOKEN1),
            CallSpec(token0.address, balance_data(token0), TAG_BALANCE0),
            CallSpec(token1.address, balance_data(token1), TAG_BALANCE1),
        ]

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> UniV2State:
        if norm_address(pool.deployment) != FACTORY:
            raise Unsupported(f"{pool.pool_id} is not from canonical Uniswap V2 factory {FACTORY}")
        token0, token1 = self._record_tokens(pool)
        self._require_standard_transfer_semantics(pool, token0, token1)
        pair = norm_address(pool.pool)
        reserves = self._decode(snapshot, CallSpec(pair, SEL_GET_RESERVES, TAG_RESERVES),
                                ["uint112", "uint112", "uint32"], "getReserves()", pool)
        reported0 = norm_address(self._decode(snapshot, CallSpec(pair, SEL_TOKEN0, TAG_TOKEN0),
                                              ["address"], "token0()", pool)[0])
        reported1 = norm_address(self._decode(snapshot, CallSpec(pair, SEL_TOKEN1, TAG_TOKEN1),
                                              ["address"], "token1()", pool)[0])
        if (reported0, reported1) != (token0.address, token1.address):
            raise Unsupported(
                f"pair token identity disagrees with discovery record: "
                f"reported {reported0}/{reported1}, record {token0.address}/{token1.address}"
            )
        balance_data = lambda: SEL_BALANCE_OF + abi_encode(["address"], [pair]).hex()
        balance0 = self._decode(snapshot, CallSpec(token0.address, balance_data(), TAG_BALANCE0),
                                ["uint256"], "token0 balanceOf(pair)", pool)[0]
        balance1 = self._decode(snapshot, CallSpec(token1.address, balance_data(), TAG_BALANCE1),
                                ["uint256"], "token1 balanceOf(pair)", pool)[0]
        if (balance0, balance1) != (reserves[0], reserves[1]):
            raise Unsupported(
                f"reserve/balance mismatch for {pool.pool_id}: reserves={reserves[0]}/{reserves[1]}, "
                f"balances={balance0}/{balance1}; donation, rebase, or non-standard transfer model required"
            )
        if not reserves[0] or not reserves[1]:
            raise Unsupported(f"Uniswap V2 pair {pool.pool_id} has empty reserves")
        return UniV2State(pool, reserves[0], reserves[1], token0, token1)

    @staticmethod
    def _record_tokens(pool: PoolRecord) -> tuple[Token, Token]:
        if len(pool.tokens) != 2:
            raise Unsupported(f"Uniswap V2 pool {pool.pool_id} must have exactly two tokens")
        return pool.tokens[0], pool.tokens[1]

    @staticmethod
    def _decode(snapshot: Snapshot, spec: CallSpec, types: list[str], what: str, pool: PoolRecord):
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for {pool.pool_id} at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for {pool.pool_id} at {snapshot.block.number}")
        try:
            return abi_decode(types, bytes.fromhex(result.raw.removeprefix("0x")))
        except (TypeError, ValueError, DecodingError) as exc:
            raise Unsupported(f"malformed {what} for {pool.pool_id}: {exc}") from None

    @staticmethod
    def _require_standard_transfer_semantics(pool: PoolRecord, token0: Token, token1: Token) -> None:
        if STETH in (token0.address, token1.address):
            raise Unsupported("raw stETH V2 reserves are excluded: rebasing transfer semantics are unsupported")
        semantics = pool.config.get("transfer_semantics")
        if not isinstance(semantics, dict) or any(
            semantics.get(token.address) != "standard" for token in (token0, token1)
        ):
            raise Unsupported(
                "token transfer semantics are unqualified; require config.transfer_semantics "
                "mapping both token addresses to 'standard'"
            )
