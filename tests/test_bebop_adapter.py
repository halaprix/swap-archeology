"""Focused regressions for Bebop adapter, quote ingestion, and historical support boundary."""

import pytest

from swaparch.adapters.bebop import (
    KNOWN_SETTLEMENT_CONTRACTS,
    ROUTER_CURRENT,
    SETTLEMENT_BLEND,
    SETTLEMENT_JAM,
    SETTLEMENT_JAM_OLD,
    SETTLEMENT_LEGACY_V2,
    BebopAdapter,
    BebopPoolState,
    normalize_bebop_quote,
    validate_quote_for_historical_block,
)
from swaparch.core.protocols import PoolState, SourceAdapter, Unsupported
from swaparch.core.types import (
    BlockRef,
    CallResult,
    CallSpec,
    PoolRecord,
    SupportStatus,
)

SAMPLE_RAW_QUOTE = {
    "requestId": "d97b5c21-c42e-41eb-b0bf-fe1e3af13718",
    "type": "121",
    "status": "SIG_SUCCESS",
    "quoteId": "121-210034234581768229581476461431270660650",
    "chainId": 1,
    "approvalType": "Standard",
    "nativeToken": "ETH",
    "taker": "0x5Bad996643a924De21b6b2875c85C33F3c5bBcB6",
    "receiver": "0x5Bad996643a924De21b6b2875c85C33F3c5bBcB6",
    "expiry": 1788960409,
    "slippage": 0.0,
    "buyTokens": {
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": {
            "amount": "2488191173",
            "decimals": 6,
            "symbol": "USDC",
            "priceUsd": 0.999891,
        }
    },
    "sellTokens": {
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": {
            "amount": "1000000000000000000",
            "decimals": 18,
            "symbol": "WETH",
            "priceUsd": 2513.15,
        }
    },
    "settlementAddress": ROUTER_CURRENT,
    "approvalTarget": ROUTER_CURRENT,
    "priceImpact": -0.010039,
    "info": "You are using Bebop's public API. For higher rate limits...",
    "tx": {
        "to": ROUTER_CURRENT,
        "value": "0x0",
        "data": "0x9586d0e800000000",
        "from": "0x5Bad996643a924De21b6b2875c85C33F3c5bBcB6",
        "gas": 391793,
        "gasPrice": 263945474,
    },
}

MOCK_BLOCK = BlockRef(1, 25896003, "0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623", 1740000000)


class MockSnapshot:
    def __init__(self, block: BlockRef):
        self.block = block

    def get(self, spec: CallSpec) -> CallResult:
        return CallResult(spec, False, "0x", "mock")

    def has(self, spec: CallSpec) -> bool:
        return False


def test_bebop_adapter_conforms_to_source_adapter_protocol():
    adapter = BebopAdapter()
    assert isinstance(adapter, SourceAdapter)
    assert adapter.family == "bebop"

    record = PoolRecord(
        family="bebop",
        chain=1,
        pool_id="bebop:settlement:0xbbbbbbb520d69a9775e85b458c58c648259fad5f",
        deployment=SETTLEMENT_BLEND,
        pool=SETTLEMENT_BLEND,
        tokens=(),
        config={},
        created_block=19783283,
        discovered_by={},
        status=SupportStatus.UNAVAILABLE,
    )

    assert adapter.read_requests(record, MOCK_BLOCK) == []
    snapshot = MockSnapshot(MOCK_BLOCK)
    assert adapter.dependent_requests(record, MOCK_BLOCK, snapshot) == []

    with pytest.raises(Unsupported, match="requires_external_quote_archive"):
        adapter.load_state(record, snapshot)


def test_quote_normalization_validates_chain_and_status():
    raw_bad_chain = dict(SAMPLE_RAW_QUOTE, chainId=137)
    with pytest.raises(ValueError, match="Unsupported chainId"):
        normalize_bebop_quote(raw_bad_chain)

    raw_bad_status = dict(SAMPLE_RAW_QUOTE, status="FAILED")
    with pytest.raises(ValueError, match="not executable"):
        normalize_bebop_quote(raw_bad_status)


def test_quote_normalization_validates_amounts():
    bad_amount = {
        **SAMPLE_RAW_QUOTE,
        "sellTokens": {
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": {
                "amount": "0",
                "decimals": 18,
                "symbol": "WETH",
            }
        },
    }
    with pytest.raises(ValueError, match="strictly positive"):
        normalize_bebop_quote(bad_amount)


def test_quote_normalization_detects_demo_mode_and_preserves_identities():
    norm = normalize_bebop_quote(SAMPLE_RAW_QUOTE, fetched_at="2026-09-09T15:00:00Z")
    assert norm.is_demo is True
    assert norm.fetch_time == "2026-09-09T15:00:00Z"
    assert norm.quote_id == "121-210034234581768229581476461431270660650"
    assert norm.amount_in == 10**18
    assert norm.amount_out == 2488191173
    assert norm.gas_estimate == 391793
    assert norm.tx_data == "0x9586d0e800000000"
    assert norm.raw_response["requestId"] == SAMPLE_RAW_QUOTE["requestId"]


def test_quote_normalization_validates_expiry():
    # Quote expired relative to reference timestamp
    with pytest.raises(ValueError, match="Quote expired"):
        normalize_bebop_quote(SAMPLE_RAW_QUOTE, reference_timestamp=1788960410)


def test_quote_state_refuses_token_mismatch_and_direction():
    norm = normalize_bebop_quote(SAMPLE_RAW_QUOTE)
    dummy_record = PoolRecord(
        family="bebop",
        chain=1,
        pool_id=f"bebop:rfq:{norm.quote_id}",
        deployment=ROUTER_CURRENT,
        pool=ROUTER_CURRENT,
        tokens=(norm.token_in, norm.token_out),
        config={},
        created_block=None,
        discovered_by={},
        status=SupportStatus.SUPPORTED,
    )
    state = BebopPoolState(record=dummy_record, quote=norm)
    assert isinstance(state, PoolState)

    # Correct direction succeeds
    assert state.quote_exact_in(norm.token_in.address, norm.token_out.address, 10**18) == 2488191173

    # Reversed direction fails
    with pytest.raises(Unsupported, match="direction/token mismatch"):
        state.quote_exact_in(norm.token_out.address, norm.token_in.address, 2488191173)

    # Different token fails
    usdt = "0xdac17f958d2ee523a2206206994597c13d831ec7"
    with pytest.raises(Unsupported, match="direction/token mismatch"):
        state.quote_exact_in(norm.token_in.address, usdt, 10**18)


def test_quote_state_refuses_amount_interpolation():
    norm = normalize_bebop_quote(SAMPLE_RAW_QUOTE)
    dummy_record = PoolRecord(
        family="bebop",
        chain=1,
        pool_id=f"bebop:rfq:{norm.quote_id}",
        deployment=ROUTER_CURRENT,
        pool=ROUTER_CURRENT,
        tokens=(norm.token_in, norm.token_out),
        config={},
        created_block=None,
        discovered_by={},
        status=SupportStatus.SUPPORTED,
    )
    state = BebopPoolState(record=dummy_record, quote=norm)

    # Half amount fails (no interpolation)
    with pytest.raises(Unsupported, match="cannot interpolate or scale"):
        state.quote_exact_in(norm.token_in.address, norm.token_out.address, 5 * 10**17)

    # Double amount fails
    with pytest.raises(Unsupported, match="cannot interpolate or scale"):
        state.quote_exact_in(norm.token_in.address, norm.token_out.address, 2 * 10**18)


def test_quote_state_single_use_capacity():
    norm = normalize_bebop_quote(SAMPLE_RAW_QUOTE)
    dummy_record = PoolRecord(
        family="bebop",
        chain=1,
        pool_id=f"bebop:rfq:{norm.quote_id}",
        deployment=ROUTER_CURRENT,
        pool=ROUTER_CURRENT,
        tokens=(norm.token_in, norm.token_out),
        config={},
        created_block=None,
        discovered_by={},
        status=SupportStatus.SUPPORTED,
    )
    state = BebopPoolState(record=dummy_record, quote=norm)
    out, next_state = state.swap(norm.token_in.address, norm.token_out.address, 10**18)
    assert out == 2488191173
    assert next_state.consumed is True

    # Attempting second swap on consumed state fails
    with pytest.raises(Unsupported, match="already consumed"):
        next_state.quote_exact_in(norm.token_in.address, norm.token_out.address, 10**18)


def test_refusal_of_current_quote_at_historical_pins_and_october_window():
    """Current live quotes cannot be promoted to historical pins or crash windows."""
    live_quote = normalize_bebop_quote(SAMPLE_RAW_QUOTE, is_historical=False)

    # Test the 5 canonical project pins
    five_pins = [
        BlockRef(1, 23549991, "0x31531ed6336f65d949af3388172e68385efb87271aae232e9a229c25ab6b02dc", 1760131487),
        BlockRef(1, 23550060, "0x4716e026c29768f740e47b68fe6fceb7070f128cb088b09f2b60d15bd4548499", 1760132315),
        BlockRef(1, 23728292, "0x6b32febfa7c4a4fdf9d7be134eb7c68a24e7c6720eefcf17da244d743a0b3d59", 1762271879),
        BlockRef(1, 24356381, "0x386830feda105fd1704e067fa0e0702065e14f8925af6f17270e9b16fe718eeb", 1769808995),
        BlockRef(1, 25896003, "0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623", 1788288000),
    ]

    # Test October 10, 2025 window endpoints (21:14 to 22:05 UTC)
    october_window_blocks = [
        BlockRef(1, 23549939, "0x789abc...", 1760130851),
        BlockRef(1, 23550192, "0xdef123...", 1760133899),
    ]

    for block in five_pins + october_window_blocks:
        with pytest.raises(Unsupported, match="requires_external_quote_archive"):
            validate_quote_for_historical_block(live_quote, block)

        # Adapter load_state_from_quote must also refuse
        adapter = BebopAdapter()
        dummy_record = PoolRecord(
            family="bebop",
            chain=1,
            pool_id=f"bebop:rfq:{live_quote.quote_id}",
            deployment=ROUTER_CURRENT,
            pool=ROUTER_CURRENT,
            tokens=(live_quote.token_in, live_quote.token_out),
            config={},
            created_block=None,
            discovered_by={},
            status=SupportStatus.SUPPORTED,
        )
        with pytest.raises(Unsupported, match="requires_external_quote_archive"):
            adapter.load_state_from_quote(dummy_record, live_quote, block=block)


def test_canonical_settlement_addresses():
    assert SETTLEMENT_BLEND == "0xbbbbbbb520d69a9775e85b458c58c648259fad5f"
    assert SETTLEMENT_LEGACY_V2 == "0xbeb09000fa59627dc02bb55448ac1893eaa501a5"
    assert SETTLEMENT_JAM == "0xbeb0b0623f66be8ce162ebdfa2ec543A522F4ea6".lower()
    assert SETTLEMENT_JAM_OLD == "0xbEbEbEb035351f58602E0C1C8B59ECBfF5d5f47b".lower()
    assert ROUTER_CURRENT == "0xBeb0009ACa35087ce7cCF11637E24dd1Aad3bf2A".lower()
    assert len(KNOWN_SETTLEMENT_CONTRACTS) == 5
