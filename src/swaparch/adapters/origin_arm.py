"""Bounded historical model of Origin's Lido stETH/WETH ARM.

Only the five pin hashes backed by the curated inventory are admitted.  The
proxy changed ABI during that window, so the reserve getter is selected by the
exact block hash rather than guessed from a block number or the current ABI.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

ARM = "0x85b78aca6deae198fbf201c82daf6ca21942acc6"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
PRICE_SCALE = 10**36
MAX_UINT256 = 2**256 - 1

SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"
SEL_TRADERATE0 = "0x45059a6b"
SEL_TRADERATE1 = "0xcf1de5d8"
SEL_PAUSED = "0x5c975abb"
SEL_WITHDRAWS_QUEUED = "0x6ec68625"
SEL_WITHDRAWS_CLAIMED = "0x35ce81c4"
SEL_GET_RESERVES = "0x0902f1ac"
SEL_IMPLEMENTATION = "0x5c60da1b"
SEL_BALANCE_OF = "0x70a08231"
SEL_SHARES_OF = "0xf5eb42dc"
SEL_TOTAL_POOLED_ETHER = "0x37cfdaca"
SEL_TOTAL_SHARES = "0xd5002f2e"

assert (
    SEL_TOKEN0,
    SEL_TOKEN1,
    SEL_TRADERATE0,
    SEL_TRADERATE1,
    SEL_PAUSED,
    SEL_WITHDRAWS_QUEUED,
    SEL_WITHDRAWS_CLAIMED,
    SEL_GET_RESERVES,
    SEL_IMPLEMENTATION,
    SEL_BALANCE_OF,
    SEL_SHARES_OF,
    SEL_TOTAL_POOLED_ETHER,
    SEL_TOTAL_SHARES,
) == (
    "0x0dfe1681",
    "0xd21220a7",
    "0x45059a6b",
    "0xcf1de5d8",
    "0x5c975abb",
    "0x6ec68625",
    "0x35ce81c4",
    "0x0902f1ac",
    "0x5c60da1b",
    "0x70a08231",
    "0xf5eb42dc",
    "0x37cfdaca",
    "0xd5002f2e",
), "Origin ARM selector table drifted"

TAG_TOKEN0 = "origin_arm:token0"
TAG_TOKEN1 = "origin_arm:token1"
TAG_TRADERATE0 = "origin_arm:traderate0"
TAG_TRADERATE1 = "origin_arm:traderate1"
TAG_PAUSED = "origin_arm:paused"
TAG_WITHDRAWS_QUEUED = "origin_arm:withdrawsQueued"
TAG_WITHDRAWS_CLAIMED = "origin_arm:withdrawsClaimed"
TAG_WETH_BALANCE = "origin_arm:weth.balanceOf(arm)"
TAG_STETH_BALANCE = "origin_arm:steth.balanceOf(arm)"
TAG_STETH_SHARES = "origin_arm:steth.sharesOf(arm)"
TAG_TOTAL_POOLED_ETHER = "origin_arm:steth.totalPooledEther"
TAG_TOTAL_SHARES = "origin_arm:steth.totalShares"
TAG_RESERVES = "origin_arm:getReserves"
TAG_IMPLEMENTATION = "origin_arm:implementation"

def _uint(value: object, what: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_UINT256:
        raise Unsupported(f"{what} must be a uint256 integer")
    return value


def _checked_add(left: int, right: int, what: str) -> int:
    if left > MAX_UINT256 - right:
        raise Unsupported(f"Origin Lido ARM {what} overflows uint256")
    return left + right


def _checked_mul(left: int, right: int, what: str) -> int:
    if left and right > MAX_UINT256 // left:
        raise Unsupported(f"Origin Lido ARM {what} overflows uint256")
    return left * right


def _historical_observation(pool: PoolRecord, block: BlockRef) -> tuple[bool, bool, Address]:
    """Return the record's source-backed ABI and proxy implementation at one hash."""
    configured = pool.config.get("historical_observations")
    observation = configured.get(block.hash.lower()) if isinstance(configured, dict) else None
    if not isinstance(observation, dict):
        raise Unsupported(
            "Origin Lido ARM has no qualified historical ABI observation at this exact block hash"
        )
    number = observation.get("number")
    use_reserves = observation.get("get_reserves")
    paused_getter = observation.get("paused_getter")
    if (
        block.chain != 1
        or type(number) is not int
        or number != block.number
        or type(use_reserves) is not bool
        or type(paused_getter) is not bool
    ):
        raise Unsupported(
            "Origin Lido ARM has no qualified historical ABI observation at this exact block hash"
        )
    try:
        implementation = norm_address(observation["implementation"])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise Unsupported("Origin Lido ARM historical implementation mapping is malformed") from exc
    return use_reserves, paused_getter, implementation


def _validate_pool(pool: PoolRecord) -> None:
    try:
        identity = (norm_address(pool.deployment), norm_address(pool.pool))
        tokens = tuple(token.address for token in pool.tokens)
    except (AttributeError, TypeError, ValueError) as exc:
        raise Unsupported("malformed Origin Lido ARM record") from exc
    expected = f"origin_arm:{ARM}:{ARM}"
    if (
        pool.chain != 1
        or pool.family != "origin_arm"
        or pool.pool_id != expected
        or identity != (ARM, ARM)
        or tokens != (WETH, STETH)
        or pool.config.get("kind") != "arm"
        or pool.config.get("generation") != "traderate (old) ABI"
    ):
        raise Unsupported("not the qualified Ethereum Origin Lido ARM singleton")
    if any(token.chain != 1 or token.decimals != 18 for token in pool.tokens):
        raise Unsupported("Origin Lido ARM requires canonical 18-decimal Ethereum tokens")


def _match_token(pool: PoolRecord, address: Address) -> Token:
    for token in pool.tokens:
        if token.address == address:
            return token
    raise Unsupported(f"pool {pool.pool_id} is missing canonical token {address}")


@dataclass(frozen=True)
class OriginLidoArmState:
    """Pinned source quote state; stETH shares keep ARM inventory exact."""

    record: PoolRecord
    weth: Token
    steth: Token
    traderate0: int
    traderate1: int
    weth_balance: int
    withdraws_queued: int
    withdraws_claimed: int
    steth_shares: int
    total_pooled_ether: int
    total_shares: int
    paused: bool = False

    def __post_init__(self) -> None:
        _validate_pool(self.record)
        if self.weth.address != WETH or self.steth.address != STETH:
            raise Unsupported(f"{self.record.pool_id} token identity mismatch")
        if self.paused is not False:
            raise Unsupported(f"{self.record.pool_id} is paused")
        for value, name in (
            (self.traderate0, "traderate0"),
            (self.traderate1, "traderate1"),
            (self.weth_balance, "weth_balance"),
            (self.withdraws_queued, "withdraws_queued"),
            (self.withdraws_claimed, "withdraws_claimed"),
            (self.steth_shares, "steth_shares"),
            (self.total_pooled_ether, "total_pooled_ether"),
            (self.total_shares, "total_shares"),
        ):
            _uint(value, name)
        if self.traderate0 == 0 or self.traderate1 == 0:
            raise Unsupported(f"{self.record.pool_id} traderates must be positive")
        if self.total_pooled_ether == 0 or self.total_shares == 0:
            raise Unsupported(f"{self.record.pool_id} Lido totals must be positive")
        if self.withdraws_claimed > self.withdraws_queued:
            raise Unsupported(f"{self.record.pool_id} withdrawsClaimed exceeds withdrawsQueued")

    def tokens(self) -> tuple[Token, ...]:
        return (self.weth, self.steth)

    def capacity_ids(self) -> tuple[str, ...]:
        return (f"origin_arm:{ARM}:weth_reserve", f"origin_arm:{ARM}:steth_shares")

    @property
    def outstanding_withdrawals(self) -> int:
        return self.withdraws_queued - self.withdraws_claimed

    @property
    def reserve0(self) -> int:
        return max(0, self.weth_balance - self.outstanding_withdrawals)

    @property
    def reserve1(self) -> int:
        return self._pooled_eth_by_shares(self.steth_shares)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return None

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, OriginLidoArmState]:
        direction = self._direction(token_in, token_out)
        amount_in = _uint(amount_in, "exact input")
        rate = self.traderate0 if direction == "weth_to_steth" else self.traderate1
        amount_out = _checked_mul(amount_in, rate, "exact-input quote") // PRICE_SCALE
        if direction == "weth_to_steth":
            if amount_out > self.reserve1:
                raise Unsupported(
                    f"{self.record.pool_id} WETH->stETH needs {amount_out} stETH but reserve has "
                    f"{self.reserve1}"
                )
            transferred_shares = self._shares_by_pooled_eth(amount_out)
            if transferred_shares > self.steth_shares:
                raise Unsupported(f"{self.record.pool_id} stETH transfer exceeds ARM shares")
            return amount_out, OriginLidoArmState(
                self.record,
                self.weth,
                self.steth,
                self.traderate0,
                self.traderate1,
                _checked_add(self.weth_balance, amount_in, "WETH balance"),
                self.withdraws_queued,
                self.withdraws_claimed,
                self.steth_shares - transferred_shares,
                self.total_pooled_ether,
                self.total_shares,
            )
        if amount_out > self.reserve0:
            raise Unsupported(
                f"{self.record.pool_id} stETH->WETH needs {amount_out} WETH but reserve has "
                f"{self.reserve0}"
            )
        if self.outstanding_withdrawals and self.weth_balance < self.outstanding_withdrawals:
            raise Unsupported(
                f"{self.record.pool_id} raw WETH balance is below outstanding withdrawals; "
                "the source liquidity gate reverts even for zero output"
            )
        return amount_out, OriginLidoArmState(
            self.record,
            self.weth,
            self.steth,
            self.traderate0,
            self.traderate1,
            self.weth_balance - amount_out,
            self.withdraws_queued,
            self.withdraws_claimed,
            _checked_add(
                self.steth_shares, self._shares_by_pooled_eth(amount_in), "stETH shares"
            ),
            self.total_pooled_ether,
            self.total_shares,
        )

    def _direction(self, token_in: Address, token_out: Address) -> str:
        try:
            token_in, token_out = norm_address(token_in), norm_address(token_out)
        except (AttributeError, TypeError, ValueError) as exc:
            raise Unsupported("malformed Origin Lido ARM token direction") from exc
        if (token_in, token_out) == (WETH, STETH):
            return "weth_to_steth"
        if (token_in, token_out) == (STETH, WETH):
            return "steth_to_weth"
        raise Unsupported(f"pair {token_in}->{token_out} is not Origin Lido ARM WETH/stETH")

    def _shares_by_pooled_eth(self, amount: int) -> int:
        return _checked_mul(amount, self.total_shares, "Lido shares conversion") // self.total_pooled_ether

    def _pooled_eth_by_shares(self, shares: int) -> int:
        return _checked_mul(shares, self.total_pooled_ether, "Lido pooled ETH conversion") // self.total_shares


class OriginArmAdapter:
    """``SourceAdapter`` for the qualified Lido ARM, not newer Origin ARMs."""

    family = "origin_arm"

    @staticmethod
    def _balance_spec(token: Address, tag: str) -> CallSpec:
        return CallSpec(token, SEL_BALANCE_OF + abi_encode(["address"], [ARM]).hex(), tag)

    @staticmethod
    def _shares_spec() -> CallSpec:
        return CallSpec(STETH, SEL_SHARES_OF + abi_encode(["address"], [ARM]).hex(), TAG_STETH_SHARES)

    def _specs(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        use_reserves, paused_getter, _implementation = _historical_observation(pool, block)
        specs = [
            CallSpec(ARM, SEL_TOKEN0, TAG_TOKEN0),
            CallSpec(ARM, SEL_TOKEN1, TAG_TOKEN1),
            CallSpec(ARM, SEL_TRADERATE0, TAG_TRADERATE0),
            CallSpec(ARM, SEL_TRADERATE1, TAG_TRADERATE1),
            CallSpec(ARM, SEL_WITHDRAWS_QUEUED, TAG_WITHDRAWS_QUEUED),
            CallSpec(ARM, SEL_WITHDRAWS_CLAIMED, TAG_WITHDRAWS_CLAIMED),
            self._balance_spec(WETH, TAG_WETH_BALANCE),
            self._balance_spec(STETH, TAG_STETH_BALANCE),
            self._shares_spec(),
            CallSpec(STETH, SEL_TOTAL_POOLED_ETHER, TAG_TOTAL_POOLED_ETHER),
            CallSpec(STETH, SEL_TOTAL_SHARES, TAG_TOTAL_SHARES),
        ]
        if paused_getter:
            specs.insert(2, CallSpec(ARM, SEL_PAUSED, TAG_PAUSED))
        if use_reserves:
            specs.append(CallSpec(ARM, SEL_GET_RESERVES, TAG_RESERVES))
        specs.append(CallSpec(ARM, SEL_IMPLEMENTATION, TAG_IMPLEMENTATION))
        return specs

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        _validate_pool(pool)
        return self._specs(pool, block)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        _validate_pool(pool)
        _historical_observation(pool, block)
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> OriginLidoArmState:
        _validate_pool(pool)
        use_reserves, paused_getter, expected_implementation = _historical_observation(
            pool, snapshot.block
        )
        specs = {spec.tag: spec for spec in self._specs(pool, snapshot.block)}
        token0 = self._address(snapshot, specs[TAG_TOKEN0], "token0()")
        token1 = self._address(snapshot, specs[TAG_TOKEN1], "token1()")
        if (token0, token1) != (WETH, STETH):
            raise Unsupported("Origin Lido ARM immutable token identity mismatch")
        implementation = self._address(snapshot, specs[TAG_IMPLEMENTATION], "implementation()")
        if implementation != expected_implementation:
            raise Unsupported("Origin Lido ARM proxy implementation disagrees with history")
        paused = self._bool(snapshot, specs[TAG_PAUSED], "paused()") if paused_getter else False
        if paused:
            raise Unsupported(f"{pool.pool_id} is paused")
        state = OriginLidoArmState(
            record=pool,
            weth=_match_token(pool, WETH),
            steth=_match_token(pool, STETH),
            traderate0=self._uint(snapshot, specs[TAG_TRADERATE0], "traderate0()"),
            traderate1=self._uint(snapshot, specs[TAG_TRADERATE1], "traderate1()"),
            weth_balance=self._uint(snapshot, specs[TAG_WETH_BALANCE], "WETH.balanceOf(ARM)"),
            withdraws_queued=self._uint(
                snapshot, specs[TAG_WITHDRAWS_QUEUED], "withdrawsQueued()"
            ),
            withdraws_claimed=self._uint(
                snapshot, specs[TAG_WITHDRAWS_CLAIMED], "withdrawsClaimed()"
            ),
            steth_shares=self._uint(snapshot, specs[TAG_STETH_SHARES], "stETH.sharesOf(ARM)"),
            total_pooled_ether=self._uint(
                snapshot, specs[TAG_TOTAL_POOLED_ETHER], "stETH.getTotalPooledEther()"
            ),
            total_shares=self._uint(snapshot, specs[TAG_TOTAL_SHARES], "stETH.getTotalShares()"),
        )
        steth_balance = self._uint(snapshot, specs[TAG_STETH_BALANCE], "stETH.balanceOf(ARM)")
        if steth_balance != state.reserve1:
            raise Unsupported("Origin Lido ARM stETH balance disagrees with sharesOf(ARM)")
        if use_reserves:
            reserve0, reserve1 = self._reserves(snapshot, specs[TAG_RESERVES])
            if (reserve0, reserve1) != (state.reserve0, state.reserve1):
                raise Unsupported("Origin Lido ARM getReserves() disagrees with balance/queue formula")
        return state

    @staticmethod
    def _raw(snapshot: Snapshot, spec: CallSpec, what: str) -> bytes:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for Origin Lido ARM at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for Origin Lido ARM at {snapshot.block.number}")
        try:
            if not result.raw.startswith("0x"):
                raise ValueError("missing 0x prefix")
            return bytes.fromhex(result.raw[2:])
        except (AttributeError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed hex for {what} at {snapshot.block.number}") from exc

    def _uint(self, snapshot: Snapshot, spec: CallSpec, what: str) -> int:
        try:
            return _uint(abi_decode(["uint256"], self._raw(snapshot, spec, what))[0], what)
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc

    def _address(self, snapshot: Snapshot, spec: CallSpec, what: str) -> Address:
        try:
            return norm_address(abi_decode(["address"], self._raw(snapshot, spec, what))[0])
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc

    def _bool(self, snapshot: Snapshot, spec: CallSpec, what: str) -> bool:
        try:
            value = abi_decode(["bool"], self._raw(snapshot, spec, what))[0]
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc
        if type(value) is not bool:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}")
        return value

    def _reserves(self, snapshot: Snapshot, spec: CallSpec) -> tuple[int, int]:
        try:
            values = abi_decode(["uint256", "uint256"], self._raw(snapshot, spec, "getReserves()"))
            return (_uint(values[0], "reserve0"), _uint(values[1], "reserve1"))
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed ABI for getReserves() at {snapshot.block.number}") from exc
