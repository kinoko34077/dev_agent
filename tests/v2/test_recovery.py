import json
from pathlib import Path
import subprocess

from recovery.diagnose import run_diagnostics
from recovery.git_recovery import inspect_git, load_last_known_good, plan_rollback, record_last_known_good, rollback_to_last_known_good, create_repair_branch
from recovery.test_results import parse_junit_report, validate_junit_report
from recovery.validate_state import validate_state
import pytest


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


def test_recovery_validates_persisted_junit_report(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1" time="0.25" /></testsuites>', encoding="utf-8")
    parsed = parse_junit_report(report)
    assert parsed.to_dict() == {"tests": 3, "failures": 1, "errors": 0, "skipped": 1, "duration_seconds": 0.25, "passed": 1}
    assert validate_junit_report(report)[0]
    checks = run_diagnostics(Path(__file__).parents[2], test_report=report)
    assert next(item for item in checks if item.name == "persisted_test_report").ok


def test_recovery_rejects_invalid_persisted_junit_report(tmp_path):
    report = tmp_path / "invalid-junit.xml"
    report.write_text('<testsuites><testsuite tests="1" failures="2" /></testsuites>', encoding="utf-8")
    ok, detail = validate_junit_report(report)
    assert not ok
    assert "exceed" in detail


def test_git_recovery_records_lkg_and_requires_explicit_mutation(tmp_path):
    root = Path(__file__).parents[2]
    snapshot = inspect_git(root)
    metadata = tmp_path / "last-known-good.json"
    record_last_known_good(root, metadata, commit=snapshot.commit)
    loaded = load_last_known_good(metadata)
    assert loaded["commit"] == snapshot.commit
    plan = plan_rollback(root, metadata)
    assert plan.target_commit == snapshot.commit
    with pytest.raises(PermissionError):
        rollback_to_last_known_good(root, metadata)
    with pytest.raises(PermissionError):
        create_repair_branch(root, metadata, "repair/test")


def test_git_recovery_refuses_dirty_reset_then_applies_explicit_opt_in(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Recovery Test")
    tracked = root / "tracked.txt"
    tracked.write_text("first\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "first")
    first = git("rev-parse", "HEAD")
    tracked.write_text("second\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "second")
    second = git("rev-parse", "HEAD")
    metadata = tmp_path / "last-known-good.json"
    record_last_known_good(root, metadata, commit=first, require_clean=True)

    tracked.write_text("uncommitted\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dirty worktree"):
        rollback_to_last_known_good(root, metadata, allow_destructive=True)
    assert inspect_git(root).commit == second

    restored = rollback_to_last_known_good(root, metadata, allow_destructive=True, allow_dirty=True)
    assert restored.commit == first
    assert restored.branch is None
    assert not restored.dirty
    assert tracked.read_text(encoding="utf-8") == "first\n"
    assert create_repair_branch(root, metadata, "repair/from-lkg", allow_write=True) == "repair/from-lkg"
