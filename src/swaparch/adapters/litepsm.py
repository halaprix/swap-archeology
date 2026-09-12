"""Bounded, exact-input model of Maker/Sky ``DssLitePsm``.

The contract's two public directions are asymmetric.  ``sellGem`` consumes an
exact gem amount, while ``buyGem`` takes an exact gem *output* and transfers
only the computed DAI requirement.  The core interface is exact-input, so a
DAI quote is available only when its input equals that requirement exactly.
Returning a floored USDC amount would leave DAI unspent and is deliberately
rejected here.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

PSM = "0xf6e72db5454dd049d0788e411b06cfaf16853042"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
POCKET = "0x37305b1cd40574e4c5ce33f8e8306be057fd7341"
WAD = 10**18
HALTED = 2**256 - 1
MAX_UINT256 = HALTED


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_GEM = _selector("gem()")
SEL_DAI = _selector("dai()")
SEL_POCKET = _selector("pocket()")
SEL_TO_18_CONVERSION_FACTOR = _selector("to18ConversionFactor()")
SEL_TIN = _selector("tin()")
SEL_TOUT = _selector("tout()")
SEL_BALANCE_OF = _selector("balanceOf(address)")
SEL_ALLOWANCE = _selector("allowance(address,address)")

assert (
    SEL_GEM,
    SEL_DAI,
    SEL_POCKET,
    SEL_TO_18_CONVERSION_FACTOR,
    SEL_TIN,
    SEL_TOUT,
    SEL_BALANCE_OF,
    SEL_ALLOWANCE,
) == (
    "0x7bd2bea7",
    "0xf4b9fa75",
    "0xcccef9e2",
    "0x4010f777",
    "0x568d4b6f",
    "0xfae036d5",
    "0x70a08231",
    "0xdd62ed3e",
), "DssLitePsm selector table drifted"

TAG_GEM = "litepsm:gem"
TAG_DAI = "litepsm:dai"
TAG_POCKET = "litepsm:pocket"
TAG_TO_18 = "litepsm:to18ConversionFactor"
TAG_TIN = "litepsm:tin"
TAG_TOUT = "litepsm:tout"
TAG_DAI_BUFFER = "litepsm:daiBalance"
TAG_POCKET_GEM = "litepsm:pocketGemBalance"
TAG_POCKET_GEM_ALLOWANCE = "litepsm:pocketGemAllowance"


def _checked_mul(left: int, right: int, what: str) -> int:
    if left and right > MAX_UINT256 // left:
        raise Unsupported(f"DssLitePsm {what} overflows uint256")
    return left * right


def _checked_add(left: int, right: int, what: str) -> int:
    if left > MAX_UINT256 - right:
        raise Unsupported(f"DssLitePsm {what} overflows uint256")
    return left + right


def _uint(value: object, what: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_UINT256:
        raise Unsupported(f"{what} must be a uint256 integer")
    return value


@dataclass(frozen=True)
class LitePsmState:
    """One LitePSM snapshot with its two finite, shared inventories."""

    record: PoolRecord
    dai: Token
    gem: Token
    to18_conversion_factor: int
    tin: int
    tout: int
    dai_buffer: int
    pocket_gem: int
    pocket_gem_allowance: int

    def __post_init__(self) -> None:
        factor = _uint(self.to18_conversion_factor, "to18_conversion_factor")
        tin = _uint(self.tin, "tin")
        tout = _uint(self.tout, "tout")
        _uint(self.dai_buffer, "dai_buffer")
        _uint(self.pocket_gem, "pocket_gem")
        _uint(self.pocket_gem_allowance, "pocket_gem_allowance")
        if self.dai.decimals != 18:
            raise Unsupported(f"{self.record.pool_id} dai() does not have 18 decimals")
        if self.gem.decimals > 18:
            raise Unsupported(f"{self.record.pool_id} gem() has more than 18 decimals")
        expected_factor = 10 ** (18 - self.gem.decimals)
        if factor != expected_factor:
            raise Unsupported(
                f"{self.record.pool_id} to18ConversionFactor() is {factor}, "
                f"expected {expected_factor} for {self.gem.symbol}"
            )
        for name, fee in (("tin", tin), ("tout", tout)):
            if fee != HALTED and not 0 <= fee <= WAD:
                raise Unsupported(f"{self.record.pool_id} {name}() is outside [0,WAD] or HALTED")

    def tokens(self) -> tuple[Token, ...]:
        return (self.dai, self.gem)

    def capacity_ids(self) -> tuple[str, ...]:
        owner = norm_address(self.record.pool)
        return (
            f"{self.record.family}:{owner}:dai_buffer",
            f"{self.record.family}:{owner}:pocket_usdc",
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return None

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, LitePsmState]:
        direction = self._direction(token_in, token_out)
        _uint(amount_in, "exact input")
        if amount_in == 0:
            self._require_open(direction)
            return 0, self
        if direction == "gem_to_dai":
            return self._sell_gem(amount_in)
        return self._buy_gem_exact_input(amount_in)

    def _direction(self, token_in: Address, token_out: Address) -> str:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if token_in == self.gem.address and token_out == self.dai.address:
            return "gem_to_dai"
        if token_in == self.dai.address and token_out == self.gem.address:
            return "dai_to_gem"
        raise Unsupported(
            f"pair {token_in}->{token_out} is not LitePSM {self.gem.address}/{self.dai.address} "
            f"for {self.record.pool_id}"
        )

    def _sell_gem(self, gem_in: int) -> tuple[int, LitePsmState]:
        self._require_open("gem_to_dai")
        gross = _checked_mul(gem_in, self.to18_conversion_factor, "sellGem gross")
        fee = _checked_mul(gross, self.tin, "sellGem fee") // WAD
        dai_out = gross - fee
        if dai_out > self.dai_buffer:
            raise Unsupported(
                f"{self.record.pool_id} sellGem needs {dai_out} DAI but buffer has "
                f"{self.dai_buffer}"
            )
        return dai_out, LitePsmState(
            record=self.record,
            dai=self.dai,
            gem=self.gem,
            to18_conversion_factor=self.to18_conversion_factor,
            tin=self.tin,
            tout=self.tout,
            dai_buffer=self.dai_buffer - dai_out,
            pocket_gem=_checked_add(self.pocket_gem, gem_in, "pocket gem balance"),
            pocket_gem_allowance=self.pocket_gem_allowance,
        )

    def _buy_gem_required_dai(self, gem_out: int) -> int:
        gross = _checked_mul(gem_out, self.to18_conversion_factor, "buyGem gross")
        fee = _checked_mul(gross, self.tout, "buyGem fee") // WAD
        return _checked_add(gross, fee, "buyGem daiIn")

    def _buy_gem_exact_input(self, dai_in: int) -> tuple[int, LitePsmState]:
        self._require_open("dai_to_gem")

        # ``buyGem`` accepts a requested gem amount, so it cannot consume an
        # arbitrary DAI exact input.  Binary search the monotonic source formula
        # and require an exact preimage rather than hiding the residual.
        upper = dai_in // self.to18_conversion_factor
        low, high = 0, upper
        while low < high:
            middle = (low + high + 1) // 2
            try:
                required = self._buy_gem_required_dai(middle)
            except Unsupported:
                high = middle - 1
                continue
            if required <= dai_in:
                low = middle
            else:
                high = middle - 1
        gem_out = low
        required = self._buy_gem_required_dai(gem_out)
        if required != dai_in:
            raise Unsupported(
                f"{self.record.pool_id} DAI->USDC exact input {dai_in} is unattainable: "
                f"buyGem({gem_out}) spends {required}, leaving {dai_in - required} unspent"
            )
        if gem_out > self.pocket_gem:
            raise Unsupported(
                f"{self.record.pool_id} buyGem needs {gem_out} USDC but pocket has "
                f"{self.pocket_gem}"
            )
        if gem_out > self.pocket_gem_allowance:
            raise Unsupported(
                f"{self.record.pool_id} buyGem needs {gem_out} USDC allowance but pocket grants "
                f"{self.pocket_gem_allowance}"
            )
        return gem_out, LitePsmState(
            record=self.record,
            dai=self.dai,
            gem=self.gem,
            to18_conversion_factor=self.to18_conversion_factor,
            tin=self.tin,
            tout=self.tout,
            dai_buffer=_checked_add(self.dai_buffer, dai_in, "DAI balance"),
            pocket_gem=self.pocket_gem - gem_out,
            # Circle FiatTokenV1 transferFrom subtracts the allowance even at MAX_UINT256.
            pocket_gem_allowance=self.pocket_gem_allowance - gem_out,
        )

    def _require_open(self, direction: str) -> None:
        if direction == "gem_to_dai" and self.tin == HALTED:
            raise Unsupported(f"{self.record.pool_id} sellGem is halted (tin == HALTED)")
        if direction == "dai_to_gem" and self.tout == HALTED:
            raise Unsupported(f"{self.record.pool_id} buyGem is halted (tout == HALTED)")


class LitePsmAdapter:
    """``SourceAdapter`` for the permissionless ``DssLitePsm`` entrypoints."""

    family = "maker_sky_psm"

    @staticmethod
    def _pool_address(pool: PoolRecord) -> Address:
        return norm_address(pool.pool)

    def _static_specs(self, pool: PoolRecord) -> list[CallSpec]:
        to = self._pool_address(pool)
        return [
            CallSpec(to, SEL_GEM, TAG_GEM),
            CallSpec(to, SEL_DAI, TAG_DAI),
            CallSpec(to, SEL_POCKET, TAG_POCKET),
            CallSpec(to, SEL_TO_18_CONVERSION_FACTOR, TAG_TO_18),
            CallSpec(to, SEL_TIN, TAG_TIN),
            CallSpec(to, SEL_TOUT, TAG_TOUT),
        ]

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._validate_pool(pool)
        return self._static_specs(pool)

    def balance_spec(self, token: Address, owner: Address, tag: str) -> CallSpec:
        data = SEL_BALANCE_OF + abi_encode(["address"], [owner]).hex()
        return CallSpec(norm_address(token), data, tag)

    def allowance_spec(self, token: Address, owner: Address, spender: Address, tag: str) -> CallSpec:
        data = SEL_ALLOWANCE + abi_encode(["address", "address"], [owner, spender]).hex()
        return CallSpec(norm_address(token), data, tag)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        if not all(snapshot.has(spec) for spec in static):
            return []
        gem = self._decode_address(snapshot, static[0], "gem()")
        dai = self._decode_address(snapshot, static[1], "dai()")
        pocket = self._decode_address(snapshot, static[2], "pocket()")
        requested = [
            self.balance_spec(dai, self._pool_address(pool), TAG_DAI_BUFFER),
            self.balance_spec(gem, pocket, TAG_POCKET_GEM),
            self.allowance_spec(
                gem, pocket, self._pool_address(pool), TAG_POCKET_GEM_ALLOWANCE
            ),
        ]
        return [spec for spec in requested if not snapshot.has(spec)]

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> LitePsmState:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        gem_address = self._decode_address(snapshot, static[0], "gem()")
        dai_address = self._decode_address(snapshot, static[1], "dai()")
        pocket = self._decode_address(snapshot, static[2], "pocket()")
        if (snapshot.block.chain != 1 or (gem_address, dai_address, pocket) != (USDC, DAI, POCKET)):
            raise Unsupported("LitePSM immutable token/pocket identity mismatch")
        factor = self._decode_uint(snapshot, static[3], "to18ConversionFactor()")
        tin = self._decode_uint(snapshot, static[4], "tin()")
        tout = self._decode_uint(snapshot, static[5], "tout()")
        dai_buffer = self._decode_uint(
            snapshot, self.balance_spec(dai_address, self._pool_address(pool), TAG_DAI_BUFFER),
            "DAI.balanceOf(litePsm)",
        )
        pocket_gem = self._decode_uint(
            snapshot, self.balance_spec(gem_address, pocket, TAG_POCKET_GEM),
            "gem.balanceOf(pocket)",
        )
        pocket_gem_allowance = self._decode_uint(
            snapshot,
            self.allowance_spec(
                gem_address, pocket, self._pool_address(pool), TAG_POCKET_GEM_ALLOWANCE
            ),
            "gem.allowance(pocket, litePsm)",
        )
        return LitePsmState(
            record=pool,
            dai=_match_token(pool, dai_address, snapshot.block.chain),
            gem=_match_token(pool, gem_address, snapshot.block.chain),
            to18_conversion_factor=factor,
            tin=tin,
            tout=tout,
            dai_buffer=dai_buffer,
            pocket_gem=pocket_gem,
            pocket_gem_allowance=pocket_gem_allowance,
        )

    @staticmethod
    def _validate_pool(pool: PoolRecord) -> None:
        if (pool.chain != 1 or pool.family != "maker_sky_psm"
                or norm_address(pool.pool) != PSM or norm_address(pool.deployment) != PSM):
            raise Unsupported("not the qualified Ethereum USDC LitePSM deployment")
        if pool.config.get("model") != "dss-lite-psm":
            raise Unsupported(
                f"{pool.pool_id} is not a DssLitePsm record "
                f"(config.model={pool.config.get('model')!r})"
            )

    @staticmethod
    def _raw(snapshot: Snapshot, spec: CallSpec, what: str) -> bytes:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for LitePSM at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for LitePSM at {snapshot.block.number}")
        try:
            return bytes.fromhex(result.raw[2:])
        except ValueError as exc:
            raise Unsupported(f"malformed hex for {what} at {snapshot.block.number}") from exc

    def _decode_address(self, snapshot: Snapshot, spec: CallSpec, what: str) -> Address:
        try:
            return norm_address(abi_decode(["address"], self._raw(snapshot, spec, what))[0])
        except (DecodingError, ValueError, TypeError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc

    def _decode_uint(self, snapshot: Snapshot, spec: CallSpec, what: str) -> int:
        try:
            return abi_decode(["uint256"], self._raw(snapshot, spec, what))[0]
        except (DecodingError, ValueError, TypeError) as exc:
            raise Unsupported(f"malformed ABI for {what} at {snapshot.block.number}") from exc


def _match_token(pool: PoolRecord, address: Address, chain: int) -> Token:
    for token in pool.tokens:
        if token.address == address and token.chain == chain:
            return token
    raise Unsupported(
        f"pool {pool.pool_id} reports token {address} absent from its discovered record"
    )
