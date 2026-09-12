"""Historical offline test suite for PancakeSwap V3 adapter.

Replays snapshot fixtures from outputs/source-expansion/pancake/qualification-evidence.json
completely offline (zero network RPC calls).
Validates:
- Exact bit-for-bit quote match with QuoterV2 on liquid pools (WETH/USDC 500, WETH/USDT 500, WETH/WBTC 2500).
- Strict refusal with Unsupported on thin/depleted pools where QuoterV2 gave deceptive partial fills.
- State loading and provenance tracking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swaparch.adapters.pancake_v3 import PANCAKE_FACTORY, PancakeV3Adapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import BlockRef, CallResult, CallSpec, PoolRecord, SupportStatus, Token

EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "source-expansion"
    / "pancake"
    / "qualification-evidence.json"
)

DISCOVERY_PATH = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "source-expansion"
    / "pancake"
    / "discovery.json"
)

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class OfflineSnapshot:
    """Mock snapshot answering from saved qualification evidence."""

    def __init__(self, block: BlockRef, calls_data: list[dict]) -> None:
        self.block = block
        self._map: dict[tuple[str, str], CallResult] = {}
        for c in calls_data:
            spec = CallSpec(to=c["to"], data=c["data"], tag=c.get("tag", ""))
            self._map[(c["to"].lower(), c["data"].lower())] = CallResult(
                spec=spec,
                success=c["success"],
                raw=c["raw"],
                via="offline_evidence",
            )

    def get(self, spec: CallSpec) -> CallResult:
        key = (spec.to.lower(), spec.data.lower())
        if key not in self._map:
            raise KeyError(f"Offline call missing: to={spec.to} data={spec.data} tag={spec.tag}")
        return self._map[key]

    def has(self, spec: CallSpec) -> bool:
        return (spec.to.lower(), spec.data.lower()) in self._map


@pytest.fixture(scope="module")
def evidence_data() -> dict:
    assert EVIDENCE_PATH.exists(), f"Evidence file not found at {EVIDENCE_PATH}"
    return json.loads(EVIDENCE_PATH.read_text())


@pytest.fixture(scope="module")
def pool_records() -> dict[str, PoolRecord]:
    assert DISCOVERY_PATH.exists(), f"Discovery file not found at {DISCOVERY_PATH}"
    disc = json.loads(DISCOVERY_PATH.read_text())
    records = {}
    for r in disc["records"]:
        pr = r["pool_record"]
        tokens = tuple(Token(**t) for t in pr["tokens"])
        rec = PoolRecord(
            family=pr["family"],
            chain=pr["chain"],
            pool_id=pr["pool_id"],
            deployment=pr["deployment"],
            pool=pr["pool"],
            tokens=tokens,
            config=pr["config"],
            created_block=pr["created_block"],
            discovered_by=pr["discovered_by"],
            status=SupportStatus(pr["status"]),
            notes=pr.get("notes", ""),
        )
        records[pr["pool"].lower()] = rec
    return records


def test_qualification_evidence_structure(evidence_data: dict) -> None:
    assert evidence_data["factory"].lower() == PANCAKE_FACTORY.lower()
    assert evidence_data["pins"] == [23549939, 23550094, 23550192]
    assert len(evidence_data["results"]) == 3


@pytest.mark.parametrize("pin_index", [0, 1, 2])
def test_historical_pins_quote_exact_matches(
    evidence_data: dict, pool_records: dict[str, PoolRecord], pin_index: int
) -> None:
    pin_data = evidence_data["results"][pin_index]
    block_info = pin_data["block"]
    block = BlockRef(
        chain=1,
        number=block_info["number"],
        hash=block_info["hash"],
        timestamp=block_info.get("timestamp", 0),
    )
    adapter = PancakeV3Adapter(word_radius=8)

    for pool_data in pin_data["pools"]:
        pool_addr = pool_data["pool"].lower()
        pool_record = pool_records[pool_addr]
        snapshot = OfflineSnapshot(block, pool_data["snapshot_calls"])

        state = adapter.load_state(pool_record, snapshot)

        # Verify decoded slot0 fields
        assert state.sqrt_price_x96 == pool_data["slot0"]["sqrt_price_x96"]
        assert state.tick == pool_data["slot0"]["tick"]
        assert state.liquidity == pool_data["slot0"]["liquidity"]
        assert state.fee_protocol == pool_data["slot0"]["fee_protocol"]

        # Verify provenance
        prov = adapter.provenance(pool_record, snapshot)
        assert prov["pool"].lower() == pool_addr
        assert prov["fee_protocol"] == pool_data["slot0"]["fee_protocol"]
        assert (
            prov["initialized_ticks_loaded"] == pool_data["provenance"]["initialized_ticks_loaded"]
        )

        # Determine sell direction (selling WETH)
        token_in = WETH
        token_out = (
            state.token0.address
            if state.token1.address.lower() == WETH.lower()
            else state.token1.address
        )

        # Verify each comparison against QuoterV2
        for comp in pool_data["comparisons"]:
            amount_in = comp["amount_in"]
            quoter_out = comp["quoter_out"]
            local_out = comp["local_out"]
            is_match = comp["match"]
            partial_detected = comp["partial_fill_detected"]

            if is_match:
                # Quoter and local state must agree exactly
                actual_out = state.quote_exact_in(token_in, token_out, amount_in)
                assert actual_out == quoter_out
                assert actual_out == local_out
                assert actual_out > 0
            elif partial_detected:
                # Local state must strictly refuse partial fills / unconsumed amounts / out of bounds
                with pytest.raises(Unsupported) as exc_info:
                    state.quote_exact_in(token_in, token_out, amount_in)
                assert str(exc_info.value) == comp["local_error"]


def test_independent_replay_aggregate_summary(
    evidence_data: dict, pool_records: dict[str, PoolRecord]
) -> None:
    """Independently counts and verifies all 27 comparison points across 3 blocks.

    Ensures no test passes solely by inspecting a stored boolean:
    - 21 exact bit-for-bit matches against QuoterV2.
    - 6 strict Unsupported refusals (preventing deceptive partial fills / out-of-bounds).
    """
    total_comps = 0
    total_matches = 0
    total_refusals = 0

    adapter = PancakeV3Adapter(word_radius=8)

    for pin_data in evidence_data["results"]:
        block_info = pin_data["block"]
        block = BlockRef(
            chain=1,
            number=block_info["number"],
            hash=block_info["hash"],
            timestamp=block_info.get("timestamp", 0),
        )
        for pool_data in pin_data["pools"]:
            pool_record = pool_records[pool_data["pool"].lower()]
            snapshot = OfflineSnapshot(block, pool_data["snapshot_calls"])
            state = adapter.load_state(pool_record, snapshot)
            token_out = (
                state.token0.address
                if state.token1.address.lower() == WETH.lower()
                else state.token1.address
            )

            for comp in pool_data["comparisons"]:
                total_comps += 1
                amount_in = comp["amount_in"]
                quoter_out = comp["quoter_out"]

                if comp["match"]:
                    total_matches += 1
                    actual = state.quote_exact_in(WETH, token_out, amount_in)
                    assert actual == quoter_out, f"Mismatch at block {block.number} size {comp['size_weth']}"
                elif comp["partial_fill_detected"]:
                    total_refusals += 1
                    with pytest.raises(Unsupported):
                        state.quote_exact_in(WETH, token_out, amount_in)

    assert total_comps == 27
    assert total_matches == 21
    assert total_refusals == 6

