from pathlib import Path
import json

from scripts.check_gate import check, load_status


def test_current_gate_status_reports_phase6_operational_work_in_progress():
    value = load_status(Path("spec/v2/GATE_STATUS.json"))
    code, details = check(value)
    assert code == 1
    assert any(detail.startswith("G/G6O") for detail in details)


def test_gate_checker_distinguishes_all_external_blockers():
    value = {"schema_version": 1, "stages": {"D": {"D1": {"status": "BLOCKED", "actionable": False, "blocker": "credential"}}}}
    assert check(value) == (2, ["D/D1: credential"])


def test_gate_checker_requires_verified_not_just_implemented_or_integrated():
    for status in ("IMPLEMENTED", "INTEGRATED"):
        code, details = check({"schema_version": 2, "phase6_entry": "ALL_VERIFIED", "stages": {"B": {"B11": {"status": status}}}})
        assert code == 1
        assert details == [f"B/B11 ({status})"]
    assert check({"schema_version": 2, "phase6_entry": "ALL_VERIFIED", "stages": {"B": {"B11": {"status": "VERIFIED", "evidence": ["test"]}}}}) == (0, [])
    assert check({"schema_version": 2, "phase6_entry": "ALL_VERIFIED", "stages": {"B": {"B11": {"status": "PASS"}}}})[0] == 1


def test_gate_checker_rejects_v2_false_positive_metadata():
    try:
        check({"schema_version": 2, "phase6_entry": "PROHIBITED_UNTIL_ALL_PASS", "stages": {}})
    except ValueError as exc:
        assert "ALL_VERIFIED" in str(exc)
    else:
        raise AssertionError("stale phase6 entry must be rejected")
    try:
        check({"schema_version": 2, "phase6_entry": "ALL_VERIFIED", "stages": {"B": {"B11": {"status": "VERIFIED"}}}})
    except ValueError as exc:
        assert "evidence" in str(exc)
    else:
        raise AssertionError("verified gates must carry evidence")


def test_gate_checker_separates_deferred_future_requirements_from_current_gates():
    value = {
        "schema_version": 3,
        "phase6_entry": "ALL_VERIFIED",
        "phase_classification": {
            "phase3_5": {"gates": ["B/B11"]},
            "phase4": {"gates": []},
            "phase5": {"gates": []},
            "phase6": {"gates": ["F/F6A", "F/F6B", "F/F6C", "F/F6D", "F/F6E"]},
            "phase6_future": {"requirements": ["E33/live_restore"]},
            "phase7_future": {"requirements": ["C19/generated_lifecycle"]},
        },
        "stages": {
            "B": {"B11": {"status": "VERIFIED", "evidence": ["test"], "deferred_requirements": ["E33/live_restore"]}},
            "E": {"E33": {"status": "DEFERRED", "actionable": False, "deferred_to": "phase6_future"}},
        },
    }
    assert check(value) == (0, [])


def test_gate_checker_derives_phase6_statuses_and_rejects_manual_false_positive():
    value = {
        "schema_version": 4,
        "phase6_entry": "ALL_VERIFIED",
        "phase6_foundation_status": "VERIFIED",
        "phase6_operational_status": "VERIFIED",
        "phase_classification": {
            "phase3_5": {"gates": []},
            "phase4": {"gates": []},
            "phase5": {"gates": []},
            "phase6_foundation": {"gates": ["F/F6A"]},
            "phase6_operational": {"gates": ["G/G6O1"]},
            "phase6_future": {"requirements": []},
            "phase7_future": {"requirements": []},
        },
        "stages": {
            "F": {"F6A": {"status": "VERIFIED", "evidence": ["test"]}},
            "G": {"G6O1": {"status": "TODO"}},
        },
    }
    try:
        check(value)
    except ValueError as exc:
        assert "phase6_operational_status" in str(exc)
    else:
        raise AssertionError("manual phase6 status must match the derived gate status")
