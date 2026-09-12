import gzip
import json
import os
import shutil
import subprocess
from copy import deepcopy

import pytest

from swaparch.explorer import build_explorer, main


def report(block, token_in, token_out, symbol_in, symbol_out, amount, output, baseline=100):
    return {
        "chain": 1, "block": block, "block_hash": f"0x{block:x}", "timestamp": block,
        "request": {"token_in": token_in, "token_out": token_out, "symbol_in": symbol_in,
                    "symbol_out": symbol_out, "decimals_in": 0, "decimals_out": 0,
                    "amount_in": amount},
        "best_split": {"amount_out": output, "steps": [], "search_info": {"kind": "direct"},
                       "gas_estimate": None},
        "single_pool_baseline": {"amount_out": baseline}, "requested_solver": "search",
        "selected_families": ["uniswap_v2"],
        "sources": [{"family": "uniswap_v2", "status": "supported", "selected": True,
                     "usable_pools": 1}, {"family": "curve", "status": "supported",
                     "selected": False, "usable_pools": 4}],
        "unsupported": [{"family": "curve", "status": "discovered_unsupported",
                         "reason": "not selected for this scenario"}],
        "limitations": ["before gas"],
    }


def write_reports(tmp_path, reports):
    for index, item in enumerate(reports):
        (tmp_path / f"{index}.json").write_text(json.dumps(item))


def test_explorer_accepts_saved_scenarios_without_precision_or_diagnostic_loss(tmp_path):
    first = report(123, "0xin", "0xout", "IN", "OUT", 900719925474099312345,
                   900719925474099312346, baseline=900719925474099312344)
    first["sources"][0]["family"] = "<script>alert(1)</script>"
    first["best_split"]["search_info"]["dual"] = {"runtime_seconds": 1.25}
    no_route = report(124, "0xnone", "0xout", "NONE", "OUT", 1, 1)
    no_route["best_split"] = None
    no_route["single_pool_baseline"] = None
    write_reports(tmp_path, [first, no_route])
    output = tmp_path / "explorer.html"
    assert build_explorer([tmp_path], output) == output
    html = output.read_text()
    assert "900719925474099312345" in html and "BigInt" in html
    assert "\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html
    assert '"selected_families":["uniswap_v2"]' in html
    assert '"best_split":null' in html and "runtime_seconds" not in html
    assert main([str(tmp_path), "--output", str(tmp_path / "cli.html")]) == 0
    with pytest.raises(FileNotFoundError, match="does not exist"):
        build_explorer([tmp_path / "missing.json"], output)
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{}")
    with pytest.raises(ValueError, match="not a saved swaparch quote report"):
        build_explorer([malformed], output)


def test_explorer_rejects_conflicting_duplicate_scenarios(tmp_path):
    one = report(123, "0xin", "0xout", "IN", "OUT", 1, 110)
    two = deepcopy(one)
    two["best_split"]["amount_out"] = 111
    write_reports(tmp_path, [one, two])
    with pytest.raises(ValueError, match="conflicting saved reports"):
        build_explorer([tmp_path], tmp_path / "explorer.html")


def test_explorer_reads_gzip_quote_reports(tmp_path):
    value = report(123, "0xin", "0xout", "IN", "OUT", 1, 110)
    with gzip.open(tmp_path / "quote.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(value, handle)

    output = tmp_path / "explorer.html"
    assert build_explorer([tmp_path / "*.json"], output) == output
    assert "123" in output.read_text()


@pytest.mark.skipif(not os.environ.get("SWAPARCH_BROWSER_TEST") or not shutil.which("google-chrome"),
                    reason="set SWAPARCH_BROWSER_TEST=1 with Google Chrome to run browser controls")
def test_browser_controls_keep_pair_and_show_missing_block_gap(tmp_path):
    reports = [report(100, "0xa", "0xb", "A", "B", 100, 110),
               report(100, "0xb", "0xa", "B", "A", 100, 999, baseline=1000),
               report(100, "0xc", "0xd", "C", "D", 100, 110),
               report(200, "0xe", "0xf", "E", "F", 100, 1)]
    reports[1]["request"]["decimals_in"] = 6
    reports[3]["best_split"] = None
    reports[3]["single_pool_baseline"] = None
    write_reports(tmp_path, reports)
    artifact = build_explorer([tmp_path], tmp_path / "explorer.html")
    html = artifact.read_text().replace("</body>", """<script>
      let pair=document.getElementById('pair'), block=document.getElementById('block');
      pair.value='C / D'; pair.dispatchEvent(new Event('change'));
      document.body.dataset.pair=document.getElementById('out').textContent;
      document.body.dataset.gain=document.getElementById('gain').textContent;
      pair.value='A / B'; pair.dispatchEvent(new Event('change'));
      let direction=document.getElementById('direction'); direction.value='B → A';
      direction.dispatchEvent(new Event('change'));
      document.body.dataset.reverseSize=document.getElementById('size').options[0].textContent;
      document.body.dataset.reverseGain=document.getElementById('gain').textContent;
      block.value='1'; block.dispatchEvent(new Event('input'));
      document.body.dataset.gap=document.getElementById('empty').textContent;
      document.body.dataset.when=document.getElementById('when').textContent;
    </script></body>""")
    artifact.write_text(html)
    browser = shutil.which("google-chrome")
    result = subprocess.run([browser, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
                             f"--user-data-dir={tmp_path / 'chrome'}", artifact.as_uri()], check=True,
                            text=True, capture_output=True)
    assert 'data-pair="110 D"' in result.stdout
    assert 'data-gain="10.00%"' in result.stdout
    assert 'data-reverse-size="0.0001 B"' in result.stdout
    assert 'data-reverse-gain="-0.10%"' in result.stdout
    assert 'data-gap="No saved report at this block for the selected scenario."' in result.stdout
    assert 'data-when="Block 200' in result.stdout


@pytest.mark.parametrize("field,value", [("amount_in", -1), ("decimals_in", True),
                                           ("decimals_out", 256), ("amount_out", True)])
def test_explorer_rejects_invalid_raw_amounts_and_decimals(tmp_path, field, value):
    invalid = report(123, "0xin", "0xout", "IN", "OUT", 1, 1)
    target = invalid["request"] if field in invalid["request"] else invalid["best_split"]
    target[field] = value
    write_reports(tmp_path, [invalid])
    with pytest.raises(ValueError, match="invalid"):
        build_explorer([tmp_path], tmp_path / "explorer.html")


def test_explorer_rejects_ambiguous_block_and_token_selectors(tmp_path):
    one = report(123, "0xin", "0xout", "IN", "OUT", 1, 1)
    other_chain = deepcopy(one)
    other_chain["chain"] = 2
    other_chain["block_hash"] = "0xother"
    write_reports(tmp_path, [one, other_chain])
    with pytest.raises(ValueError, match="chain/hash"):
        build_explorer([tmp_path], tmp_path / "explorer.html")
    other_chain["chain"] = 1
    other_chain["block_hash"] = one["block_hash"]
    other_chain["request"]["token_in"] = "0xother"
    write_reports(tmp_path, [one, other_chain])
    with pytest.raises(ValueError, match="token addresses"):
        build_explorer([tmp_path], tmp_path / "explorer.html")
