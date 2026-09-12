"""Stage pinned Ethena ARM ABI reads without admitting an adapter.

The proxy was created at block 23924639.  Earlier requested blocks are
recorded as not deployed without making an RPC call.  For deployed blocks the
first stage records mutually incompatible legacy and multi-asset ABI probes;
the second stage follows only the observed shape.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.core.types import CallResult, CallSpec
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore, StoredSnapshot

ROOT = Path(__file__).resolve().parents[1]
ARM = "0xceda2d856238aa0d12f6329de20b9115f07c366d"
USDE = "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"
SUSDE = "0x9d39a5de30e57443bff2a8307a4256c8797a3497"
CREATED_BLOCK = 23_924_639
LADDER = (1, 10**18, 100 * 10**18)

# These deployment manifests map the observed implementation addresses to source
# objects. A successful proxy implementation() read remains mandatory; neither
# source digest establishes deployed-runtime bytecode equivalence on its own.
SOURCE_OBSERVATIONS = {
    "legacy_deployment_record": {
        "commit": "bd5f945c769244ae6f8beac3726f015e7c752e55",
        "implementation": "0x11e6bee1662a2e2b20a7163cc33334fa1cf19979",
        "files_sha256": {
            "src/contracts/EthenaARM.sol": "797d33c87351e1fec5c2d5e063d4ec767f8a8e6aa3898663c9846658f311b547",
            "src/contracts/AbstractARM.sol": "2f5c0b102c994a951fc0ac5b78b2b5df04fa6bcde61a1196aee933ad688ca23e",
        },
        "shape": "token0/token1/traderate0/traderate1/getReserves() plus sUSDe ERC-4626 conversion",
    },
    "multi_asset_deployment_record": {
        "commit": "b6cbc6aa114a657cc54f29741f7fc3de31a26a53",
        "implementation": "0xebb2b66759b593ea50eb8c306e2e13464cdb99fe",
        "files_sha256": {
            "src/contracts/EthenaARM.sol": "a3dea2686d704177b9f0840ce601f36a169ce4af8215da89963d3b331507164b",
            "src/contracts/AbstractARM.sol": "18ddb3bbc33ee17c6348b6023574fe355b29632ebb1f432c2a7c1d38bf7f1a6a",
            "src/contracts/adapters/EthenaAssetAdapter.sol": "93d5994d4d75507085193f7ad74f92b8a00f0f67c333f16a862082121bb0a1e5",
        },
        "shape": "baseAssetConfigs(sUSDe)/getReserves(sUSDe) with adapter conversion",
    },
}


def spec(target: str, signature: str, types: tuple[str, ...] = (), args: tuple[Any, ...] = ()) -> CallSpec:
    return CallSpec(
        target,
        "0x" + keccak(text=signature)[:4].hex() + encode(types, args).hex(),
        f"ethena_arm:{signature}",
    )


def usable(result: CallResult) -> bool:
    return result.success and result.raw != "0x"


def decode_address(result: CallResult) -> str:
    return str(decode(["address"], bytes.fromhex(result.raw[2:]))[0]).lower()


def stage_one() -> list[CallSpec]:
    return [
        spec(ARM, "implementation()"),
        spec(ARM, "liquidityAsset()"),
        spec(ARM, "token0()"),
        spec(ARM, "token1()"),
        spec(ARM, "traderate0()"),
        spec(ARM, "traderate1()"),
        spec(ARM, "getReserves()"),
        spec(ARM, "getBaseAssets()"),
        spec(ARM, "baseAssetConfigs(address)", ("address",), (SUSDE,)),
        spec(ARM, "getReserves(address)", ("address",), (SUSDE,)),
        spec(ARM, "paused()"),
    ]


def observed_epoch(snapshot: StoredSnapshot, calls: list[CallSpec]) -> str:
    by_tag = {call.tag: snapshot.get(call) for call in calls}
    legacy = all(usable(by_tag[f"ethena_arm:{signature}"]) for signature in (
        "token0()", "token1()", "traderate0()", "traderate1()", "getReserves()",
    ))
    modern = all(usable(by_tag[f"ethena_arm:{signature}"]) for signature in (
        "getBaseAssets()", "baseAssetConfigs(address)", "getReserves(address)",
    ))
    if legacy == modern:
        return "ambiguous_or_unsupported"
    return "legacy" if legacy else "multi_asset"


def stage_two(snapshot: StoredSnapshot, calls: list[CallSpec], epoch: str) -> list[CallSpec]:
    by_tag = {call.tag: snapshot.get(call) for call in calls}
    common = [
        spec(USDE, "balanceOf(address)", ("address",), (ARM,)),
        spec(SUSDE, "balanceOf(address)", ("address",), (ARM,)),
    ]
    if epoch == "legacy":
        return common + [
            spec(ARM, "withdrawsQueued()"),
            spec(ARM, "withdrawsClaimed()"),
            spec(ARM, "liquidityAmountInCooldown()"),
            spec(ARM, "activeMarket()"),
            *[
                spec(SUSDE, "convertToAssets(uint256)", ("uint256",), (amount,))
                for amount in LADDER
            ],
            *[
                spec(SUSDE, "convertToShares(uint256)", ("uint256",), (amount,))
                for amount in LADDER
            ],
        ]
    if epoch == "multi_asset":
        config = by_tag["ethena_arm:baseAssetConfigs(address)"]
        fields = decode(
            ["uint128", "uint128", "uint128", "uint128", "uint128", "uint120", "bool", "address"],
            bytes.fromhex(config.raw[2:]),
        )
        adapter = str(fields[-1]).lower()
        return common + [
            spec(ARM, "reservedWithdrawLiquidity()"),
            spec(ARM, "activeMarket()"),
            spec(adapter, "asset()"),
            *[
                spec(adapter, "convertToAssets(uint256)", ("uint256",), (amount,))
                for amount in LADDER
            ],
            *[
                spec(adapter, "convertToShares(uint256)", ("uint256",), (amount,))
                for amount in LADDER
            ],
        ]
    return []


def stage_three(snapshot: StoredSnapshot, calls: list[CallSpec], epoch: str) -> list[CallSpec]:
    """Read the market's source-used withdrawal limit after its address is known."""

    if epoch != "multi_asset":
        return []
    active = next(call for call in calls if call.tag == "ethena_arm:activeMarket()")
    result = snapshot.get(active)
    if not usable(result):
        return []
    market = decode_address(result)
    if market == "0x0000000000000000000000000000000000000000":
        return []
    return [spec(market, "maxWithdraw(address)", ("address",), (ARM,))]


def probe(number: int) -> dict[str, Any]:
    if number < CREATED_BLOCK:
        return {
            "number": number,
            "status": "not_deployed_before_creation",
            "created_block": CREATED_BLOCK,
            "network_requests": 0,
        }
    client = RpcClient()
    block = client.get_block(number)
    store = SnapshotStore()
    first_calls = stage_one()
    snapshot = store.extend(block, first_calls, client)
    implementation = snapshot.get(first_calls[0])
    if not usable(implementation):
        raise ValueError("Ethena ARM implementation() is unavailable at deployed block")
    epoch = observed_epoch(snapshot, first_calls)
    second_calls = stage_two(snapshot, first_calls, epoch)
    if second_calls:
        snapshot = store.extend(block, second_calls, client)
    third_calls = stage_three(snapshot, second_calls, epoch)
    if third_calls:
        snapshot = store.extend(block, third_calls, client)
    payload = {
        "block": asdict(block),
        "proxy": ARM,
        "created_block": CREATED_BLOCK,
        "implementation": decode_address(implementation),
        "observed_epoch": epoch,
        "source_observations": SOURCE_OBSERVATIONS,
        "stage_one": [asdict(snapshot.get(call)) for call in first_calls],
        "stage_two": [asdict(snapshot.get(call)) for call in second_calls],
        "stage_three": [asdict(snapshot.get(call)) for call in third_calls],
        "network_requests": client.network_requests,
        "scope": "ABI and source-shape evidence only; no adapter admission or quote verdict",
    }
    output = ROOT / "data/discovery-evidence/ethena-arm" / f"{block.hash}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")
    return {"number": number, "hash": block.hash, "epoch": epoch, "implementation": payload["implementation"],
            "network_requests": client.network_requests, "evidence": str(output)}


if __name__ == "__main__":
    for argument in sys.argv[1:] or ["24356381", "25896003"]:
        print(json.dumps(probe(int(argument))), flush=True)
