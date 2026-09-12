"""Independent offline regressions for study identity, resume, and accounting."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import historical_study as study


def block_row(manifest, inventory_file):
    row = manifest["blocks"]["1"]
    return json.loads((inventory_file.parent.parent / "run" / row["shard"]).read_text())


@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    inventory_file = inventory / "one.json"
    inventory_file.write_text(json.dumps({
        "family": "fixture", "status": "supported", "unresolved": [],
        "pools": [{"pool_id": "p", "status": "supported", "notes": "qualified",
                   "config": {"validated_block_hashes": ["h1"],
                              "registry_observations": {"h1": {"rate": 1}}}}],
    }))
    config = {"scenarios": [{"token_in": "WETH", "token_out": "USDC",
                             "amount": {"human": "1", "raw": str(10**18)}}]}
    calls = {"stages": [], "quotes": []}
    header = ["h1"]
    monkeypatch.setattr(study, "_code_identity", lambda: "fixture-code")

    def quote(job, output):
        calls["quotes"].append(dict(job))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "block": job["block"], "block_hash": header[0],
            "requested_solver": job["solver"], "offline": True, "network_requests": 0,
            "request": {"symbol_in": job["token_in"], "symbol_out": job["token_out"],
                        "amount_in": job["raw"]},
        }))
        return 0

    def stage(number):
        calls["stages"].append(number)
        print(json.dumps({"network_requests": 2}))
        return 0

    options = {"acquire": True, "stage_functions": [("fixture", stage)], "quote_function": quote,
               "inventory_root": inventory, "header_hash": lambda _: header[0]}

    def run(blocks=(1,), **overrides):
        return study.run_study(blocks, config, tmp_path / "run", **(options | overrides))

    return run, inventory_file, config, calls, header


def test_inventory_identity_tracks_admission_and_coverage_but_not_other_hash(fixture_run):
    _, path, _, _, _ = fixture_run
    base = json.loads(path.read_text())
    original = study._effective_inventory_identity("h1", path.parent)
    for field in ("status", "notes"):
        changed = copy.deepcopy(base)
        changed["pools"][0][field] = "changed"
        path.write_text(json.dumps(changed))
        assert study._effective_inventory_identity("h1", path.parent) != original
    for field in ("status", "unresolved"):
        changed = copy.deepcopy(base)
        changed[field] = "changed"
        path.write_text(json.dumps(changed))
        assert study._effective_inventory_identity("h1", path.parent) != original
    changed = copy.deepcopy(base)
    changed["pools"][0]["config"]["validated_block_hashes"].append("h2")
    changed["pools"][0]["config"]["registry_observations"]["h2"] = {"rate": 2}
    path.write_text(json.dumps(changed))
    assert study._effective_inventory_identity("h1", path.parent) == original


def test_retry_preserves_attempts_and_updates_reused_quote_coverage(fixture_run):
    run, inventory, _, calls, _ = fixture_run
    attempts = []

    def stage(number):
        attempts.append(number)
        if len(attempts) == 1:
            raise RuntimeError("request failed after contacting archive")
        print(json.dumps({"network_requests": 2}))
        return 0

    first = run(stage_functions=[("fixture", stage)])
    assert first["summary"]["all_stage_coverage_complete"] is False
    second = run(stage_functions=[("fixture", stage)])
    assert second["summary"]["all_stage_coverage_complete"] is True
    assert len(calls["quotes"]) == 1
    rows = block_row(second, inventory)["stages"]["fixture"]["attempts"]
    assert len(rows) == 2
    assert rows[0]["log"] != rows[1]["log"]
    assert all(Path(row["log"]).is_file() for row in rows)
    assert second["acquisition_network_requests"]["known"] == 2
    assert second["acquisition_network_requests"]["unknown_stages"] == 1
    run(stage_functions=[("fixture", stage)])
    assert len(attempts) == 2 and len(calls["quotes"]) == 1


def test_report_tamper_header_change_and_inventory_change_invalidate(fixture_run):
    run, inventory, _, calls, header = fixture_run
    first = run()
    report = Path(next(iter(block_row(first, inventory)["scenarios"].values()))["report"])
    report.write_text(report.read_text() + " ")
    run()
    assert len(calls["stages"]) == 1 and len(calls["quotes"]) == 2
    header[0] = "h2"
    run()
    assert len(calls["stages"]) == 2 and len(calls["quotes"]) == 3
    changed = json.loads(inventory.read_text())
    changed["pools"][0]["status"] = "discovered_unsupported"
    inventory.write_text(json.dumps(changed))
    last = run()
    assert len(calls["stages"]) == 3 and len(calls["quotes"]) == 4
    assert last["acquisition_network_requests"]["known"] == 6


def test_code_block_and_solver_budget_drift_rejected(fixture_run, monkeypatch):
    run, _, _, _, _ = fixture_run
    run()
    for changed in ({"grid_parts": 11}, {"max_steps": 7},
                    {"beam_width": 3}, {"max_expansions": 4}):
        with pytest.raises(ValueError, match="different code or scenario/solver"):
            run(**changed)
    with pytest.raises(ValueError, match="different planned blocks"):
        run(blocks=(2,))
    monkeypatch.setattr(study, "_code_identity", lambda: "changed-code")
    with pytest.raises(ValueError, match="different code or scenario/solver"):
        run()


def test_interruption_checkpoint_counts_all_planned_blocks(fixture_run):
    run, inventory, _, _, _ = fixture_run

    def interrupted(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(blocks=(1, 2, 3), stage_functions=[("fixture", interrupted)])
    manifest = json.loads((inventory.parent.parent / "run/manifest.json").read_text())
    assert manifest["planned_total"] == 3
    assert manifest["summary"]["total"] == 3
    assert manifest["summary"]["planned"] == manifest["summary"]["remaining"] == 3
    assert manifest["summary"]["all_stage_coverage_complete"] is False
    assert manifest["acquisition_network_requests"]["known"] == 0
    assert manifest["acquisition_network_requests"]["unknown_stages"] == 1
    resumed = run(blocks=(1, 2, 3))
    assert resumed["summary"]["completed"] == 3
    assert resumed["acquisition_network_requests"]["known"] == 6
    assert resumed["acquisition_network_requests"]["unknown_stages"] == 1
    assert len(block_row(resumed, inventory)["stages"]["fixture"]["attempts"]) == 2


def test_missing_acquisition_stages_are_not_complete(fixture_run):
    run, inventory, _, _, _ = fixture_run
    manifest = run(acquire=False, stage_functions=[])
    entry = next(iter(block_row(manifest, inventory)["scenarios"].values()))
    assert entry["coverage"]["complete"] is False
    assert set(entry["coverage"]["missing_stages"]) == {name for name, _ in study.STAGE_MODULES}
    assert manifest["summary"]["all_stage_coverage_complete"] is False


@pytest.mark.parametrize("change", ["header", "inventory"])
def test_offline_resume_marks_old_stage_identity_stale(fixture_run, change):
    run, inventory, _, calls, header = fixture_run
    run()
    if change == "header":
        header[0] = "h2"
    else:
        changed = json.loads(inventory.read_text())
        changed["status"] = "discovered_unsupported"
        inventory.write_text(json.dumps(changed))
    resumed = run(acquire=False)
    assert len(calls["stages"]) == 1 and len(calls["quotes"]) == 2
    entry = next(iter(block_row(resumed, inventory)["scenarios"].values()))
    assert entry["coverage"]["complete"] is False
    assert "fixture" in entry["coverage"]["stale_stages"]
    assert resumed["summary"]["all_stage_coverage_complete"] is False


def test_duplicate_scenarios_and_solvers_rejected(fixture_run):
    run, _, config, _, _ = fixture_run
    with pytest.raises(ValueError, match="duplicate"):
        run(solvers=("search", "search"))
    config["scenarios"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        run()


def test_exact_scenario_amounts_and_cli_budgets(monkeypatch):
    assert study._raw_amount("123456789012345678.123456789012345678", 18) == (
        "123456789012345678123456789012345678")
    assert study._raw_amount("0.000000000000000001", 18) == "1"
    for invalid in ("0", "-1", "NaN", "Infinity", "0.0000001", "1e-1000000000"):
        with pytest.raises(ValueError):
            study._raw_amount(invalid, 6)
    received = {}

    def run(*args, **kwargs):
        received.update(kwargs)
        return {"summary": {"failed": 0}, "acquisition_network_requests": {}}

    monkeypatch.setattr(study, "run_study", run)
    assert study.main(["--blocks", "1", "--run-id", "fixture", "--grid-parts", "3",
                       "--max-steps", "4", "--beam-width", "5", "--max-expansions", "6"]) == 0
    assert {key: received[key] for key in ("grid_parts", "max_steps", "beam_width", "max_expansions")} == {
        "grid_parts": 3, "max_steps": 4, "beam_width": 5, "max_expansions": 6}
