import json
from pathlib import Path

from recovery.diagnose import run_diagnostics
from recovery.validate_state import validate_state


def test_recovery_diagnostics_are_read_only_and_network_free():
    root = Path(__file__).parents[2]
    checks = run_diagnostics(root)
    assert checks
    assert all(item.ok for item in checks)


def test_recovery_state_validator_checks_minimum_shape(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"task_id": "t", "status": "queued"}), encoding="utf-8")
    assert validate_state(state_path) == (True, "state shape is readable")

    state_path.write_text(json.dumps({"status": "queued"}), encoding="utf-8")
    ok, message = validate_state(state_path)
    assert not ok
    assert "task_id" in message
