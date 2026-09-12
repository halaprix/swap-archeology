"""Exact-input model of Maker/Sky USDS connectors.

Includes:
1. ``DaiUsdsConverter`` (``0x3225737a9bbb6473cb4a45b7244aca2befdb276a``):
   Direct 1:1, zero-fee, integer-exact conversion between DAI and USDS via
   Maker/Sky DSS ``DaiJoin`` and ``UsdsJoin``.

2. ``UsdsPsmWrapper`` (``0xa188eec8f81263234da3622a406892f3d630f98c``):
   USDS<->USDC conversion wrapping ``DssLitePsm`` (``0xf6e72db5454dd049d0788e411b06cfaf16853042``).
   CRITICAL: It shares the EXACT same DAI buffer and pocket USDC reserves as LitePSM.
   It MUST NOT create independent duplicated USDC liquidity.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from ..core.protocols import Snapshot, Unsupported
from ..core.types import Address, BlockRef, CallSpec, PoolRecord, Token, norm_address

CONVERTER = "0x3225737a9bbb6473cb4a45b7244aca2befdb276a"
WRAPPER = "0xa188eec8f81263234da3622a406892f3d630f98c"
LITE_PSM = "0xf6e72db5454dd049d0788e411b06cfaf16853042"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
POCKET = "0x37305b1cd40574e4c5ce33f8e8306be057fd7341"
DAI_JOIN = "0x9759a6ac90977b93b58547b4a71c78317f391a28"
USDS_JOIN = "0x3c0f895007ca717aa01c8693e59df1e8c3777feb"

WAD = 10**18
RAY = 10**27
HALTED = 2**256 - 1
MAX_UINT256 = HALTED
MAX_CONVERSION_WAD = MAX_UINT256 // RAY


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


SEL_DAI_JOIN = _selector("daiJoin()")
SEL_USDS_JOIN = _selector("usdsJoin()")
SEL_DAI = _selector("dai()")
SEL_USDS = _selector("usds()")
SEL_GEM = _selector("gem()")
SEL_PSM = _selector("psm()")
SEL_POCKET = _selector("pocket()")
SEL_TO_18_CONVERSION_FACTOR = _selector("to18ConversionFactor()")
SEL_TIN = _selector("tin()")
SEL_TOUT = _selector("tout()")
SEL_LIVE = _selector("live()")
SEL_WARDS = _selector("wards(address)")
SEL_BALANCE_OF = _selector("balanceOf(address)")
SEL_ALLOWANCE = _selector("allowance(address,address)")

assert (
    SEL_DAI_JOIN,
    SEL_USDS_JOIN,
    SEL_DAI,
    SEL_USDS,
    SEL_GEM,
    SEL_PSM,
    SEL_POCKET,
    SEL_TO_18_CONVERSION_FACTOR,
    SEL_TIN,
    SEL_TOUT,
    SEL_LIVE,
    SEL_WARDS,
    SEL_BALANCE_OF,
    SEL_ALLOWANCE,
) == (
    "0xc11645bc",
    "0xfa1e2e86",
    "0xf4b9fa75",
    "0x4cf282fb",
    "0x7bd2bea7",
    "0x04bda262",
    "0xcccef9e2",
    "0x4010f777",
    "0x568d4b6f",
    "0xfae036d5",
    "0x957aa58c",
    "0xbf353dbb",
    "0x70a08231",
    "0xdd62ed3e",
), "USDS connector selector table drifted"

TAG_CONV_DAI_JOIN = "usds_conv:daiJoin"
TAG_CONV_USDS_JOIN = "usds_conv:usdsJoin"
TAG_CONV_DAI = "usds_conv:dai"
TAG_CONV_USDS = "usds_conv:usds"
TAG_CONV_LIVE = "usds_conv:daiJoin.live"
TAG_CONV_USDS_WARDS = "usds_conv:usds.wards"
TAG_CONV_DAI_WARDS = "usds_conv:dai.wards"

TAG_WRAP_PSM = "usds_wrap:psm"
TAG_WRAP_GEM = "usds_wrap:gem"
TAG_WRAP_USDS = "usds_wrap:usds"
TAG_WRAP_POCKET = "usds_wrap:pocket"
TAG_WRAP_TO_18 = "usds_wrap:to18ConversionFactor"
TAG_WRAP_TIN = "usds_wrap:tin"
TAG_WRAP_TOUT = "usds_wrap:tout"
TAG_WRAP_DAI_BUFFER = "usds_wrap:psmDaiBalance"
TAG_WRAP_POCKET_GEM = "usds_wrap:pocketGemBalance"
TAG_WRAP_POCKET_GEM_ALLOWANCE = "usds_wrap:pocketGemAllowance"


def _checked_mul(left: int, right: int, what: str) -> int:
    if left and right > MAX_UINT256 // left:
        raise Unsupported(f"{what} overflows uint256")
    return left * right


def _checked_add(left: int, right: int, what: str) -> int:
    if left > MAX_UINT256 - right:
        raise Unsupported(f"{what} overflows uint256")
    return left + right


def _uint(value: object, what: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_UINT256:
        raise Unsupported(f"{what} must be a uint256 integer")
    return value


# ---------------------------------------------------------------------------
# 1. DaiUsdsConverter: 1:1 exact zero-fee connector between DAI and USDS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DaiUsdsState:
    """State for the DAI<->USDS 1:1 Converter (0x3225...)."""

    record: PoolRecord
    dai: Token
    usds: Token
    dai_join: Address
    usds_join: Address
    live: int
    usds_wards: int
    dai_wards: int

    def __post_init__(self) -> None:
        _uint(self.live, "live")
        _uint(self.usds_wards, "usds_wards")
        _uint(self.dai_wards, "dai_wards")
        if self.dai.decimals != 18:
            raise Unsupported(f"{self.record.pool_id} dai() does not have 18 decimals")
        if self.usds.decimals != 18:
            raise Unsupported(f"{self.record.pool_id} usds() does not have 18 decimals")

    def tokens(self) -> tuple[Token, ...]:
        return (self.dai, self.usds)

    def capacity_ids(self) -> tuple[str, ...]:
        # Converter does not consume LitePSM USDC or DAI buffer reserves.
        # Its conversions mint/burn via DSS engine without local token balance limits.
        return ()

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return 95_000

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, DaiUsdsState]:
        direction = self._direction(token_in, token_out)
        _uint(amount_in, "exact input")

        # Canonical join/exit arithmetic checks:
        # UsdsJoin.exit/join and DaiJoin.join/exit scale amounts by RAY (10**27) in Vat.move.
        # Solidity 0.8.21 checks overflow on RAY * wad, and DaiJoin.mul(ONE, wad) requires
        # (z = x * y) / y == x. Any input exceeding MAX_CONVERSION_WAD will revert on-chain.
        if amount_in > MAX_CONVERSION_WAD:
            raise Unsupported(
                f"{self.record.pool_id} amount_in {amount_in} exceeds maximum conversion "
                f"limit {MAX_CONVERSION_WAD} (wad * RAY overflows uint256 in DSS engine)"
            )

        # Canonical direction-specific liveness and mint-authority gating:
        # DAI -> USDS (daiToUsds):
        #   daiJoin.join burns DAI (no live check in DaiJoin.join, no dai.wards needed).
        #   usdsJoin.exit calls usds.mint(usr, wad) (requires usds.wards(usdsJoin) == 1).
        # USDS -> DAI (usdsToDai):
        #   usdsJoin.join burns USDS (no live check, no usds.wards needed).
        #   daiJoin.exit requires live == 1 (require(live == 1, "DaiJoin/not-live")),
        #   and calls dai.mint(usr, wad) (requires dai.wards(daiJoin) == 1).
        if direction == "dai_to_usds":
            if self.usds_wards != 1:
                raise Unsupported(
                    f"{self.record.pool_id} usdsJoin lacks USDS mint authority (usds.wards != 1)"
                )
        elif direction == "usds_to_dai":
            if self.live != 1:
                raise Unsupported(
                    f"{self.record.pool_id} daiJoin is not live / caged (live != 1)"
                )
            if self.dai_wards != 1:
                raise Unsupported(
                    f"{self.record.pool_id} daiJoin lacks DAI mint authority (dai.wards != 1)"
                )

        if amount_in == 0:
            return 0, self

        # 1:1 exact conversion, zero fee, perfect integer conservation
        return amount_in, self

    def _direction(self, token_in: Address, token_out: Address) -> str:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if token_in == self.dai.address and token_out == self.usds.address:
            return "dai_to_usds"
        if token_in == self.usds.address and token_out == self.dai.address:
            return "usds_to_dai"
        raise Unsupported(
            f"pair {token_in}->{token_out} is not DaiUsds {self.dai.address}/{self.usds.address} "
            f"for {self.record.pool_id}"
        )


class DaiUsdsAdapter:
    """SourceAdapter for the permissionless DaiUsds converter (0x3225...)."""

    family = "maker_sky_psm"

    @staticmethod
    def _pool_address(pool: PoolRecord) -> Address:
        return norm_address(pool.pool)

    def _static_specs(self, pool: PoolRecord) -> list[CallSpec]:
        to = self._pool_address(pool)
        return [
            CallSpec(to, SEL_DAI_JOIN, TAG_CONV_DAI_JOIN),
            CallSpec(to, SEL_USDS_JOIN, TAG_CONV_USDS_JOIN),
            CallSpec(to, SEL_DAI, TAG_CONV_DAI),
            CallSpec(to, SEL_USDS, TAG_CONV_USDS),
        ]

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._validate_pool(pool)
        return self._static_specs(pool)

    @staticmethod
    def wards_spec(token: Address, user: Address, tag: str) -> CallSpec:
        data = SEL_WARDS + abi_encode(["address"], [norm_address(user)]).hex()
        return CallSpec(norm_address(token), data, tag)

    @staticmethod
    def live_spec(contract: Address, tag: str) -> CallSpec:
        return CallSpec(norm_address(contract), SEL_LIVE, tag)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        if not all(snapshot.has(spec) for spec in static):
            return []
        dai_join = self._decode_address(snapshot, static[0], "daiJoin()")
        usds_join = self._decode_address(snapshot, static[1], "usdsJoin()")
        dai = self._decode_address(snapshot, static[2], "dai()")
        usds = self._decode_address(snapshot, static[3], "usds()")

        requested = [
            self.live_spec(dai_join, TAG_CONV_LIVE),
            self.wards_spec(usds, usds_join, TAG_CONV_USDS_WARDS),
            self.wards_spec(dai, dai_join, TAG_CONV_DAI_WARDS),
        ]
        return [spec for spec in requested if not snapshot.has(spec)]

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> DaiUsdsState:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        dai_join = self._decode_address(snapshot, static[0], "daiJoin()")
        usds_join = self._decode_address(snapshot, static[1], "usdsJoin()")
        dai_addr = self._decode_address(snapshot, static[2], "dai()")
        usds_addr = self._decode_address(snapshot, static[3], "usds()")

        if snapshot.block.chain != 1 or (dai_addr, usds_addr) != (DAI, USDS):
            raise Unsupported("DaiUsds immutable token identity mismatch")

        live = self._decode_uint(
            snapshot, self.live_spec(dai_join, TAG_CONV_LIVE), "daiJoin.live()"
        )
        usds_wards = self._decode_uint(
            snapshot, self.wards_spec(usds_addr, usds_join, TAG_CONV_USDS_WARDS), "usds.wards(usdsJoin)"
        )
        dai_wards = self._decode_uint(
            snapshot, self.wards_spec(dai_addr, dai_join, TAG_CONV_DAI_WARDS), "dai.wards(daiJoin)"
        )

        return DaiUsdsState(
            record=pool,
            dai=_match_token(pool, dai_addr, snapshot.block.chain),
            usds=_match_token(pool, usds_addr, snapshot.block.chain),
            dai_join=dai_join,
            usds_join=usds_join,
            live=live,
            usds_wards=usds_wards,
            dai_wards=dai_wards,
        )

    @staticmethod
    def _validate_pool(pool: PoolRecord) -> None:
        if (
            pool.chain != 1
            or pool.family != "maker_sky_psm"
            or norm_address(pool.pool) != CONVERTER
            or norm_address(pool.deployment) != CONVERTER
        ):
            raise Unsupported("not the qualified Ethereum DaiUsds converter deployment")
        if pool.config.get("model") != "DaiUsdsConverter":
            raise Unsupported(
                f"{pool.pool_id} is not a DaiUsdsConverter record "
                f"(config.model={pool.config.get('model')!r})"
            )

    @staticmethod
    def _raw(snapshot: Snapshot, spec: CallSpec, what: str) -> bytes:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for DaiUsds at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for DaiUsds at {snapshot.block.number}")
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


# ---------------------------------------------------------------------------
# 2. UsdsPsmWrapper: USDS<->USDC wrapper wrapping DssLitePsm (REFERENCE ONLY)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UsdsPsmWrapperState:
    """REFERENCE ONLY: Snapshot for UsdsPsmWrapper (0xa188...) sharing LitePSM reserves.

    The active integration uses one DaiUsdsConverter + existing LitePsmState.
    This wrapper implementation remains a separately qualified reference model,
    advertising LitePSM capacity IDs, but must not create a competing wrapper
    state in the active routing universe.
    """

    record: PoolRecord
    usds: Token
    gem: Token  # USDC
    psm: Address  # underlying LitePSM (0xf6e7...)
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
        if self.usds.decimals != 18:
            raise Unsupported(f"{self.record.pool_id} usds() does not have 18 decimals")
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
        return (self.usds, self.gem)

    def capacity_ids(self) -> tuple[str, ...]:
        # CRITICAL: Wrapper draws from the underlying LitePSM reserves.
        # It must advertise the LitePSM capacity IDs, NOT its own pool address,
        # so any shared capacity conflict is caught and never double-counted.
        underlying = norm_address(self.psm)
        return (
            f"maker_sky_psm:{underlying}:dai_buffer",
            f"maker_sky_psm:{underlying}:pocket_usdc",
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._direction(token_in, token_out)
        return 160_000

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        amount_out, _ = self.swap(token_in, token_out, amount_in)
        return amount_out

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, UsdsPsmWrapperState]:
        direction = self._direction(token_in, token_out)
        _uint(amount_in, "exact input")
        if amount_in == 0:
            self._require_open(direction)
            return 0, self
        if direction == "gem_to_usds":
            return self._sell_gem(amount_in)
        return self._buy_gem_exact_input(amount_in)

    def _direction(self, token_in: Address, token_out: Address) -> str:
        token_in, token_out = norm_address(token_in), norm_address(token_out)
        if token_in == self.gem.address and token_out == self.usds.address:
            return "gem_to_usds"
        if token_in == self.usds.address and token_out == self.gem.address:
            return "usds_to_gem"
        raise Unsupported(
            f"pair {token_in}->{token_out} is not UsdsPsmWrapper {self.gem.address}/{self.usds.address} "
            f"for {self.record.pool_id}"
        )

    def _sell_gem(self, gem_in: int) -> tuple[int, UsdsPsmWrapperState]:
        self._require_open("gem_to_usds")
        gross = _checked_mul(gem_in, self.to18_conversion_factor, "sellGem gross")
        fee = _checked_mul(gross, self.tin, "sellGem fee") // WAD
        usds_out = gross - fee
        if usds_out > self.dai_buffer:
            raise Unsupported(
                f"{self.record.pool_id} sellGem needs {usds_out} DAI/USDS buffer but has "
                f"{self.dai_buffer}"
            )
        return usds_out, UsdsPsmWrapperState(
            record=self.record,
            usds=self.usds,
            gem=self.gem,
            psm=self.psm,
            to18_conversion_factor=self.to18_conversion_factor,
            tin=self.tin,
            tout=self.tout,
            dai_buffer=self.dai_buffer - usds_out,
            pocket_gem=_checked_add(self.pocket_gem, gem_in, "pocket gem balance"),
            pocket_gem_allowance=self.pocket_gem_allowance,
        )

    def _buy_gem_required_usds(self, gem_out: int) -> int:
        gross = _checked_mul(gem_out, self.to18_conversion_factor, "buyGem gross")
        fee = _checked_mul(gross, self.tout, "buyGem fee") // WAD
        return _checked_add(gross, fee, "buyGem usdsIn")

    def _buy_gem_exact_input(self, usds_in: int) -> tuple[int, UsdsPsmWrapperState]:
        self._require_open("usds_to_gem")
        upper = usds_in // self.to18_conversion_factor
        low, high = 0, upper
        while low < high:
            middle = (low + high + 1) // 2
            try:
                required = self._buy_gem_required_usds(middle)
            except Unsupported:
                high = middle - 1
                continue
            if required <= usds_in:
                low = middle
            else:
                high = middle - 1
        gem_out = low
        required = self._buy_gem_required_usds(gem_out)
        if required != usds_in:
            raise Unsupported(
                f"{self.record.pool_id} USDS->USDC exact input {usds_in} is unattainable: "
                f"buyGem({gem_out}) spends {required}, leaving {usds_in - required} unspent"
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
        return gem_out, UsdsPsmWrapperState(
            record=self.record,
            usds=self.usds,
            gem=self.gem,
            psm=self.psm,
            to18_conversion_factor=self.to18_conversion_factor,
            tin=self.tin,
            tout=self.tout,
            dai_buffer=_checked_add(self.dai_buffer, usds_in, "DAI balance"),
            pocket_gem=self.pocket_gem - gem_out,
            pocket_gem_allowance=self.pocket_gem_allowance - gem_out,
        )

    def _require_open(self, direction: str) -> None:
        if direction == "gem_to_usds" and self.tin == HALTED:
            raise Unsupported(f"{self.record.pool_id} sellGem is halted (tin == HALTED)")
        if direction == "usds_to_gem" and self.tout == HALTED:
            raise Unsupported(f"{self.record.pool_id} buyGem is halted (tout == HALTED)")


class UsdsPsmWrapperAdapter:
    """REFERENCE ONLY: SourceAdapter for the UsdsPsmWrapper (0xa188...)."""

    family = "maker_sky_psm"

    @staticmethod
    def _pool_address(pool: PoolRecord) -> Address:
        return norm_address(pool.pool)

    def _static_specs(self, pool: PoolRecord) -> list[CallSpec]:
        to = self._pool_address(pool)
        return [
            CallSpec(to, SEL_PSM, TAG_WRAP_PSM),
            CallSpec(to, SEL_GEM, TAG_WRAP_GEM),
            CallSpec(to, SEL_USDS, TAG_WRAP_USDS),
            CallSpec(to, SEL_POCKET, TAG_WRAP_POCKET),
            CallSpec(to, SEL_TO_18_CONVERSION_FACTOR, TAG_WRAP_TO_18),
            CallSpec(to, SEL_TIN, TAG_WRAP_TIN),
            CallSpec(to, SEL_TOUT, TAG_WRAP_TOUT),
        ]

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        self._validate_pool(pool)
        return self._static_specs(pool)

    @staticmethod
    def balance_spec(token: Address, owner: Address, tag: str) -> CallSpec:
        data = SEL_BALANCE_OF + abi_encode(["address"], [owner]).hex()
        return CallSpec(norm_address(token), data, tag)

    @staticmethod
    def allowance_spec(token: Address, owner: Address, spender: Address, tag: str) -> CallSpec:
        data = SEL_ALLOWANCE + abi_encode(["address", "address"], [owner, spender]).hex()
        return CallSpec(norm_address(token), data, tag)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        if not all(snapshot.has(spec) for spec in static):
            return []
        psm = self._decode_address(snapshot, static[0], "psm()")
        gem = self._decode_address(snapshot, static[1], "gem()")
        pocket = self._decode_address(snapshot, static[3], "pocket()")

        # Underlying LitePSM holds DAI in psm contract; gem is in pocket with allowance to psm
        requested = [
            self.balance_spec(DAI, psm, TAG_WRAP_DAI_BUFFER),
            self.balance_spec(gem, pocket, TAG_WRAP_POCKET_GEM),
            self.allowance_spec(gem, pocket, psm, TAG_WRAP_POCKET_GEM_ALLOWANCE),
        ]
        return [spec for spec in requested if not snapshot.has(spec)]

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> UsdsPsmWrapperState:
        self._validate_pool(pool)
        static = self._static_specs(pool)
        psm = self._decode_address(snapshot, static[0], "psm()")
        gem_address = self._decode_address(snapshot, static[1], "gem()")
        usds_address = self._decode_address(snapshot, static[2], "usds()")
        pocket = self._decode_address(snapshot, static[3], "pocket()")

        if (
            snapshot.block.chain != 1
            or (gem_address, usds_address, psm, pocket) != (USDC, USDS, LITE_PSM, POCKET)
        ):
            raise Unsupported("UsdsPsmWrapper immutable token/psm identity mismatch")

        factor = self._decode_uint(snapshot, static[4], "to18ConversionFactor()")
        tin = self._decode_uint(snapshot, static[5], "tin()")
        tout = self._decode_uint(snapshot, static[6], "tout()")

        dai_buffer = self._decode_uint(
            snapshot, self.balance_spec(DAI, psm, TAG_WRAP_DAI_BUFFER), "DAI.balanceOf(litePsm)"
        )
        pocket_gem = self._decode_uint(
            snapshot, self.balance_spec(gem_address, pocket, TAG_WRAP_POCKET_GEM), "gem.balanceOf(pocket)"
        )
        pocket_gem_allowance = self._decode_uint(
            snapshot,
            self.allowance_spec(gem_address, pocket, psm, TAG_WRAP_POCKET_GEM_ALLOWANCE),
            "gem.allowance(pocket, litePsm)",
        )

        return UsdsPsmWrapperState(
            record=pool,
            usds=_match_token(pool, usds_address, snapshot.block.chain),
            gem=_match_token(pool, gem_address, snapshot.block.chain),
            psm=psm,
            to18_conversion_factor=factor,
            tin=tin,
            tout=tout,
            dai_buffer=dai_buffer,
            pocket_gem=pocket_gem,
            pocket_gem_allowance=pocket_gem_allowance,
        )

    @staticmethod
    def _validate_pool(pool: PoolRecord) -> None:
        if (
            pool.chain != 1
            or pool.family != "maker_sky_psm"
            or norm_address(pool.pool) != WRAPPER
            or norm_address(pool.deployment) != WRAPPER
        ):
            raise Unsupported("not the qualified Ethereum UsdsPsmWrapper deployment")
        if pool.config.get("model") != "UsdsPsmWrapper":
            raise Unsupported(
                f"{pool.pool_id} is not a UsdsPsmWrapper record "
                f"(config.model={pool.config.get('model')!r})"
            )

    @staticmethod
    def _raw(snapshot: Snapshot, spec: CallSpec, what: str) -> bytes:
        if not snapshot.has(spec):
            raise Unsupported(f"missing {what} for UsdsPsmWrapper at {snapshot.block.number}")
        result = snapshot.get(spec)
        if not result.success:
            raise Unsupported(f"failed {what} for UsdsPsmWrapper at {snapshot.block.number}")
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


# ---------------------------------------------------------------------------
# 3. Unified UsdsConnectorAdapter handling both Maker/Sky USDS records
# ---------------------------------------------------------------------------

class UsdsConnectorAdapter:
    """Unified SourceAdapter routing to DaiUsdsAdapter or UsdsPsmWrapperAdapter."""

    family = "maker_sky_psm"

    def __init__(self) -> None:
        self.conv_adapter = DaiUsdsAdapter()
        self.wrap_adapter = UsdsPsmWrapperAdapter()

    def _delegate(self, pool: PoolRecord):
        norm_pool = norm_address(pool.pool)
        if norm_pool == CONVERTER or pool.config.get("model") == "DaiUsdsConverter":
            return self.conv_adapter
        if norm_pool == WRAPPER or pool.config.get("model") == "UsdsPsmWrapper":
            return self.wrap_adapter
        raise Unsupported(f"{pool.pool_id} is neither DaiUsdsConverter nor UsdsPsmWrapper")

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        return self._delegate(pool).read_requests(pool, block)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        return self._delegate(pool).dependent_requests(pool, block, snapshot)

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> DaiUsdsState | UsdsPsmWrapperState:
        return self._delegate(pool).load_state(pool, snapshot)


def _match_token(pool: PoolRecord, address: Address, chain: int) -> Token:
    for token in pool.tokens:
        if token.address == address and token.chain == chain:
            return token
    raise Unsupported(
        f"pool {pool.pool_id} reports token {address} absent from its discovered record"
    )
