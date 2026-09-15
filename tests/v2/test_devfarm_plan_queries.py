from __future__ import annotations

import pytest

from scripts.devfarm import DevFarmError
from scripts.devfarm_plan_queries import (
    artifact_reference_paths,
    require_approved_review_decision,
    require_task,
)


def test_require_task_returns_only_matching_task() -> None:
    plan = {
        "tasks": [
            {"task_id": "task-a", "owner": "worker"},
            {"task_id": "task-b", "owner": "codex"},
        ]
    }

    assert require_task(plan, "task-b")["owner"] == "codex"


def test_require_task_rejects_missing_or_malformed_task_list() -> None:
    with pytest.raises(DevFarmError, match="plan tasks are missing"):
        require_task({}, "task-a")
    with pytest.raises(DevFarmError, match="repair task does not exist"):
        require_task({"tasks": [{"task_id": "task-a"}]}, "task-b")


def test_require_approved_review_decision_is_identity_bound() -> None:
    plan = {
        "review_decisions": [
            {
                "decision_id": "decision-1",
                "task_id": "task-a",
                "attempt_id": "attempt-1",
                "decision": "APPROVE_INTEGRATION",
            }
        ]
    }

    decision = require_approved_review_decision(
        plan,
        decision_id="decision-1",
        task_id="task-a",
        attempt_id="attempt-1",
    )
    assert decision["decision"] == "APPROVE_INTEGRATION"

    with pytest.raises(DevFarmError, match="matching durable repair review decision"):
        require_approved_review_decision(
            plan,
            decision_id="decision-1",
            task_id="task-a",
            attempt_id="attempt-2",
        )


def test_artifact_reference_paths_are_bounded_to_path_references() -> None:
    packet = {
        "artifact_refs": [
            {"kind": "patch", "path": "patch.diff"},
            {"kind": "verification", "path": "verification.json"},
            {"kind": "inline", "value": "not a path"},
        ]
    }

    assert artifact_reference_paths(packet) == {"patch.diff", "verification.json"}

    with pytest.raises(DevFarmError, match="artifact references are missing"):
        artifact_reference_paths({})
