"""Focused unit tests for USDS connectors: DaiUsds converter and UsdsPsmWrapper.

Covers:
1. Exact integer 1:1 arithmetic, zero fee, perfect input/output conservation.
2. Boundary checks, zero input, domain limits, gating (caged / unauthorized wards).
3. Semantic and arithmetic equivalence between:
   - Direct wrapper: USDS <-> USDC
   - Graph edge: USDS <-> DAI (via DaiUsds) <-> USDC (via LitePSM)
4. Shared capacity preservation: wrapper shares identical capacity IDs with LitePSM;
   evaluator rejects conflicting separate pools but cleanly evaluates the composed graph edge.
5. Unattainable exact input (sub-micro preimage requirement) and terminal refund under
   Evaluator's allow_psm_dai_refund policy.
6. Adapter read specifications, dependent requests, and state decoding.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from swaparch.adapters.litepsm import LitePsmState
from swaparch.adapters.usds import (
    CONVERTER,
    DAI,
    LITE_PSM,
    MAX_CONVERSION_WAD,
    MAX_UINT256,
    POCKET,
    USDC,
    USDS,
    WRAPPER,
    DaiUsdsAdapter,
    DaiUsdsState,
    UsdsConnectorAdapter,
    UsdsPsmWrapperAdapter,
    UsdsPsmWrapperState,
)
from swaparch.core.protocols import Unsupported
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    Plan,
    PoolRecord,
    Step,
    SupportStatus,
    Token,
    TradeRequest,
    norm_address,
)
from swaparch.evaluator.evaluate import Evaluator

DAI_TOKEN = Token(1, DAI, "DAI", 18)
USDS_TOKEN = Token(1, USDS, "USDS", 18)
USDC_TOKEN = Token(1, USDC, "USDC", 6)


def make_dummy_converter_record() -> PoolRecord:
    return PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id=f"maker_sky_psm:{CONVERTER}:{CONVERTER}",
        deployment=CONVERTER,
        pool=CONVERTER,
        tokens=(DAI_TOKEN, USDS_TOKEN),
        config={"kind": "converter", "model": "DaiUsdsConverter"},
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.SUPPORTED,
    )


def make_dummy_wrapper_record() -> PoolRecord:
    return PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id=f"maker_sky_psm:{WRAPPER}:{WRAPPER}",
        deployment=WRAPPER,
        pool=WRAPPER,
        tokens=(USDS_TOKEN, USDC_TOKEN),
        config={
            "kind": "psm_wrapper",
            "model": "UsdsPsmWrapper",
            "wraps": LITE_PSM,
            "to18ConversionFactor": 10**12,
        },
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.SUPPORTED,
    )


def make_dummy_litepsm_record() -> PoolRecord:
    return PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id=f"maker_sky_psm:{LITE_PSM}:{LITE_PSM}",
        deployment=LITE_PSM,
        pool=LITE_PSM,
        tokens=(DAI_TOKEN, USDC_TOKEN),
        config={
            "kind": "psm",
            "model": "dss-lite-psm",
            "pocket": POCKET,
            "to18ConversionFactor": 10**12,
        },
        created_block=None,
        discovered_by={"method": "curated"},
        status=SupportStatus.SUPPORTED,
    )


def make_sample_conv_state(live: int = 1, usds_wards: int = 1, dai_wards: int = 1) -> DaiUsdsState:
    return DaiUsdsState(
        record=make_dummy_converter_record(),
        dai=DAI_TOKEN,
        usds=USDS_TOKEN,
        dai_join="0x9759a6ac90977b93b58547b4a71c78317f391a28",
        usds_join="0x3c0f895007ca717aa01c8693e59df1e8c3777feb",
        live=live,
        usds_wards=usds_wards,
        dai_wards=dai_wards,
    )


def make_sample_lite_state(
    dai_buffer: int = 800_000_000 * 10**18,
    pocket_gem: int = 3_000_000_000 * 10**6,
    pocket_gem_allowance: int = 3_000_000_000 * 10**6,
    tin: int = 0,
    tout: int = 0,
) -> LitePsmState:
    return LitePsmState(
        record=make_dummy_litepsm_record(),
        dai=DAI_TOKEN,
        gem=USDC_TOKEN,
        to18_conversion_factor=10**12,
        tin=tin,
        tout=tout,
        dai_buffer=dai_buffer,
        pocket_gem=pocket_gem,
        pocket_gem_allowance=pocket_gem_allowance,
    )


def make_sample_wrap_state(
    dai_buffer: int = 800_000_000 * 10**18,
    pocket_gem: int = 3_000_000_000 * 10**6,
    pocket_gem_allowance: int = 3_000_000_000 * 10**6,
    tin: int = 0,
    tout: int = 0,
) -> UsdsPsmWrapperState:
    return UsdsPsmWrapperState(
        record=make_dummy_wrapper_record(),
        usds=USDS_TOKEN,
        gem=USDC_TOKEN,
        psm=LITE_PSM,
        to18_conversion_factor=10**12,
        tin=tin,
        tout=tout,
        dai_buffer=dai_buffer,
        pocket_gem=pocket_gem,
        pocket_gem_allowance=pocket_gem_allowance,
    )


# ===========================================================================
# 1. DaiUsdsConverter exact arithmetic and gating tests
# ===========================================================================

def test_converter_exact_1_to_1_conservation():
    state = make_sample_conv_state()
    for amount in (1, 10, 100, 1_000_000, 10**18, 10**24):
        # DAI -> USDS
        out_usds, next_state = state.swap(DAI, USDS, amount)
        assert out_usds == amount
        assert next_state == state

        # USDS -> DAI
        out_dai, next_state2 = state.swap(USDS, DAI, amount)
        assert out_dai == amount
        assert next_state2 == state

        assert state.quote_exact_in(DAI, USDS, amount) == amount
        assert state.quote_exact_in(USDS, DAI, amount) == amount


def test_converter_zero_amount():
    state = make_sample_conv_state()
    out, next_state = state.swap(DAI, USDS, 0)
    assert out == 0
    assert next_state == state


def test_converter_domain_and_gating():
    # Negative input
    state = make_sample_conv_state()
    with pytest.raises(Unsupported, match="must be a uint256"):
        state.swap(DAI, USDS, -1)

    # Overflow uint256
    with pytest.raises(Unsupported, match="must be a uint256"):
        state.swap(DAI, USDS, 2**256)

    # Arithmetic limit: wad * RAY (10**27) must fit in uint256
    out_max, _ = state.swap(DAI, USDS, MAX_CONVERSION_WAD)
    assert out_max == MAX_CONVERSION_WAD
    with pytest.raises(Unsupported, match="exceeds maximum conversion limit"):
        state.swap(DAI, USDS, MAX_CONVERSION_WAD + 1)
    with pytest.raises(Unsupported, match="exceeds maximum conversion limit"):
        state.swap(DAI, USDS, MAX_UINT256)

    # Canonical direction-specific gating:
    # Caged / not live:
    # In canonical Maker DSS, daiJoin.join does NOT check live == 1, but daiJoin.exit DOES.
    caged_state = make_sample_conv_state(live=0)
    # DAI -> USDS succeeds under caged daiJoin
    out_caged, _ = caged_state.swap(DAI, USDS, 10**18)
    assert out_caged == 10**18
    # USDS -> DAI fails when daiJoin is caged
    with pytest.raises(Unsupported, match="daiJoin is not live / caged"):
        caged_state.swap(USDS, DAI, 10**18)

    # Unauthorized usdsJoin (usds.wards != 1):
    # Only daiToUsds needs USDS mint authority (calls usdsJoin.exit -> usds.mint).
    unauth_usds = make_sample_conv_state(usds_wards=0)
    with pytest.raises(Unsupported, match="lacks USDS mint authority"):
        unauth_usds.swap(DAI, USDS, 10**18)
    # usdsToDai burns USDS via usdsJoin.join, does not require usds.wards
    out_usds_unauth, _ = unauth_usds.swap(USDS, DAI, 10**18)
    assert out_usds_unauth == 10**18

    # Unauthorized daiJoin (dai.wards != 1):
    # Only usdsToDai needs DAI mint authority (calls daiJoin.exit -> dai.mint).
    unauth_dai = make_sample_conv_state(dai_wards=0)
    with pytest.raises(Unsupported, match="lacks DAI mint authority"):
        unauth_dai.swap(USDS, DAI, 10**18)
    # daiToUsds burns DAI via daiJoin.join, does not require dai.wards
    out_dai_unauth, _ = unauth_dai.swap(DAI, USDS, 10**18)
    assert out_dai_unauth == 10**18

    # Unsupported pair
    with pytest.raises(Unsupported, match="is not DaiUsds"):
        state.swap(DAI, USDC, 10**18)


# ===========================================================================
# 2. Wrapper vs Composed Graph Edge Equivalence
# ===========================================================================

def test_wrapper_sell_gem_equivalence():
    """Verify USDC -> USDS via Wrapper vs USDC -> DAI (LitePSM) -> USDS (Converter)."""
    conv_state = make_sample_conv_state()
    lite_state = make_sample_lite_state()
    wrap_state = make_sample_wrap_state()

    for usdc_units in (1, 100, 10_000, 1_000_000):
        usdc_in = usdc_units * 10**6

        # 1. Direct wrapper
        usds_wrap_out, wrap_after = wrap_state.swap(USDC, USDS, usdc_in)

        # 2. Graph edge: LitePSM then Converter
        dai_lite_out, lite_after = lite_state.swap(USDC, DAI, usdc_in)
        usds_conv_out, _ = conv_state.swap(DAI, USDS, dai_lite_out)

        assert usds_wrap_out == usds_conv_out == dai_lite_out
        assert wrap_after.dai_buffer == lite_after.dai_buffer
        assert wrap_after.pocket_gem == lite_after.pocket_gem


def test_wrapper_buy_gem_equivalence():
    """Verify USDS -> USDC via Wrapper vs USDS -> DAI (Converter) -> USDC (LitePSM)."""
    conv_state = make_sample_conv_state()
    lite_state = make_sample_lite_state()
    wrap_state = make_sample_wrap_state()

    for usdc_units in (1, 100, 10_000, 1_000_000):
        usdc_wanted = usdc_units * 10**6
        # Preimage USDS in (with tout = 0):
        usds_in = usdc_wanted * 10**12

        # 1. Direct wrapper
        usdc_wrap_out, wrap_after = wrap_state.swap(USDS, USDC, usds_in)

        # 2. Graph edge: Converter then LitePSM
        dai_conv_out, _ = conv_state.swap(USDS, DAI, usds_in)
        usdc_lite_out, lite_after = lite_state.swap(DAI, USDC, dai_conv_out)

        assert usdc_wrap_out == usdc_lite_out == usdc_wanted
        assert wrap_after.dai_buffer == lite_after.dai_buffer
        assert wrap_after.pocket_gem == lite_after.pocket_gem
        assert wrap_after.pocket_gem_allowance == lite_after.pocket_gem_allowance


# ===========================================================================
# 3. Shared Capacity Preservation & Avoidance of Liquidity Duplication
# ===========================================================================

def test_wrapper_advertises_litepsm_capacity_ids():
    """CRITICAL: Wrapper must advertise LitePSM capacity IDs, not an independent reserve."""
    wrap_state = make_sample_wrap_state()
    lite_state = make_sample_lite_state()

    assert wrap_state.capacity_ids() == (
        f"maker_sky_psm:{norm_address(LITE_PSM)}:dai_buffer",
        f"maker_sky_psm:{norm_address(LITE_PSM)}:pocket_usdc",
    )
    assert wrap_state.capacity_ids() == lite_state.capacity_ids()


def test_evaluator_rejects_plan_using_both_wrapper_and_litepsm_as_separate_pools():
    """Evaluator._shared_capacity_reason correctly rejects plans with two separate pools claiming LitePSM capacity."""
    wrap_state = make_sample_wrap_state()
    lite_state = make_sample_lite_state()

    evaluator = Evaluator()
    request = TradeRequest(DAI_TOKEN, USDC_TOKEN, 1000 * 10**18)
    # Plan with 2 steps referencing different pool_ids that share capacity IDs:
    plan = Plan(
        request=request,
        steps=(
            Step(lite_state.record.pool_id, DAI, USDC, 500 * 10**18),
            Step(wrap_state.record.pool_id, USDS, USDC, 500 * 10**18),
        ),
        solver="test",
    )

    ev = evaluator.evaluate(plan, [lite_state, wrap_state])
    assert not ev.feasible
    assert "shared capacity not modelled" in ev.reasons


def test_evaluator_cleanly_evaluates_graph_edge_through_dai_usds_into_litepsm():
    """Routing through DaiUsds connector into LitePSM updates the single LitePSM state cleanly."""
    conv_state = make_sample_conv_state()
    lite_state = make_sample_lite_state()

    evaluator = Evaluator()
    usds_in = 1000 * 10**18
    request = TradeRequest(USDS_TOKEN, USDC_TOKEN, usds_in)
    plan = Plan(
        request=request,
        steps=(
            Step(conv_state.record.pool_id, USDS, DAI, usds_in),
            Step(lite_state.record.pool_id, DAI, USDC, usds_in),
        ),
        solver="test_graph",
    )

    ev = evaluator.evaluate(plan, [conv_state, lite_state])
    assert ev.feasible
    assert ev.amount_in_spent == usds_in
    assert ev.residual_in == 0
    assert ev.amount_out == 1000 * 10**6
    assert len(ev.steps) == 2
    assert ev.steps[0].amount_out == usds_in
    assert ev.steps[1].amount_out == 1000 * 10**6


def test_evaluator_sub_micro_dust_refund_via_graph_edge():
    """A sub-micro USDS remainder (e.g. 1000.0000005 USDS) converts 1:1 to DAI, and LitePSM leaves 0.5 microDAI refund."""
    conv_state = make_sample_conv_state()
    lite_state = make_sample_lite_state()

    dust = 500_000_000_000  # 0.5 * 10^12 wei (0.5 micro-DAI)
    spent_usds = 1000 * 10**18
    total_usds_in = spent_usds + dust

    evaluator = Evaluator()
    request = TradeRequest(USDS_TOKEN, USDC_TOKEN, total_usds_in, allow_psm_dai_refund=True)
    plan = Plan(
        request=request,
        steps=(
            Step(conv_state.record.pool_id, USDS, DAI, total_usds_in),
            Step(lite_state.record.pool_id, DAI, USDC, spent_usds),
        ),
        solver="test_dust",
    )

    ev = evaluator.evaluate(plan, [conv_state, lite_state])
    assert ev.feasible
    assert ev.amount_out == 1000 * 10**6
    assert ev.residual_in == 0
    assert ev.terminal_refund == {DAI: dust}


def test_wrapper_capacity_exhaustion():
    wrap_state = make_sample_wrap_state(
        dai_buffer=100 * 10**18,
        pocket_gem=50 * 10**6,
        pocket_gem_allowance=50 * 10**6,
    )

    # sellGem exceeding dai_buffer
    with pytest.raises(Unsupported, match="buffer but has"):
        wrap_state.swap(USDC, USDS, 101 * 10**6)

    # buyGem exceeding pocket_gem
    with pytest.raises(Unsupported, match="buyGem needs"):
        wrap_state.swap(USDS, USDC, 51 * 10**18)


# ===========================================================================
# 4. Adapter Read Specifications and State Loading
# ===========================================================================

def test_dai_usds_adapter_specs_and_loading():
    adapter = DaiUsdsAdapter()
    pool_rec = make_dummy_converter_record()
    block = BlockRef(1, 23549939, "0x" + "a" * 64, 1760130851)

    specs = adapter.read_requests(pool_rec, block)
    assert len(specs) == 4
    tags = [s.tag for s in specs]
    assert "usds_conv:daiJoin" in tags
    assert "usds_conv:usdsJoin" in tags
    assert "usds_conv:dai" in tags
    assert "usds_conv:usds" in tags

    # Snapshot mock
    snapshot = MagicMock()
    snapshot.block = block
    snapshot.has.side_effect = lambda spec: True

    def mock_get(spec: CallSpec) -> CallResult:
        if spec.tag == "usds_conv:daiJoin":
            raw = "0x" + "0" * 24 + "9759a6ac90977b93b58547b4a71c78317f391a28"
        elif spec.tag == "usds_conv:usdsJoin":
            raw = "0x" + "0" * 24 + "3c0f895007ca717aa01c8693e59df1e8c3777feb"
        elif spec.tag == "usds_conv:dai":
            raw = "0x" + "0" * 24 + DAI[2:]
        elif spec.tag == "usds_conv:usds":
            raw = "0x" + "0" * 24 + USDS[2:]
        elif spec.tag in ("usds_conv:daiJoin.live", "usds_conv:usds.wards", "usds_conv:dai.wards"):
            raw = "0x" + hex(1)[2:].zfill(64)
        else:
            raw = "0x" + "0" * 64
        return CallResult(spec, True, raw)

    snapshot.get.side_effect = mock_get

    state = adapter.load_state(pool_rec, snapshot)
    assert isinstance(state, DaiUsdsState)
    assert state.live == 1
    assert state.usds_wards == 1
    assert state.dai_wards == 1


def test_unified_usds_connector_adapter_dispatch():
    unified = UsdsConnectorAdapter()
    conv_rec = make_dummy_converter_record()
    wrap_rec = make_dummy_wrapper_record()
    block = BlockRef(1, 23549939, "0x" + "a" * 64, 1760130851)

    conv_specs = unified.read_requests(conv_rec, block)
    wrap_specs = unified.read_requests(wrap_rec, block)

    assert len(conv_specs) == 4
    assert len(wrap_specs) == 7

    invalid_rec = PoolRecord(
        family="maker_sky_psm",
        chain=1,
        pool_id="maker_sky_psm:0x123:0x123",
        deployment="0x0000000000000000000000000000000000000123",
        pool="0x0000000000000000000000000000000000000123",
        tokens=(DAI_TOKEN, USDS_TOKEN),
        config={},
        created_block=None,
        discovered_by={},
    )
    with pytest.raises(Unsupported, match="neither DaiUsdsConverter nor UsdsPsmWrapper"):
        unified.read_requests(invalid_rec, block)


def test_usds_wrapper_adapter_specs_and_loading():
    adapter = UsdsPsmWrapperAdapter()
    wrap_rec = make_dummy_wrapper_record()
    block = BlockRef(1, 23549939, "0x" + "a" * 64, 1760130851)

    specs = adapter.read_requests(wrap_rec, block)
    assert len(specs) == 7

    snapshot = MagicMock()
    snapshot.block = block
    snapshot.has.side_effect = lambda spec: True

    def mock_get(spec: CallSpec) -> CallResult:
        if spec.tag == "usds_wrap:psm":
            raw = "0x" + "0" * 24 + LITE_PSM[2:]
        elif spec.tag == "usds_wrap:gem":
            raw = "0x" + "0" * 24 + USDC[2:]
        elif spec.tag == "usds_wrap:usds":
            raw = "0x" + "0" * 24 + USDS[2:]
        elif spec.tag == "usds_wrap:pocket":
            raw = "0x" + "0" * 24 + POCKET[2:]
        elif spec.tag == "usds_wrap:to18ConversionFactor":
            raw = "0x" + hex(10**12)[2:].zfill(64)
        elif spec.tag in ("usds_wrap:tin", "usds_wrap:tout"):
            raw = "0x" + "0" * 64
        elif spec.tag == "usds_wrap:psmDaiBalance":
            raw = "0x" + hex(400_000_000 * 10**18)[2:].zfill(64)
        elif spec.tag in ("usds_wrap:pocketGemBalance", "usds_wrap:pocketGemAllowance"):
            raw = "0x" + hex(2_000_000_000 * 10**6)[2:].zfill(64)
        else:
            raw = "0x" + "0" * 64
        return CallResult(spec, True, raw)

    snapshot.get.side_effect = mock_get

    state = adapter.load_state(wrap_rec, snapshot)
    assert isinstance(state, UsdsPsmWrapperState)
    assert state.tin == 0
    assert state.tout == 0
    assert state.dai_buffer == 400_000_000 * 10**18
    assert state.pocket_gem == 2_000_000_000 * 10**6


def test_pinned_evidence_offline_reproduction():
    """Verify saved raw pinned evidence file exists and all historical qualification checks passed."""
    import json
    from pathlib import Path

    evidence_file = Path(__file__).resolve().parents[1] / "outputs/source-expansion/usds/pinned_evidence.json"
    assert evidence_file.exists(), f"pinned evidence not found at {evidence_file}"

    pins = json.loads(evidence_file.read_text())
    assert len(pins) == 3, f"expected 3 pins, found {len(pins)}"

    expected_hashes = {
        23549939: "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
        23550094: "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d",
        23550192: "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c",
    }

    for p in pins:
        b = p["block"]
        num = b["number"]
        assert num in expected_hashes
        assert b["hash"] == expected_hashes[num]
        assert p["all_checks_passed"] is True
        assert p["dai_usds_state"]["live"] == 1
        assert p["dai_usds_state"]["usds_wards"] == 1
        assert p["dai_usds_state"]["dai_wards"] == 1
        assert p["usds_psm_wrapper_state"]["tin"] == 0
        assert p["usds_psm_wrapper_state"]["tout"] == 0
        assert int(p["usds_psm_wrapper_state"]["dai_buffer"]) > 400_000_000 * 10**18
        assert int(p["usds_psm_wrapper_state"]["pocket_gem"]) > 2_000_000_000 * 10**6


def test_produced_inventory_overlay_schema_regression():
    """Regression test: parse EVERY produced inventory row via pool_record_from_json.

    Verifies:
    1. Every pool row parses without ValueError or schema failure into a valid PoolRecord.
    2. SupportStatus values are strictly valid enum members (no invalid strings like 'unsupported').
    3. The reference-only UsdsPsmWrapper row parses as SupportStatus.DISCOVERED_UNSUPPORTED
       with its exclusion reason intact.
    4. Active converter and AMM pools parse as SupportStatus.SUPPORTED.
    """
    import json
    from pathlib import Path

    from swaparch.universe import pool_record_from_json

    overlay_file = (
        Path(__file__).resolve().parents[1]
        / "outputs/source-expansion/usds/usds_inventory_overlay.json"
    )
    assert overlay_file.exists(), f"overlay file not found at {overlay_file}"

    data = json.loads(overlay_file.read_text())
    assert "pools" in data
    assert len(data["pools"]) >= 2

    parsed_records = []
    for row in data["pools"]:
        record = pool_record_from_json(row)
        assert isinstance(record, PoolRecord)
        assert isinstance(record.status, SupportStatus)
        parsed_records.append(record)

    # Find the wrapper record and verify its exact status and exclusion reason
    wrapper_records = [r for r in parsed_records if norm_address(r.pool) == norm_address(WRAPPER)]
    assert len(wrapper_records) == 1
    wrapper_rec = wrapper_records[0]
    assert wrapper_rec.status == SupportStatus.DISCOVERED_UNSUPPORTED
    assert "Reference model only" in wrapper_rec.notes

    # Verify converter record
    conv_records = [r for r in parsed_records if norm_address(r.pool) == norm_address(CONVERTER)]
    assert len(conv_records) == 1
    assert conv_records[0].status == SupportStatus.SUPPORTED


