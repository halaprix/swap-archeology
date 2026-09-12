"""Acquire LitePSM at historical pins and qualify its source-derived quote model.

There is no on-chain quoter. Independent expressions below use Fraction and
explicit source fees; raw state, boundary checks, and source identity are saved.
"""

import hashlib
import json
import os
import sys
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from swaparch.adapters.litepsm import LitePsmAdapter
from swaparch.core.protocols import Unsupported
from swaparch.rpc.client import RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import acquire, load_inventory, observe_singleton_activation

ROOT = Path(__file__).resolve().parents[1]
PSM = "0xf6e72db5454dd049d0788e411b06cfaf16853042"
WAD = 10**18
SOURCE = Path(
    os.environ.get("SWAPARCH_LITEPSM_SOURCE", ROOT / "data/protocol-sources/litepsm/DssLitePsm.sol")
)
SOURCE_HASH = "502eed38778ac29758959cadbb3d2f36aa3af21144e7374483795581a6279ce8"


def main(number):
    if not SOURCE.is_file():
        raise FileNotFoundError(
            f"LitePSM canonical source is missing: {SOURCE}. "
            "Set SWAPARCH_LITEPSM_SOURCE to DssLitePsm.sol."
        )
    raw_source = SOURCE.read_bytes()
    if hashlib.sha256(raw_source).hexdigest() != SOURCE_HASH:
        raise ValueError("canonical source changed since arithmetic trace")
    source_copy = ROOT / "data/protocol-sources/litepsm/DssLitePsm.sol"
    if SOURCE != source_copy:
        source_copy.parent.mkdir(parents=True, exist_ok=True)
        source_copy.write_bytes(raw_source)
    client, adapter = RpcClient(), LitePsmAdapter()
    block = client.get_block(number)
    record = next(r for r in load_inventory("maker_sky_psm") if r.pool == PSM)
    record = observe_singleton_activation(record, block, client)
    if record is None:
        print(
            json.dumps(
                {
                    "block": number,
                    "reason": "LitePSM unavailable at block",
                    "network_requests": client.network_requests,
                }
            )
        )
        return 0
    snapshot, _acquisition = acquire(
        {adapter.family: adapter}, [record], block, SnapshotStore(), client
    )
    state = adapter.load_state(record, snapshot)
    static = adapter.read_requests(record, block)
    # Dependent calls are already cached after acquire(), so enumerate explicitly.
    calls = static + [
        adapter.balance_spec(state.dai.address, PSM, "dai_balance"),
        adapter.balance_spec(state.gem.address, record.config["pocket"], "pocket_balance"),
        adapter.allowance_spec(state.gem.address, record.config["pocket"], PSM, "pocket_allowance"),
    ]
    values = [asdict(snapshot.get(s)) for s in calls]
    checks = []
    if state.tin != 0 or state.tout != 0:
        raise ValueError("five-pin qualification boundary sizes below require observed zero fees")
    for token_in, token_out in ((state.gem, state.dai), (state.dai, state.gem)):
        for units in (1, 100, 1_000_000):
            amount = units * 10**token_in.decimals
            gross = Fraction(amount, 10**token_in.decimals) * 10**token_out.decimals
            if token_in == state.gem:
                expected = int(gross) - int(gross * Fraction(state.tin, WAD))
            else:
                expected = int(gross / (1 + Fraction(state.tout, WAD)))
            actual, after = state.swap(token_in.address, token_out.address, amount)
            expected_dai = state.dai_buffer + (amount if token_in == state.dai else -expected)
            expected_gem = state.pocket_gem + (amount if token_in == state.gem else -expected)
            expected_allowance = state.pocket_gem_allowance - (
                expected if token_in == state.dai else 0
            )
            checks.append(
                {
                    "direction": f"{token_in.symbol}->{token_out.symbol}",
                    "amount_in": amount,
                    "expected_out": expected,
                    "actual_out": actual,
                    "matched": (
                        actual == expected
                        and after.dai_buffer == expected_dai
                        and after.pocket_gem == expected_gem
                        and after.pocket_gem_allowance == expected_allowance
                    ),
                }
            )
    # Explicitly check both directional hard limits and the exact-input residual.
    cases = [
        (state.gem, state.dai, state.dai_buffer // state.to18_conversion_factor + 1, "DAI buffer"),
        (
            state.dai,
            state.gem,
            (min(state.pocket_gem, state.pocket_gem_allowance) + 1) * state.to18_conversion_factor,
            "pocket balance or allowance",
        ),
        (state.dai, state.gem, 10**18 + 1, "unattainable input"),
    ]
    for token_in, token_out, amount, name in cases:
        try:
            state.swap(token_in.address, token_out.address, amount)
            reason = None
        except Unsupported as exc:
            reason = str(exc)
        expected_reason = {
            "DAI buffer": "but buffer has",
            "pocket balance or allowance": "buyGem needs",
            "unattainable input": "leaving 1 unspent",
        }[name]
        checks.append(
            {
                "boundary": name,
                "amount_in": amount,
                "matched": reason is not None and expected_reason in reason,
                "reason": reason,
            }
        )
    path = ROOT / f"data/validation/litepsm/{block.hash}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "block": asdict(block),
                "pool": PSM,
                "calls": values,
                "source": {
                    "commit": "dbf0022225f645f5697e5517d0cf00810471bccf",
                    "sha256": SOURCE_HASH,
                    "path": str(source_copy.relative_to(ROOT)),
                },
                "checks": checks,
                "scope": "Canonical source arithmetic on pinned inventory; no settlement/transfer validation or on-chain quoter.",
                "network_requests": client.network_requests,
            },
            indent=2,
        )
        + "\n"
    )
    supported = all(c["matched"] for c in checks)
    inventory_path = ROOT / "data/discovery/1/maker_sky_psm.json"
    inventory = json.loads(inventory_path.read_text())
    row = next(r for r in inventory["pools"] if r["pool"] == PSM)
    row["discovered_by"] = dict(record.discovered_by)
    hashes = set(row["config"].get("validated_block_hashes", []))
    hashes.discard(block.hash)
    if supported:
        hashes.add(block.hash)
    row["config"]["validated_block_hashes"] = sorted(hashes)
    row["status"] = "supported" if hashes else "discovered_unsupported"
    row["discovered_by"]["deployed_by_block"] = min(
        number, row["discovered_by"].get("deployed_by_block", number)
    )
    row["discovered_by"]["evidence"] = sorted(
        set(row["discovered_by"]["evidence"] + [str(path.relative_to(ROOT))])
    )
    row["notes"] = (
        "Finite DAI/pocketUSDC/allowance source model; DAI exact inputs must have a buyGem preimage. Per-hash qualification under data/validation/litepsm; no settlement claim."
    )
    row["config"]["directions"][0]["rate"] = (
        "gross = gemAmt * factor; daiOut = gross - floor(gross * tin / 1e18)"
    )
    inventory["status"] = "supported" if hashes else "discovered_unsupported"
    inventory_path.write_text(json.dumps(inventory, indent=1) + "\n")
    print(
        json.dumps(
            {
                "block": number,
                "checks": len(checks),
                "matched": sum(c["matched"] for c in checks),
                "network_requests": client.network_requests,
                "supported": supported,
                "evidence": str(path),
            }
        ),
        flush=True,
    )
    return int(not supported)


if __name__ == "__main__":
    raise SystemExit(max(main(int(n)) for n in (sys.argv[1:] or ["25896003"])))
