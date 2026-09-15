"""Tests for October oracle reference collector: rounding, decimals, call failures, and pinned blocks."""

import pytest
from eth_abi import decode, encode

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.oracles.reference_collector import (
    ONEINCH_OFFCHAIN_ORACLE,
    UNISWAP_V3_USDC_WETH_500,
    USDC_ADDRESS,
    WETH_ADDRESS,
    collect_block_references,
    compute_arithmetic_mean_tick,
    decode_1inch_rate,
    decode_univ3_twap,
    get_quote_at_tick_canonical,
)
from swaparch.rpc.client import RpcClient
from swaparch.rpc.multicall import Multicall3


def _solidity_reference_oracle_library_tick_mean(delta: int, seconds_ago: int) -> int:
    """Exact simulation of Solidity OracleLibrary.consult tick division.

    Solidity:
        arithmeticMeanTick = int24(tickCumulativesDelta / secondsAgo);
        if (tickCumulativesDelta < 0 && (tickCumulativesDelta % secondsAgo != 0)) arithmeticMeanTick--;
    """
    quotient = int(delta / seconds_ago)
    remainder = abs(delta) % seconds_ago
    if delta < 0 and remainder != 0:
        quotient -= 1
    return quotient


def test_floor_negative_tick_rounding():
    """Verify that compute_arithmetic_mean_tick properly floors negative tick deltas."""
    test_cases = [
        # (delta, window)
        (10, 3),    # 3.333 -> 3
        (12, 3),    # 4.0 -> 4
        (0, 60),    # 0.0 -> 0
        (1, 60),    # 0.016 -> 0
        (-1, 60),   # -0.016 -> -1
        (-59, 60),  # -0.983 -> -1
        (-60, 60),  # -1.0 -> -1
        (-61, 60),  # -1.016 -> -2
        (-10, 3),   # -3.333 -> -4
        (-12, 3),   # -4.0 -> -4
        (-11640612, 60),
        (11640612, 60),
        (-58129152, 300),
        (58129152, 300),
    ]
    for delta, window in test_cases:
        expected = _solidity_reference_oracle_library_tick_mean(delta, window)
        actual = compute_arithmetic_mean_tick(delta, window)
        assert actual == expected, f"Mismatch for delta={delta}, window={window}: actual={actual} != expected={expected}"

    with pytest.raises(ValueError, match="seconds_ago must be positive"):
        compute_arithmetic_mean_tick(100, 0)
    with pytest.raises(ValueError, match="seconds_ago must be positive"):
        compute_arithmetic_mean_tick(100, -10)


def test_canonical_get_quote_at_tick_and_decimals():
    """Verify canonical Uniswap V3 getQuoteAtTick and 1inch decimal scaling."""
    # 1. Uniswap V3: Tick 194010 at baseAmount = 10^18 WETH
    quote_raw_194010 = get_quote_at_tick_canonical(194010, base_amount=10**18, base_token=WETH_ADDRESS, quote_token=USDC_ADDRESS)
    assert quote_raw_194010 == 3755554042
    price_194010 = quote_raw_194010 / 1e6
    assert abs(price_194010 - 3755.554042) < 1e-6

    # Tick 193763 at baseAmount = 10^18 WETH
    quote_raw_193763 = get_quote_at_tick_canonical(193763, base_amount=10**18, base_token=WETH_ADDRESS, quote_token=USDC_ADDRESS)
    assert quote_raw_193763 == 3849466577
    price_193763 = quote_raw_193763 / 1e6
    assert abs(price_193763 - 3849.466577) < 1e-6

    # 2. 1inch decimal scaling: WETH(18) -> USDC(6)
    # rate is returned with 1e18 scale in raw units: price = rate / 10^6
    spec = CallSpec(ONEINCH_OFFCHAIN_ORACLE, "0x")
    res_ok = CallResult(spec, True, "0x" + encode(["uint256"], [3781532393]).hex(), "eth_call")
    dec_ok = decode_1inch_rate(res_ok)
    assert dec_ok["success"] is True
    assert dec_ok["status"] == "ok"
    assert dec_ok["rawRate"] == 3781532393
    assert abs(dec_ok["price"] - 3781.532393) < 1e-6

    # Edge cases
    res_unit = CallResult(spec, True, "0x" + encode(["uint256"], [1_000_000]).hex(), "eth_call")
    dec_unit = decode_1inch_rate(res_unit)
    assert abs(dec_unit["price"] - 1.0) < 1e-6

    res_micro = CallResult(spec, True, "0x" + encode(["uint256"], [1]).hex(), "eth_call")
    dec_micro = decode_1inch_rate(res_micro)
    assert abs(dec_micro["price"] - 0.000001) < 1e-9


def test_call_failure_and_revert_semantics_no_fallback():
    """Verify fail-closed error handling: returns null price and explicit status, no fallback."""
    spec_1inch = CallSpec(ONEINCH_OFFCHAIN_ORACLE, "0x")
    spec_univ3 = CallSpec(UNISWAP_V3_USDC_WETH_500, "0x")

    # 1. 1inch call revert
    res_fail = CallResult(spec_1inch, False, "0x", "eth_call")
    dec_fail = decode_1inch_rate(res_fail)
    assert dec_fail["success"] is False
    assert dec_fail["status"] == "reverted"
    assert dec_fail["price"] is None
    assert "reverted" in dec_fail["reason"]

    # 2. 1inch zero rate
    res_zero = CallResult(spec_1inch, True, "0x" + encode(["uint256"], [0]).hex(), "eth_call")
    dec_zero = decode_1inch_rate(res_zero)
    assert dec_zero["success"] is True
    assert dec_zero["status"] == "zero_rate"
    assert dec_zero["price"] is None
    assert "zero rate" in dec_zero["reason"]

    # 3. Uniswap V3 call revert (e.g. insufficient observation history 'OLD')
    revert_data = "0x08c379a0" + encode(["string"], ["OLD"]).hex()
    res_v3_revert = CallResult(spec_univ3, False, revert_data, "eth_call")
    dec_v3 = decode_univ3_twap(res_v3_revert, 300)
    assert dec_v3["success"] is False
    assert dec_v3["status"] == "reverted"
    assert dec_v3["price"] is None
    assert dec_v3["tickCumulatives"] is None
    assert "reverted" in dec_v3["reason"]

    # 4. Bad payload decoding
    res_bad = CallResult(spec_univ3, True, "0xdeadbeef", "eth_call")
    dec_bad = decode_univ3_twap(res_bad, 300)
    assert dec_bad["success"] is False
    assert dec_bad["status"] == "decoding_error"
    assert dec_bad["price"] is None


def test_pinned_three_blocks_independent_decode():
    """Verify independent decoding of the three historical crash pins: 23549939, 23550094, 23550192."""
    client = RpcClient()
    mc = Multicall3(client)

    pins = [
        {
            "block": 23549939,
            "hash": "0x965cc1fbaaa1e134a5ed4649e8d55ed10f791577059e86b2bd1f95822d2ddf12",
            "expected_1inch_rate": 3781532393,
            "expected_1inch_price": 3781.532393,
            "expected_twap60_tick": 194010,
            "expected_twap60_price": 3755.554042,
            "expected_twap300_tick": 193763,
            "expected_twap300_price": 3849.466577,
        },
        {
            "block": 23550094,
            "hash": "0x6a9c5b9c7c69d0da72dfea4ff0da559dfb7382b58e5796857916e0591c1c7c6d",
            "expected_1inch_rate": 3703034020,
            "expected_1inch_price": 3703.034020,
            "expected_twap60_tick": 194571,
            "expected_twap60_price": 3550.678198,
            "expected_twap300_tick": 194653,
            "expected_twap300_price": 3521.683129,
        },
        {
            "block": 23550192,
            "hash": "0x421e085a31e315c2358ad8769965950726e92f73b94b35e8c0fa0695bd3d214c",
            "expected_1inch_rate": 3822672246,
            "expected_1inch_price": 3822.672246,
            "expected_twap60_tick": 193648,
            "expected_twap60_price": 3893.988728,
            "expected_twap300_tick": 193681,
            "expected_twap300_price": 3881.160385,
        },
    ]

    for pin in pins:
        block_ref = BlockRef(1, pin["block"], pin["hash"], 0)
        sidecar_vals, raw_row = collect_block_references(mc, block_ref)

        # 1. Check sidecar values
        assert sidecar_vals["oneinch_spot"]["status"] == "ok"
        assert abs(sidecar_vals["oneinch_spot"]["price"] - pin["expected_1inch_price"]) < 1e-5

        assert sidecar_vals["uniswap_v3_twap_60"]["status"] == "ok"
        assert abs(sidecar_vals["uniswap_v3_twap_60"]["price"] - pin["expected_twap60_price"]) < 1e-5

        assert sidecar_vals["uniswap_v3_twap_300"]["status"] == "ok"
        assert abs(sidecar_vals["uniswap_v3_twap_300"]["price"] - pin["expected_twap300_price"]) < 1e-5

        # 2. Check raw integers
        assert raw_row["oneinch_spot"]["rawRate"] == pin["expected_1inch_rate"]
        assert raw_row["uniswap_v3_twap_60"]["arithmeticMeanTick"] == pin["expected_twap60_tick"]
        assert raw_row["uniswap_v3_twap_300"]["arithmeticMeanTick"] == pin["expected_twap300_tick"]

        # 3. Independent raw byte decode check (bypassing collector helpers directly)
        raw_hex_1inch = raw_row["oneinch_spot"]["raw"]
        raw_int_1inch = decode(["uint256"], bytes.fromhex(raw_hex_1inch[2:]))[0]
        assert raw_int_1inch == pin["expected_1inch_rate"]
        assert raw_int_1inch / 1e6 == pin["expected_1inch_price"]


def test_published_sidecar_contract_and_integrity():
    """Verify published sidecar JSON strictly complies with contract across all 254 blocks."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sidecar_path = root / "frontend/public/october-oracle-references.json"
    source_path = root / "frontend/public/october-sources.json"

    assert sidecar_path.exists(), "Sidecar file must exist"
    with open(sidecar_path) as f:
        sidecar = json.load(f)
    with open(source_path) as f:
        source = json.load(f)

    # Contract checks
    assert sidecar["schemaVersion"] == 1
    source_ids = {s["id"] for s in sidecar["sources"]}
    assert len(source_ids) == len(sidecar["sources"])
    assert {"oneinch_spot", "uniswap_v3_twap_60", "uniswap_v3_twap_300"} <= source_ids

    assert len(sidecar["rows"]) == 254
    assert len(source["rows"]) == 254

    for s_row, r_row in zip(source["rows"], sidecar["rows"]):
        assert s_row["block"] == r_row["block"]
        assert s_row["blockHash"] == r_row["blockHash"]
        assert s_row["timestamp"] == r_row["timestamp"]

        vals = r_row["values"]
        for src_id in ["oneinch_spot", "uniswap_v3_twap_60", "uniswap_v3_twap_300"]:
            assert src_id in vals
            entry = vals[src_id]
            assert "price" in entry
            assert "status" in entry
            assert entry["status"] == "ok"
            assert isinstance(entry["price"], (int, float))
            assert 3000 <= entry["price"] <= 4500


def test_observe_requires_exact_array_lengths():
    """Verify that decode_univ3_twap rejects observe results with length != 2."""
    spec = CallSpec(UNISWAP_V3_USDC_WETH_500, "0x")

    # 1 element instead of 2
    bad_data_1 = "0x" + encode(["int56[]", "uint160[]"], [[12345], [67890]]).hex()
    res_bad_1 = CallResult(spec, True, bad_data_1, "eth_call")
    dec_1 = decode_univ3_twap(res_bad_1, 60)
    assert dec_1["success"] is False
    assert dec_1["status"] == "decoding_error"
    assert "invalid array lengths" in dec_1["reason"]

    # 3 elements instead of 2
    bad_data_3 = "0x" + encode(["int56[]", "uint160[]"], [[1, 2, 3], [4, 5, 6]]).hex()
    res_bad_3 = CallResult(spec, True, bad_data_3, "eth_call")
    dec_3 = decode_univ3_twap(res_bad_3, 60)
    assert dec_3["success"] is False
    assert dec_3["status"] == "decoding_error"
    assert "invalid array lengths" in dec_3["reason"]


def test_source_validation_truncated_duplicate_outofrange():
    """Verify collector rejects truncated, duplicate, or out-of-range block inputs."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root / "scripts") not in sys.path:
        sys.path.insert(0, str(root / "scripts"))

    from collect_october_oracle_references import (
        CANONICAL_END,
        CANONICAL_START,
        validate_requested_blocks,
        validate_source_dataset,
    )

    # 1. Truncated rows (253 instead of 254)
    truncated_rows = [
        {"block": b, "blockHash": "0x" + "aa" * 32, "timestamp": "2025-10-10T21:14:11Z"}
        for b in range(CANONICAL_START, CANONICAL_END)  # missing last block
    ]
    with pytest.raises(ValueError, match="Source row count mismatch"):
        validate_source_dataset({"rows": truncated_rows})

    # 2. Duplicate block
    dup_rows = [
        {"block": b, "blockHash": "0x" + "aa" * 32, "timestamp": "2025-10-10T21:14:11Z"}
        for b in range(CANONICAL_START, CANONICAL_END + 1)
    ]
    dup_rows[1]["block"] = dup_rows[0]["block"]
    with pytest.raises(ValueError, match="Duplicate block"):
        validate_source_dataset({"rows": dup_rows})

    # 3. Out-of-range requested block
    valid_blocks = set(range(CANONICAL_START, CANONICAL_END + 1))
    with pytest.raises(ValueError, match="outside canonical range"):
        validate_requested_blocks([CANONICAL_START, 23549000], valid_blocks)
    with pytest.raises(ValueError, match="outside canonical range"):
        validate_requested_blocks([CANONICAL_END + 1], valid_blocks)


def test_subset_run_isolation_does_not_clobber_full_artifacts(tmp_path):
    """Verify that a subset run isolates its output and never clobbers full artifacts."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "test_oracle_refs"
    out_dir.mkdir()

    # Pre-populate fake full artifacts in out_dir
    fake_full_raw = out_dir / "raw-responses.json"
    fake_full_raw.write_text(json.dumps({"schemaVersion": 1, "blockCount": 254, "marker": "FULL"}))
    fake_full_prov = out_dir / "provenance.json"
    fake_full_prov.write_text(json.dumps({"schemaVersion": 1, "marker": "FULL_PROV"}))
    fake_sidecar = tmp_path / "october-oracle-references.json"
    fake_sidecar.write_text(json.dumps({"schemaVersion": 1, "marker": "FULL_SIDECAR"}))

    # Run collector script with subset --blocks 23549939
    script_path = root / "scripts/collect_october_oracle_references.py"
    cmd = [
        sys.executable,
        str(script_path),
        "--blocks", "23549939",
        "--out-dir", str(out_dir),
        "--out-sidecar", str(fake_sidecar),
        "--offline",
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    # Verify full artifacts are untouched!
    assert json.loads(fake_full_raw.read_text())["marker"] == "FULL"
    assert json.loads(fake_full_prov.read_text())["marker"] == "FULL_PROV"
    assert json.loads(fake_sidecar.read_text())["marker"] == "FULL_SIDECAR"

    # Verify subset output was written to isolated subset/ dir!
    subset_raw = out_dir / "subset/raw-responses-subset.json"
    assert subset_raw.exists()
    assert json.loads(subset_raw.read_text())["blockCount"] == 1
    subset_prov = out_dir / "subset/provenance-subset.json"
    assert subset_prov.exists()
    assert json.loads(subset_prov.read_text())["isSubset"] is True
