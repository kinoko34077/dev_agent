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


def test_gate_checker_requires_verified_not_just_implemented_or_integrated():
    for status in ("IMPLEMENTED", "INTEGRATED"):
        code, details = check({"schema_version": 2, "stages": {"B": {"B11": {"status": status}}}})
        assert code == 1
        assert details == [f"B/B11 ({status})"]
    assert check({"schema_version": 2, "stages": {"B": {"B11": {"status": "VERIFIED", "evidence": ["test"]}}}}) == (0, [])
    assert check({"schema_version": 2, "stages": {"B": {"B11": {"status": "PASS"}}}})[0] == 1


def test_gate_checker_rejects_v2_false_positive_metadata():
    try:
        check({"schema_version": 2, "phase6_entry": "PROHIBITED_UNTIL_ALL_PASS", "stages": {}})
    except ValueError as exc:
        assert "ALL_VERIFIED" in str(exc)
    else:
        raise AssertionError("stale phase6 entry must be rejected")
    try:
        check({"schema_version": 2, "stages": {"B": {"B11": {"status": "VERIFIED"}}}})
    except ValueError as exc:
        assert "evidence" in str(exc)
    else:
        raise AssertionError("verified gates must carry evidence")
