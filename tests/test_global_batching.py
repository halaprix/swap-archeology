"""All adapters share each acquisition-phase Multicall batch."""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode, encode

from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token
from swaparch.rpc.multicall import MULTICALL3
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire


def _spec(address: str, selector: str, tag: str) -> CallSpec:
    return CallSpec(address, selector, tag)


def _pool(family: str, number: int) -> PoolRecord:
    address = f"0x{number:040x}"
    return PoolRecord(
        family=family,
        chain=1,
        pool_id=f"{family}:{number}",
        deployment=address,
        pool=address,
        tokens=(Token(1, address, family, 18),),
        config={},
        created_block=1,
        discovered_by={},
        status=SupportStatus.SUPPORTED,
    )


@dataclass
class _Adapter:
    family: str
    initial: tuple[CallSpec, ...]
    dependent: tuple[CallSpec, ...]

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        return list(self.initial)

    def dependent_requests(self, pool: PoolRecord, block: BlockRef, snapshot) -> list[CallSpec]:
        return [] if all(snapshot.has(spec) for spec in self.dependent) else list(self.dependent)

    def load_state(self, pool: PoolRecord, snapshot):  # pragma: no cover - acquisition only
        raise AssertionError("not used")


class _FakeClient:
    def __init__(self) -> None:
        self.aggregates: list[tuple[CallSpec, ...]] = []
        self.network_requests = 0

    def cached_call(self, spec: CallSpec, block: BlockRef):
        return None

    def store_call_result(self, result: CallResult, block: BlockRef) -> None:
        pass

    def eth_call(self, to: str, data: str, block: BlockRef) -> CallResult:
        assert to == MULTICALL3
        calls = decode(["(address,bool,bytes)[]"], bytes.fromhex(data[10:]))[0]
        specs = tuple(CallSpec(address, "0x" + call_data.hex()) for address, _, call_data in calls)
        self.aggregates.append(specs)
        self.network_requests += 1
        raw = "0x" + encode(
            ["(bool,bytes)[]"],
            [[(True, bytes((index + 1,))) for index in range(len(specs))]],
        ).hex()
        return CallResult(CallSpec(to, data), True, raw, "fake")


def test_acquire_batches_protocols_by_phase_dedupes_and_resumes(tmp_path) -> None:
    block = BlockRef(1, 100, "0x" + "10" * 32, 0)
    shared = _spec("0x" + "11" * 20, "0x11111111", "shared-a")
    adapters = {
        "one": _Adapter(
            "one",
            (shared, _spec("0x" + "12" * 20, "0x12121212", "one")),
            (_spec("0x" + "13" * 20, "0x13131313", "one-dependent"),),
        ),
        "two": _Adapter(
            "two",
            (CallSpec(shared.to, shared.data, "shared-b"), _spec("0x" + "14" * 20, "0x14141414", "two")),
            (_spec("0x" + "15" * 20, "0x15151515", "two-dependent"),),
        ),
    }
    store = SnapshotStore(tmp_path, batch_size=3)
    client = _FakeClient()

    snapshot, report = acquire(adapters, [_pool("one", 1), _pool("two", 2)], block, store, client)

    assert [[spec.data for spec in batch] for batch in client.aggregates] == [
        ["0x11111111", "0x12121212", "0x14141414"],
        ["0x13131313", "0x15151515"],
    ]
    assert report.phases == 2
    assert report.specs_total == 6
    assert snapshot.has(shared)
    assert store.acquisition_batches == [
        {"logical_requested": 3, "missing": 3, "network_requests": 1},
        {"logical_requested": 2, "missing": 2, "network_requests": 1},
    ]

    resumed = _FakeClient()
    _, resumed_report = acquire(adapters, [_pool("one", 1), _pool("two", 2)], block, store, resumed)
    assert resumed.aggregates == []
    assert resumed.network_requests == 0
    assert resumed_report.network_requests == 0
    assert store.acquisition_batches[-1] == {
        "logical_requested": 3,
        "missing": 0,
        "network_requests": 0,
    }

    # Carry call identities to a new block, never the old returned values.
    next_block = BlockRef(1, 101, "0x" + "11" * 32, 1)
    fresh = _FakeClient()
    next_store = SnapshotStore(tmp_path, batch_size=10)
    next_snapshot, _ = acquire(adapters, [_pool("one", 1), _pool("two", 2)],
                              next_block, next_store, fresh,
                              prefetch_specs=store.requested_specs.values())
    assert len(fresh.aggregates) == 1 and len(fresh.aggregates[0]) == 5
    assert next_snapshot.block == next_block
    assert next_store.acquisition_batches[0]['missing'] == 5


def test_snapshot_store_honors_batch_size(tmp_path) -> None:
    block = BlockRef(1, 101, "0x" + "20" * 32, 0)
    specs = [_spec(f"0x{index:040x}", f"0x{index:08x}", str(index)) for index in range(1, 6)]
    client = _FakeClient()

    SnapshotStore(tmp_path, batch_size=2).build(block, specs, client)

    assert [len(batch) for batch in client.aggregates] == [2, 2, 1]
