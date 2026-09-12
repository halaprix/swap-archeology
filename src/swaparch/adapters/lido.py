"""Exact quote model for the Ethereum wstETH/stETH wrapper singleton.

The model keeps the wrapper's wstETH supply and backing stETH shares so an
unwrap cannot use backing that was never present.  Its returned quote remains
the public getter result; see the adapter documentation for the separate
stETH transfer-rounding settlement boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
WSTETH = "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0"
MAX_UINT256 = 2**256 - 1
# ponytail: conservative model limit; widen only after pin-by-pin proxy proof.
MAX_SUPPORTED_INPUT = 2**128 - 1

SEL_TOTAL_POOLED_ETHER = "0x37cfdaca"
SEL_TOTAL_SHARES = "0xd5002f2e"
SEL_STETH = "0xc1fe3e48"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_SHARES_OF = "0xf5eb42dc"

assert (
    SEL_TOTAL_POOLED_ETHER,
    SEL_TOTAL_SHARES,
    SEL_STETH,
    SEL_TOTAL_SUPPLY,
    SEL_SHARES_OF,
) == (
    "0x37cfdaca",
    "0xd5002f2e",
    "0xc1fe3e48",
    "0x18160ddd",
    "0xf5eb42dc",
), "Lido selector table drifted"

TAG_TOTAL_POOLED_ETHER = "lido:totalPooledEther"
TAG_TOTAL_SHARES = "lido:totalShares"
TAG_STETH = "lido:wstETH.stETH"
TAG_WSTETH_SUPPLY = "lido:wstETH.totalSupply"
TAG_WRAPPER_SHARES = "lido:stETH.sharesOf(wstETH)"


def _uint(value: object, what: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_UINT256:
        raise Unsupported(f"{what} must be a uint256 integer")
    return value


def _checked_add(left: int, right: int, what: str) -> int:
    if left > MAX_UINT256 - right:
        raise Unsupported(f"Lido {what} overflows uint256")
    return left + right


def _checked_mul(left: int, right: int, what: str) -> int:
    if left and right > MAX_UINT256 // left:
        raise Unsupported(f"Lido {what} overflows uint256")
    return left * right


def _supported_input(value: object) -> int:
    value = _uint(value, "exact input")
    if value >= MAX_SUPPORTED_INPUT:
        raise Unsupported("Lido exact input is outside this adapter's <2**128-1 supported domain")
    return value


def _validate_pool(pool: PoolRecord) -> None:
    try:
        identity = (norm_address(pool.deployment), norm_address(pool.pool))
        token_addresses = tuple(token.address for token in pool.tokens)
    except (AttributeError, TypeError, ValueError) as exc:
        raise Unsupported("malformed Lido wrapper record") from exc
    expected_id = f"lido:{WSTETH}:{WSTETH}"
    if (
        pool.chain != 1
        or pool.family != "lido"
        or identity != (WSTETH, WSTETH)
        or pool.pool_id != expected_id
        or token_addresses != (STETH, WSTETH)
    ):
        raise Unsupported("not the qualified Ethereum wstETH wrapper singleton")
    if pool.config.get("model") != "lido_wsteth":
        raise Unsupported(f"{pool.pool_id} is not a lido_wsteth record")


def _match_token(pool: PoolRecord, address: Address, chain: int) -> Token:
    for token in pool.tokens:
        if token.address == address and token.chain == chain:
            return token
    raise Unsupported(f"pool {pool.pool_id} is missing canonical token {address}")


@dataclass(frozen=True)
class LidoWstEthState:
    """One pinned wstETH wrapper state, including finite backing shares."""

    record: PoolRecord
    steth: Token
    wsteth: Token
    total_pooled_ether: int
    total_shares: int
    wsteth_supply: int
    wrapper_steth_shares: int

    def __post_init__(self) -> None:
        _validate_pool(self.record)
        if self.steth.address != STETH or self.wsteth.address != WSTETH:
            raise Unsupported(f"{self.record.pool_id} token identity mismatch")
        if self.steth.decimals != 18 or self.wsteth.decimals != 18:
            raise Unsupported(f"{self.record.pool_id} tokens must have 18 decimals")
        if _uint(self.total_pooled_ether, "total_pooled_ether") == 0:
            raise Unsupported("Lido getTotalPooledEther() is zero")
        if _uint(self.total_shares, "total_shares") == 0:
            raise Unsupported("Lido getTotalShares() is zero")
        _uint(self.wsteth_supply, "wsteth_supply")
        _uint(self.wrapper_steth_shares, "wrapper_steth_shares")

    def tokens(self) -> tuple[Token, ...]:
        return (self.steth, self.wsteth)

    def capacity_ids(self) -> tuple[str, ...]:
        # The common share rate is read-only during wrapping, not consumed liquidity.
        return (f"lido:{WSTETH}:wrapper_backing",)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return None

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, LidoWstEthState]:
        direction = self._direction(token_in, token_out)
        amount_in = _supported_input(amount_in)
        if direction == "wrap":
            return self._wrap(amount_in)
        return self._unwrap(amount_in)

    def _direction(self, token_in: Address, token_out: Address) -> str:
        try:
            token_in, token_out = norm_address(token_in), norm_address(token_out)
        except (AttributeError, TypeError, ValueError) as exc:
            raise Unsupported("malformed Lido token direction") from exc
        if token_in == STETH and token_out == WSTETH:
            return "wrap"
        if token_in == WSTETH and token_out == STETH:
            return "unwrap"
        raise Unsupported(f"pair {token_in}->{token_out} is not Lido stETH/wstETH")

    def _shares_by_pooled_eth(self, amount: int) -> int:
        return _checked_mul(amount, self.total_shares, "getSharesByPooledEth") // self.total_pooled_ether

    def _pooled_eth_by_shares(self, shares: int) -> int:
        return _checked_mul(shares, self.total_pooled_ether, "getPooledEthByShares") // self.total_shares

    def _wrap(self, steth_in: int) -> tuple[int, LidoWstEthState]:
        minted_wsteth = self._shares_by_pooled_eth(steth_in)
        return minted_wsteth, LidoWstEthState(
            record=self.record,
            steth=self.steth,
            wsteth=self.wsteth,
            total_pooled_ether=self.total_pooled_ether,
            total_shares=self.total_shares,
            wsteth_supply=_checked_add(self.wsteth_supply, minted_wsteth, "wstETH supply"),
            wrapper_steth_shares=_checked_add(
                self.wrapper_steth_shares, minted_wsteth, "wrapper backing shares"
            ),
        )

    def _unwrap(self, wsteth_in: int) -> tuple[int, LidoWstEthState]:
        if wsteth_in > self.wsteth_supply:
            raise Unsupported(
                f"{self.record.pool_id} unwrap burns {wsteth_in} wstETH but supply is "
                f"{self.wsteth_supply}"
            )
        quoted_steth = self._pooled_eth_by_shares(wsteth_in)
        transferred_shares = self._shares_by_pooled_eth(quoted_steth)
        if transferred_shares > self.wrapper_steth_shares:
            raise Unsupported(
                f"{self.record.pool_id} unwrap transfers {transferred_shares} backing shares but "
                f"wrapper holds {self.wrapper_steth_shares}"
            )
        return quoted_steth, LidoWstEthState(
            record=self.record,
            steth=self.steth,
            wsteth=self.wsteth,
            total_pooled_ether=self.total_pooled_ether,
            total_shares=self.total_shares,
            wsteth_supply=self.wsteth_supply - wsteth_in,
            wrapper_steth_shares=self.wrapper_steth_shares - transferred_shares,
        )


class LidoAdapter:
    """``SourceAdapter`` for only the Ethereum wstETH wrapper singleton."""

    family = "lido"

    @staticmethod
    def _specs() -> list[CallSpec]:
        wrapper_shares_data = SEL_SHARES_OF + abi_encode(["address"], [WSTETH]).hex()
        return [
            CallSpec(STETH, SEL_TOTAL_POOLED_ETHER, TAG_TOTAL_POOLED_ETHER),
            CallSpec(STETH, SEL_TOTAL_SHARES, TAG_TOTAL_SHARES),
            CallSpec(WSTETH, SEL_STETH, TAG_STETH),
            CallSpec(WSTETH, SEL_TOTAL_SUPPLY, TAG_WSTETH_SUPPLY),
            CallSpec(STETH, wrapper_shares_data, TAG_WRAPPER_SHARES),
        ]

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        _validate_pool(pool)
        return self._specs()

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        _validate_pool(pool)
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> LidoWstEthState:
        _validate_pool(pool)
        specs = self._specs()
        if snapshot.block.chain != 1:
            raise Unsupported("not an Ethereum Lido snapshot")
        wrapper_steth = self._decode_address(snapshot, specs[2], "wstETH.stETH()")
        if wrapper_steth != STETH:
            raise Unsupported("wstETH immutable stETH() identity mismatch")
        return LidoWstEthState(
            record=pool,
            steth=_match_token(pool, STETH, snapshot.block.chain),
            wsteth=_match_token(pool, WSTETH, snapshot.block.chain),
            total_pooled_ether=self._decode_uint(
                snapshot, specs[0], "stETH.getTotalPooledEther()"
            ),
            total_shares=self._decode_uint(snapshot, specs[1], "stETH.getTotalShares()"),
            wsteth_supply=self._decode_uint(snapshot, specs[3], "wstETH.totalSupply()"),
            wrapper_steth_shares=self._decode_uint(
                snapshot, specs[4], "stETH.sharesOf(wstETH)"
            ),
        )

    @staticmethod
    def _raw(snapshot: Snapshot, spec: CallSpec, what: str) -> bytes:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for Lido at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for Lido at {snapshot.block.number}")
        try:
            if not result.raw.startswith("0x"):
                raise ValueError("missing 0x prefix")
            return bytes.fromhex(result.raw[2:])
        except (AttributeError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed hex for {what} at {snapshot.block.number}") from exc

    def _decode_uint(self, snapshot: Snapshot, spec: CallSpec, what: str) -> int:
        try:
            return abi_decode(["uint256"], self._raw(snapshot, spec, what))[0]
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc

    def _decode_address(self, snapshot: Snapshot, spec: CallSpec, what: str) -> Address:
        try:
            return norm_address(abi_decode(["address"], self._raw(snapshot, spec, what))[0])
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc
