"""Read-plan reuse must not carry Origin ABI probes across implementations."""

from swaparch.collection import READ_PLAN_VERSION, _retained_prefetch, _retained_read_plan
from swaparch.core.types import CallSpec

ARM = "0x85b78aca6deae198fbf201c82daf6ca21942acc6"


def _report(*, reserves: object, paused: object, include_arm: bool = True) -> dict:
    record = {
        "family": "origin_arm", "deployment": ARM, "pool": ARM,
        "pool_id": f"origin_arm:{ARM}:{ARM}",
        "config": {"historical_observations": {"0xpin": {
            "number": 123, "implementation": "0x1111111111111111111111111111111111111111",
            "get_reserves": reserves, "paused_getter": paused,
        }}},
    }
    return {"block": {"number": 123, "hash": "0xpin"},
            "read_specs": [{"to": ARM, "data": "0x0902f1ac", "tag": "stale"},
                           {"to": ARM, "data": "0x5c975abb", "tag": "stale"}],
            "records": [{"record": record}] if include_arm else []}


def test_origin_supported_optional_getters_are_rebuilt_from_exact_record() -> None:
    assert READ_PLAN_VERSION == 4
    assert [spec.tag for spec in _retained_prefetch(_report(reserves=False, paused=False))] == []
    assert [spec.tag for spec in _retained_prefetch(_report(reserves=True, paused=False))] == [
        "origin_arm:getReserves"
    ]
    assert [spec.tag for spec in _retained_prefetch(_report(reserves=True, paused=True))] == [
        "origin_arm:getReserves", "origin_arm:paused"
    ]


def test_origin_optional_getter_with_stale_tag_is_not_reused() -> None:
    stale = CallSpec(ARM, "0x0902f1ac", "state")

    assert _retained_read_plan({(stale.to, stale.data): stale}) == {}
    assert _retained_prefetch(_report(reserves="true", paused=False)) == []
    assert _retained_prefetch(_report(reserves=True, paused=True, include_arm=False)) == []
    malformed = _report(reserves=True, paused=True)
    malformed["records"][0]["record"]["config"] = None
    assert _retained_prefetch(malformed) == []


def test_incoming_prefetch_preserves_rebuilt_tick_plan() -> None:
    specs = [CallSpec("0x" + byte * 20, "0x12345678", tag)
             for byte, tag in (("11", "univ3:tickLens"), ("12", "univ4:tickLiquidityBulk"))]
    report = _report(reserves=False, paused=False, include_arm=False)
    report["read_specs"] = [{"to": spec.to, "data": spec.data, "tag": spec.tag} for spec in specs]
    assert _retained_prefetch(report) == specs


def test_origin_prefetch_requires_matching_observation_hash_and_number() -> None:
    for key, value in (("hash", "0xother"), ("number", 124)):
        report = _report(reserves=True, paused=True)
        report["block"][key] = value
        assert _retained_prefetch(report) == []
