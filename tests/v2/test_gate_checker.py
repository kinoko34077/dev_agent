from pathlib import Path
import json

from scripts.check_gate import check, load_status


def test_current_gate_status_reports_actionable_work():
    value = load_status(Path("spec/v2/GATE_STATUS.json"))
    code, details = check(value)
    assert code == 1
    assert any("A/A7" in item for item in details)


def test_gate_checker_distinguishes_all_external_blockers():
    value = {"schema_version": 1, "stages": {"D": {"D1": {"status": "BLOCKED", "actionable": False, "blocker": "credential"}}}}
    assert check(value) == (2, ["D/D1: credential"])
