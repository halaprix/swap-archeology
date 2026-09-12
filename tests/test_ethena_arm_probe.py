"""Offline guard checks for the Ethena ARM evidence probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from swaparch.core.types import CallResult, CallSpec

PATH = Path(__file__).resolve().parents[1] / "scripts" / "ethena_arm_probe.py"
SPEC = importlib.util.spec_from_file_location("ethena_arm_probe", PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Snapshot:
    def __init__(self, results: dict[CallSpec, CallResult]) -> None:
        self.results = results

    def get(self, call: CallSpec) -> CallResult:
        return self.results[call]


def _result(call: CallSpec, success: bool = True, raw: str = "0x01") -> CallResult:
    return CallResult(call, success, raw, "test")


def test_precreation_blocks_are_a_zero_rpc_gate() -> None:
    result = probe.probe(probe.CREATED_BLOCK - 1)
    assert result == {
        "number": probe.CREATED_BLOCK - 1,
        "status": "not_deployed_before_creation",
        "created_block": probe.CREATED_BLOCK,
        "network_requests": 0,
    }


def test_epoch_selection_requires_one_complete_incompatible_shape() -> None:
    calls = probe.stage_one()
    legacy = Snapshot({call: _result(call, False, "0x") for call in calls})
    for call in calls:
        if call.tag in {
            "ethena_arm:token0()",
            "ethena_arm:token1()",
            "ethena_arm:traderate0()",
            "ethena_arm:traderate1()",
            "ethena_arm:getReserves()",
        }:
            legacy.results[call] = _result(call)
    assert probe.observed_epoch(legacy, calls) == "legacy"

    modern = Snapshot({call: _result(call, False, "0x") for call in calls})
    for call in calls:
        if call.tag in {
            "ethena_arm:getBaseAssets()",
            "ethena_arm:baseAssetConfigs(address)",
            "ethena_arm:getReserves(address)",
        }:
            modern.results[call] = _result(call)
    assert probe.observed_epoch(modern, calls) == "multi_asset"


def test_nonzero_multi_asset_market_adds_max_withdraw_dependency() -> None:
    active = probe.spec(probe.ARM, "activeMarket()")
    raw = "0x" + "00" * 12 + "0dc20109ea012f050beda184844c1ed5ec6da33a"
    snapshot = Snapshot({active: _result(active, raw=raw)})
    dependent = probe.stage_three(snapshot, [active], "multi_asset")
    assert len(dependent) == 1
    assert dependent[0].to == "0x0dc20109ea012f050beda184844c1ed5ec6da33a"
    assert dependent[0].tag == "ethena_arm:maxWithdraw(address)"
