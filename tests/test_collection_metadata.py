"""Collection-only metadata stays in memory while unblocking adapter reads."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from eth_abi import encode

from swaparch.adapters import curve_ng, origin_arm
from swaparch.adapters.origin_arm import ARM, STETH, WETH, OriginArmAdapter
from swaparch.collection_metadata import CollectionMetadataAdapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

META = "0xf98b45fa17de75fb1ad0e7afd971b0ca00e379fc"
POOL = "0x02950460e2b9529d0e00284a5fa2d7bdf3fa4d72"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
BLOCK = BlockRef(1, 123, "0x" + "12" * 32, 0)


class Snapshot:
    def __init__(self, calls: list[CallResult], block: BlockRef = BLOCK) -> None:
        self.block = block
        self.calls = {(call.spec.to, call.spec.data): call for call in calls}

    def has(self, spec: CallSpec) -> bool:
        return (spec.to, spec.data) in self.calls

    def get(self, spec: CallSpec) -> CallResult:
        return self.calls[(spec.to, spec.data)]


@dataclass
class Adapter:
    family: str = "curve"

    def read_requests(self, pool, block):
        assert BLOCK.hash in pool.config["registry_observations"]
        return [CallSpec(POOL, "0x12345678", "state")]

    def dependent_requests(self, pool, block, snapshot):
        return []

    def load_state(self, pool, snapshot):
        return pool


def result(spec: CallSpec, types: list[str], values: list[object]) -> CallResult:
    return CallResult(spec, True, "0x" + encode(types, values).hex(), "fixture")


def test_curve_metadata_hydrates_only_the_wrapper_record():
    record = PoolRecord(
        family="curve", chain=1, pool_id=f"curve:{META}:{POOL}", deployment=META, pool=POOL,
        tokens=(Token(1, USDC, "USDC", 6), Token(1, USDT, "USDT", 6)),
        config={"transfer_semantics": {USDC: "standard", USDT: "standard"}},
        created_block=1, discovered_by={}, status=SupportStatus.SUPPORTED,
    )
    adapter = CollectionMetadataAdapter(Adapter())
    specs = adapter.read_requests(record, BLOCK)
    zero = curve_ng.ZERO
    calls = [
        result(specs[0], ["address[8]"], [[USDC, USDT, *([zero] * 6)]]),
        result(specs[1], ["uint256[8]"], [[6, 6, *([0] * 6)]]),
        result(specs[2], ["address"], [zero]),
        result(specs[3], ["address[10]"], [[META, *([zero] * 9)]]),
    ]
    snapshot = Snapshot(calls)
    base_specs = adapter.dependent_requests(record, BLOCK, snapshot)
    assert len(base_specs) == 1
    snapshot.calls[(base_specs[0].to, base_specs[0].data)] = result(
        base_specs[0], ["address"], [curve_ng.FACTORY]
    )
    assert adapter.dependent_requests(record, BLOCK, snapshot) == [CallSpec(POOL, "0x12345678", "state")]
    hydrated = adapter.hydrated_record(record, BLOCK)
    assert hydrated is not record
    assert "registry_observations" not in record.config
    assert hydrated.config["registry_observations"][BLOCK.hash]["base_registries"] == [curve_ng.FACTORY]
    # Already pinned metadata also works in a fresh wrapper without re-probing it.
    replay = CollectionMetadataAdapter(Adapter())
    assert replay.read_requests(hydrated, BLOCK) == [CallSpec(POOL, "0x12345678", "state")]
    assert replay.load_state(hydrated, snapshot) == hydrated


def _origin_record(observations: dict[str, object]) -> PoolRecord:
    return PoolRecord(
        family="origin_arm", chain=1, pool_id=f"origin_arm:{ARM}:{ARM}", deployment=ARM, pool=ARM,
        tokens=(Token(1, WETH, "WETH", 18), Token(1, STETH, "stETH", 18)),
        config={"kind": "arm", "generation": "traderate (old) ABI",
                "historical_observations": observations},
        created_block=1, discovered_by={}, status=SupportStatus.SUPPORTED,
    )


def _origin_block(byte: str, number: int) -> BlockRef:
    return BlockRef(1, number, "0x" + byte * 32, 0)


def _observation(block: BlockRef, implementation: str, reserves: bool, paused: bool) -> dict[str, object]:
    return {"number": block.number, "implementation": implementation,
            "get_reserves": reserves, "paused_getter": paused}


OLD_IMPLEMENTATION = "0x1111111111111111111111111111111111111111"
NEW_IMPLEMENTATION = "0x2222222222222222222222222222222222222222"


@pytest.mark.parametrize(("reserves", "paused"), [(False, False), (True, True)])
def test_origin_known_implementation_reuses_consistent_optional_getters(reserves: bool, paused: bool) -> None:
    observed = _origin_block("31" if reserves else "30", 300 if reserves else 299)
    target = _origin_block("41" if reserves else "40", 400 if reserves else 399)
    implementation = NEW_IMPLEMENTATION if reserves else OLD_IMPLEMENTATION
    record = _origin_record({observed.hash: _observation(observed, implementation, reserves, paused)})
    adapter = CollectionMetadataAdapter(OriginArmAdapter())
    implementation_spec, = adapter.read_requests(record, target)
    snapshot = Snapshot([result(implementation_spec, ["address"], [implementation])], target)

    requested = adapter.dependent_requests(record, target, snapshot)

    tags = {spec.tag for spec in requested}
    assert (origin_arm.TAG_RESERVES in tags) is reserves
    assert (origin_arm.TAG_PAUSED in tags) is paused
    hydrated = adapter.hydrated_record(record, target)
    assert hydrated.config["historical_observations"][target.hash]["implementation"] == implementation


def test_origin_unknown_implementation_probes_optional_getters_before_read_plan() -> None:
    observed = _origin_block("50", 500)
    target = _origin_block("51", 501)
    record = _origin_record({observed.hash: _observation(observed, OLD_IMPLEMENTATION, False, False)})
    adapter = CollectionMetadataAdapter(OriginArmAdapter())
    implementation_spec, = adapter.read_requests(record, target)
    snapshot = Snapshot([result(implementation_spec, ["address"], [NEW_IMPLEMENTATION])], target)

    optional = adapter.dependent_requests(record, target, snapshot)
    assert [spec.tag for spec in optional] == ["collection:origin:reserves", "collection:origin:paused"]
    snapshot.calls.update({
        (optional[0].to, optional[0].data): result(optional[0], ["uint256", "uint256"], [1, 2]),
        (optional[1].to, optional[1].data): result(optional[1], ["bool"], [False]),
    })
    requested = adapter.dependent_requests(record, target, snapshot)
    assert {spec.tag for spec in requested} >= {origin_arm.TAG_RESERVES, origin_arm.TAG_PAUSED}


def test_origin_conflicting_implementation_history_reprobes_and_records_failures() -> None:
    first, second, target = _origin_block("60", 600), _origin_block("61", 601), _origin_block("62", 602)
    record = _origin_record({
        first.hash: _observation(first, OLD_IMPLEMENTATION, False, False),
        second.hash: _observation(second, OLD_IMPLEMENTATION, True, True),
    })
    adapter = CollectionMetadataAdapter(OriginArmAdapter())
    implementation_spec, = adapter.read_requests(record, target)
    snapshot = Snapshot([result(implementation_spec, ["address"], [OLD_IMPLEMENTATION])], target)
    optional = adapter.dependent_requests(record, target, snapshot)
    assert [spec.tag for spec in optional] == ["collection:origin:reserves", "collection:origin:paused"]
    snapshot.calls.update({
        (optional[0].to, optional[0].data): CallResult(optional[0], False, "0x", "fixture"),
        (optional[1].to, optional[1].data): CallResult(optional[1], False, "0x", "fixture"),
    })
    requested = adapter.dependent_requests(record, target, snapshot)
    assert origin_arm.TAG_RESERVES not in {spec.tag for spec in requested}
    assert adapter.hydrated_record(record, target).config["historical_observations"][target.hash]["get_reserves"] is False


def test_origin_optional_probe_rejects_malformed_success() -> None:
    target = _origin_block("70", 700)
    adapter = CollectionMetadataAdapter(OriginArmAdapter())
    record = _origin_record({})
    implementation_spec, = adapter.read_requests(record, target)
    snapshot = Snapshot([result(implementation_spec, ["address"], [OLD_IMPLEMENTATION])], target)
    optional = adapter.dependent_requests(record, target, snapshot)
    snapshot.calls.update({
        (optional[0].to, optional[0].data): result(optional[0], ["uint256"], [1]),
        (optional[1].to, optional[1].data): result(optional[1], ["bool"], [False]),
    })
    with pytest.raises(Unsupported, match="malformed Origin getReserves"):
        adapter.dependent_requests(record, target, snapshot)
