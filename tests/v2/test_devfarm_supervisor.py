from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
import pytest

from scripts.devfarm_commander import validate_plan
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    record_wake,
    select_heartbeat_cadence,
)
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from scripts.devfarm_commander import create_plan


def _plan(**overrides):
    value = {
        "run_id": "supervisor-test-001",
        "objective": "test supervisor metadata",
        "base_revision": "abc123",
        "tasks": [
            {
                "task_id": "task-001",
                "owner": "codex",
                "ownership": [],
                "dependencies": [],
                "assignment": {"owner": "codex"},
            }
        ],
        "ownership": [{"task_id": "task-001", "paths": []}],
        "assignments": [{"task_id": "task-001", "owner": "codex"}],
        "dependencies": [{"task_id": "task-001", "depends_on": []}],
        "results": [],
    }
    value.update(overrides)
    return value


def test_commander_plan_preserves_bounded_supervisor_metadata():
    normalized = validate_plan(
        _plan(
            supervisor={
                "status": "WAITING_FOR_WORKER",
                "roadmap_reference": {"path": "docs/V2_EXECUTION_PLAN.md"},
                "roadmap_position": "handoff",
                "cadence_minutes": 5,
                "unchanged_check_limit": 3,
                "next_action": "wait_for_worker",
            }
        )
    )

    assert normalized["supervisor"]["status"] == "WAITING_FOR_WORKER"
    assert normalized["supervisor"]["cadence_minutes"] == 5
    assert normalized["supervisor"]["unchanged_check_limit"] == 3
    assert normalized["supervisor"]["metrics"]["codex_wake_count"] == 0


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [(300, 1), (301, 5), (1200, 5), (1201, 10), (3600, 10), (3601, 15), (None, 15)],
)
def test_heartbeat_cadence_uses_bounded_time_bands(remaining, expected):
    assert select_heartbeat_cadence(remaining) == expected


def test_wake_event_is_deduplicated_without_raw_worker_output():
    metadata = normalize_supervisor_metadata()
    updated = record_wake(
        metadata,
        kind="HOST_VERIFIED_RESULT_READY",
        task_id="task-001",
        attempt_id="attempt-001",
        digest="a" * 64,
    )
    repeated = record_wake(
        updated,
        kind="HOST_VERIFIED_RESULT_READY",
        task_id="task-001",
        attempt_id="attempt-001",
        digest="a" * 64,
    )

    assert len(updated["wake_events"]) == 1
    assert repeated == updated
    assert "raw_output" not in updated["wake_events"][0]


def test_supervisor_metadata_rejects_raw_conversation_payloads():
    with pytest.raises(ValueError, match="raw"):
        normalize_supervisor_metadata({"wake_events": [{"kind": "x", "raw_output": "secret"}]})


def test_unchanged_heartbeat_escalates_after_bounded_checks():
    metadata = normalize_supervisor_metadata({"cadence_minutes": 1, "unchanged_check_limit": 2})

    first = advance_heartbeat(metadata, unchanged=True)
    second = advance_heartbeat(first, unchanged=True)
    third = advance_heartbeat(second, unchanged=True)

    assert first["cadence_minutes"] == 1
    assert second["cadence_minutes"] == 5
    assert third["cadence_minutes"] == 10


def test_supervised_run_creates_durable_wait_metadata_without_integrating(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")

    created = runner.create(roadmap_reference={"path": "docs/V2_EXECUTION_PLAN.md"}, expected_remaining_seconds=30)
    step = runner.advance(providers={})

    assert created.status == "ACTIVE"
    assert created.cadence_minutes == 1
    assert step.plan_status == "READY"
    assert step.status == "ACTIVE"
    assert step.next_action == "advance"
    assert step.metrics["worker_success_count"] == 0
    assert all(task["status"] != "INTEGRATED" for task in runner.plan()["tasks"])


def test_supervised_run_waits_without_llm_polling_until_intervention(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create(expected_remaining_seconds=30)
    baseline = runner.status()
    calls = []
    sleeps = []

    def fake_advance(**_kwargs):
        calls.append(True)
        if len(calls) == 1:
            return replace(baseline, status="WAITING_FOR_WORKER", next_action="wait_for_worker")
        return replace(baseline, status="REVIEWING", next_action="review_host_verified")

    runner.advance = fake_advance
    monotonic_values = iter((0.0, 0.0, 0.0))
    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: next(monotonic_values),
    )

    assert step.status == "REVIEWING"
    assert len(calls) == 2
    assert sleeps == [60.0]


def test_supervisor_cli_is_invokable_as_a_script():
    result = subprocess.run(
        [sys.executable, "scripts/devfarm_supervisor.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "bounded Codex supervisor" in result.stdout
