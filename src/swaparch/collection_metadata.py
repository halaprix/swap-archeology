"""Exact-block metadata hydration for collection without inventory mutation."""

from __future__ import annotations

from dataclasses import replace

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError

from .adapters import curve_ng, origin_arm
from .core.protocols import Snapshot, SourceAdapter, Unsupported
from .core.types import BlockRef, CallSpec, PoolRecord, norm_address


def _curve_spec(signature: str, pool: PoolRecord, tag: str) -> CallSpec:
    return CallSpec(
        norm_address(pool.deployment),
        curve_ng._selector(signature) + abi_encode(["address"], [norm_address(pool.pool)]).hex(),
        tag,
    )


class CollectionMetadataAdapter:
    """Wrap an adapter and supply only the exact-block identity it requires.

    Hydrated records live in this wrapper for one collection run.  Nothing here
    upgrades ``status`` or writes discovery/validation artifacts.
    """

    def __init__(self, adapter: SourceAdapter) -> None:
        self.adapter = adapter
        self.family = adapter.family
        self._hydrated: dict[tuple[str, str], PoolRecord] = {}

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        if self._needs_curve_metadata(pool, block):
            return self._curve_metadata_specs(pool)
        if self._needs_origin_metadata(pool, block):
            return self._origin_metadata_specs(pool)
        return self.adapter.read_requests(self.hydrated_record(pool, block), block)

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        hydrated = self.hydrated_record(pool, block)
        if hydrated is pool:
            if self._needs_curve_metadata(pool, block):
                pending = self._curve_hydrate(pool, block, snapshot)
                if pending:
                    return pending
            elif self._needs_origin_metadata(pool, block):
                pending = self._origin_hydrate(pool, block, snapshot)
                if pending:
                    return pending
            hydrated = self.hydrated_record(pool, block)
            return self.adapter.read_requests(hydrated, block) + self.adapter.dependent_requests(
                hydrated, block, snapshot
            )
        return self.adapter.dependent_requests(hydrated, block, snapshot)

    def load_state(self, pool: PoolRecord, snapshot: Snapshot):
        hydrated = self.hydrated_record(pool, snapshot.block)
        if (self._needs_curve_metadata(hydrated, snapshot.block)
                or self._needs_origin_metadata(hydrated, snapshot.block)):
            raise Unsupported(f"missing collection metadata for {pool.pool_id} at {snapshot.block.hash}")
        return self.adapter.load_state(hydrated, snapshot)

    def hydrated_record(self, pool: PoolRecord, block: BlockRef) -> PoolRecord:
        """Return this run's exact-block overlay, or the original record."""
        return self._hydrated.get((pool.pool_id, block.hash.lower()), pool)

    @staticmethod
    def _needs_curve_metadata(pool: PoolRecord, block: BlockRef) -> bool:
        return pool.family == "curve" and block.hash.lower() not in pool.config.get(
            "registry_observations", {}
        )

    @staticmethod
    def _needs_origin_metadata(pool: PoolRecord, block: BlockRef) -> bool:
        return pool.family == "origin_arm" and block.hash.lower() not in pool.config.get(
            "historical_observations", {}
        )

    @staticmethod
    def _curve_metadata_specs(pool: PoolRecord) -> list[CallSpec]:
        return [
            _curve_spec("get_coins(address)", pool, "collection:curve:coins"),
            _curve_spec("get_decimals(address)", pool, "collection:curve:decimals"),
            _curve_spec("get_base_pool(address)", pool, "collection:curve:base-pool"),
            _curve_spec("get_registry_handlers_from_pool(address)", pool, "collection:curve:handlers"),
        ]

    @staticmethod
    def _origin_metadata_specs(pool: PoolRecord) -> list[CallSpec]:
        arm = norm_address(pool.pool)
        return [CallSpec(arm, origin_arm.SEL_IMPLEMENTATION, "collection:origin:implementation")]

    @staticmethod
    def _origin_optional_specs(pool: PoolRecord) -> list[CallSpec]:
        arm = norm_address(pool.pool)
        return [
            CallSpec(arm, origin_arm.SEL_GET_RESERVES, "collection:origin:reserves"),
            CallSpec(arm, origin_arm.SEL_PAUSED, "collection:origin:paused"),
        ]

    def _curve_hydrate(self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot) -> list[CallSpec]:
        specs = self._curve_metadata_specs(pool)
        coins = self._decode(snapshot, specs[0], ["address[8]"], "Curve coins")
        decimals = self._decode(snapshot, specs[1], ["uint256[8]"], "Curve decimals")
        base_pool = self._decode(snapshot, specs[2], ["address"], "Curve base pool")[0]
        handlers = self._decode(snapshot, specs[3], ["address[10]"], "Curve registry handlers")
        zero = curve_ng.ZERO
        handler_addresses = tuple(norm_address(value) for value in handlers[0] if norm_address(value) != zero)
        base_specs = [
            CallSpec(
                norm_address(pool.deployment),
                curve_ng._selector("get_base_registry(address)")
                + abi_encode(["address"], [handler]).hex(),
                "collection:curve:base-registry",
            )
            for handler in handler_addresses
        ]
        if any(not snapshot.has(spec) for spec in base_specs):
            return base_specs
        observed_coins = tuple(norm_address(value) for value in coins[0] if norm_address(value) != zero)
        expected_coins = tuple(token.address for token in pool.tokens)
        if observed_coins != expected_coins:
            raise Unsupported("Curve registry coin identity disagrees with pool record")
        observed_decimals = tuple(decimals[0][:len(observed_coins)])
        if observed_decimals != tuple(token.decimals for token in pool.tokens):
            raise Unsupported("Curve registry decimals disagree with pool record")
        base_registries = [
            norm_address(self._decode(snapshot, spec, ["address"], "Curve base registry")[0])
            for spec in base_specs
        ]
        config = dict(pool.config)
        observations = dict(config.get("registry_observations", {}))
        observations[block.hash.lower()] = {
            "block": block.number,
            "coins": list(observed_coins),
            "decimals": list(observed_decimals),
            "base_pool": norm_address(base_pool),
            "registry_handlers": list(handler_addresses),
            "base_registries": base_registries,
        }
        config["registry_observations"] = observations
        self._hydrated[(pool.pool_id, block.hash.lower())] = replace(pool, config=config)
        return []

    def _origin_hydrate(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        implementation, = self._origin_metadata_specs(pool)
        address = norm_address(self._decode(snapshot, implementation, ["address"], "Origin implementation")[0])
        flags = self._origin_abi_flags(pool, address)
        if flags is None:
            reserves, paused = self._origin_optional_specs(pool)
            if not snapshot.has(reserves) or not snapshot.has(paused):
                return [reserves, paused]
            get_reserves = snapshot.get(reserves).success
            paused_getter = snapshot.get(paused).success
            if get_reserves:
                self._decode(snapshot, reserves, ["uint256", "uint256"], "Origin getReserves")
            if paused_getter:
                self._decode(snapshot, paused, ["bool"], "Origin paused")
        else:
            get_reserves, paused_getter = flags
        config = dict(pool.config)
        observations = dict(config.get("historical_observations", {}))
        observations[block.hash.lower()] = {
            "number": block.number,
            "implementation": address,
            "get_reserves": get_reserves,
            "paused_getter": paused_getter,
        }
        config["historical_observations"] = observations
        self._hydrated[(pool.pool_id, block.hash.lower())] = replace(pool, config=config)
        return []

    @staticmethod
    def _origin_abi_flags(pool: PoolRecord, implementation: str) -> tuple[bool, bool] | None:
        """Return capabilities only when every observation for an implementation agrees."""
        observations = pool.config.get("historical_observations", {})
        flags: set[tuple[bool, bool]] = set()
        if not isinstance(observations, dict):
            return None
        for observation in observations.values():
            if not isinstance(observation, dict):
                continue
            try:
                same_implementation = norm_address(observation["implementation"]) == implementation
            except (KeyError, TypeError, ValueError):
                continue
            get_reserves = observation.get("get_reserves")
            paused_getter = observation.get("paused_getter")
            if same_implementation:
                if type(get_reserves) is not bool or type(paused_getter) is not bool:
                    return None
                flags.add((get_reserves, paused_getter))
        return next(iter(flags)) if len(flags) == 1 else None

    @staticmethod
    def _decode(snapshot: Snapshot, spec: CallSpec, types: list[str], what: str):
        if not snapshot.has(spec) or not snapshot.get(spec).success:
            raise Unsupported(f"missing or failed {what}")
        try:
            return abi_decode(types, bytes.fromhex(snapshot.get(spec).raw.removeprefix("0x")))
        except (DecodingError, TypeError, ValueError) as exc:
            raise Unsupported(f"malformed {what}: {exc}") from None
