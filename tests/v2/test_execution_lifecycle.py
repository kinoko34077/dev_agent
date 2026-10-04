from __future__ import annotations

from dataclasses import replace
import json

import pytest

from src.dev_agent.domain.execution import (
    CanonicalExecutionBinding,
    ExecutionLifecycleStage,
    devfarm_lifecycle_stage,
    operation_lifecycle_stage,
)
from src.dev_agent.domain.protocol import ProtocolError, TaskStatus
from scripts.devfarm_plan_state import (
    CommanderPlanStore,
    observe_task_lifecycle,
    refresh_plan,
    set_task_lifecycle,
)


def test_operation_and_devfarm_statuses_project_to_one_lifecycle_vocabulary():
    assert operation_lifecycle_stage(TaskStatus.WAITING_DEPENDENCY, handoff_pending=True) is ExecutionLifecycleStage.HANDOFF_PENDING
    assert operation_lifecycle_stage(TaskStatus.RUNNING) is ExecutionLifecycleStage.ATTEMPT_ACTIVE
    assert devfarm_lifecycle_stage("DISPATCHED") is ExecutionLifecycleStage.ATTEMPT_ACTIVE
    assert devfarm_lifecycle_stage("PROPOSED") is ExecutionLifecycleStage.RESULT_RECEIVED
    assert devfarm_lifecycle_stage("HOST_VERIFIED") is ExecutionLifecycleStage.HOST_VERIFIED
    assert devfarm_lifecycle_stage("INTEGRATED") is ExecutionLifecycleStage.INTEGRATED


def test_canonical_execution_binding_is_stable_and_replay_idempotent():
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_backend_task("devfarm-task-1")
    replay = CanonicalExecutionBinding.from_dict(binding.to_dict())

    assert replay == binding
    assert binding.merge_observation(replay) == binding


def test_canonical_execution_binding_rejects_conflicting_backend_identity():
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_backend_task("devfarm-task-1")
    conflicting = replace(binding, backend_task_id="devfarm-task-2")

    with pytest.raises(ProtocolError, match="backend task identity"):
        binding.merge_observation(conflicting)


def test_canonical_execution_binding_rejects_regression_after_integration():
    integrated = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_backend_task("devfarm-task-1").with_stage(ExecutionLifecycleStage.INTEGRATED)
    regressed = integrated.with_stage(ExecutionLifecycleStage.ATTEMPT_ACTIVE)

    with pytest.raises(ProtocolError, match="terminal lifecycle"):
        integrated.merge_observation(regressed)


def test_canonical_execution_binding_roundtrips_attempt_and_authority_facts():
    binding = (
        CanonicalExecutionBinding.for_child(
            logical_execution_id="operation-child-1",
            proposal_id="proposal-1",
            child_key="worker-a",
            executor_kind="devfarm_worker",
        )
        .with_backend_task("devfarm-task-1")
        .with_observation(
            stage=ExecutionLifecycleStage.RESULT_RECEIVED,
            attempt_id="attempt-1",
            result_ref=".devfarm/results/worker-a/attempts/attempt-1/result.json",
            verification_id="verification-1",
            review_decision_id="review-1",
            integration_revision="a" * 40,
            dependency_satisfied=True,
        )
    )

    restored = CanonicalExecutionBinding.from_dict(binding.to_dict())

    assert restored == binding
    assert restored.merge_observation(restored) == restored


def test_canonical_execution_binding_rejects_conflicting_attempt_facts():
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_backend_task("devfarm-task-1").with_observation(
        stage=ExecutionLifecycleStage.RESULT_RECEIVED,
        attempt_id="attempt-1",
        result_ref=".devfarm/results/worker-a/result.json",
    )
    conflicting = binding.with_observation(
        stage=ExecutionLifecycleStage.RESULT_RECEIVED,
        attempt_id="attempt-1",
        result_ref=".devfarm/results/worker-a/other-result.json",
    )

    with pytest.raises(ProtocolError, match="result reference"):
        binding.merge_observation(conflicting)


def test_canonical_execution_binding_accepts_a_fresh_nonterminal_attempt():
    failed = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_backend_task("devfarm-task-1").with_observation(
        stage=ExecutionLifecycleStage.FAILED,
        attempt_id="attempt-1",
        result_ref=".devfarm/results/worker-a/attempts/attempt-1/result.json",
    )
    retry = failed.for_retry().with_observation(
        stage=ExecutionLifecycleStage.ATTEMPT_ACTIVE,
        attempt_id="attempt-2",
    )

    merged = failed.merge_observation(retry)

    assert merged.stage is ExecutionLifecycleStage.ATTEMPT_ACTIVE
    assert merged.attempt_id == "attempt-2"
    assert merged.result_ref is None


def test_canonical_execution_binding_allows_dependency_satisfaction_to_advance():
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="operation-child-1",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    ).with_observation(
        stage=ExecutionLifecycleStage.READY,
        dependency_satisfied=False,
    )
    observed = binding.with_observation(
        stage=ExecutionLifecycleStage.ATTEMPT_ACTIVE,
        dependency_satisfied=True,
    )

    merged = binding.merge_observation(observed)

    assert merged.dependency_satisfied is True


def test_devfarm_status_is_a_projection_of_canonical_lifecycle_facts():
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="worker-a",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="devfarm_worker",
    )
    task = {
        "task_id": "worker-a",
        "status": "READY",
        "canonical_execution": binding.to_dict(),
    }

    set_task_lifecycle(task, "DISPATCHED")
    observe_task_lifecycle(
        task,
        stage=ExecutionLifecycleStage.RESULT_RECEIVED,
        attempt_id="attempt-1",
        result_ref=".devfarm/results/worker-a/result.json",
    )

    assert task["status"] == "DISPATCHED"
    assert task["canonical_execution"]["stage"] == ExecutionLifecycleStage.RESULT_RECEIVED.value
    assert task["canonical_execution"]["attempt_id"] == "attempt-1"
    assert task["canonical_execution"]["result_ref"].endswith("result.json")


def test_dependency_release_records_canonical_satisfaction():
    blocked = CanonicalExecutionBinding.for_child(
        logical_execution_id="dependent",
        proposal_id="proposal-1",
        child_key="dependent",
        executor_kind="operation",
    ).with_observation(
        stage=ExecutionLifecycleStage.BLOCKED,
        dependency_satisfied=False,
    )
    plan = {
        "run_id": "dependency-canonical-run",
        "objective": "release a dependency after its canonical integration fact",
        "base_revision": "a" * 40,
        "tasks": [
            {
                "task_id": "dependency",
                "owner": "codex",
                "status": "INTEGRATED",
            },
            {
                "task_id": "dependent",
                "owner": "codex",
                "status": "BLOCKED",
                "block_reason": "dependency_failed",
                "dependencies": ["dependency"],
                "canonical_execution": blocked.to_dict(),
            },
        ],
    }

    refreshed = refresh_plan(plan)

    task = refreshed["tasks"][1]
    assert task["status"] == "READY"
    assert task["canonical_execution"]["stage"] == ExecutionLifecycleStage.READY.value
    assert task["canonical_execution"]["dependency_satisfied"] is True


def test_waiting_dependency_wake_starts_ready_canonical_attempt():
    waiting = CanonicalExecutionBinding.for_child(
        logical_execution_id="dependent-waiting",
        proposal_id="proposal-1",
        child_key="dependent",
        executor_kind="operation",
    ).with_observation(
        stage=ExecutionLifecycleStage.WAITING,
        dependency_satisfied=False,
    )
    plan = {
        "run_id": "waiting-dependency-wake-run",
        "objective": "wake one canonical dependent execution",
        "base_revision": "a" * 40,
        "tasks": [
            {
                "task_id": "dependency",
                "owner": "codex",
                "status": "INTEGRATED",
            },
            {
                "task_id": "dependent",
                "owner": "codex",
                "status": "PLANNED",
                "dependencies": ["dependency"],
                "canonical_execution": waiting.to_dict(),
            },
        ],
    }

    refreshed = refresh_plan(plan)

    task = refreshed["tasks"][1]
    assert task["status"] == "READY"
    assert task["canonical_execution"]["stage"] == ExecutionLifecycleStage.READY.value
    assert task["canonical_execution"]["dependency_satisfied"] is True


def test_plan_reload_projects_compatibility_status_from_canonical_fact(tmp_path):
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="worker-a",
        proposal_id="proposal-1",
        child_key="worker-a",
        executor_kind="operation",
    ).with_observation(
        stage=ExecutionLifecycleStage.HOST_VERIFIED,
        attempt_id="attempt-1",
        result_ref=".devfarm/results/worker-a/result.json",
        verification_id="verification-1",
    )
    store = CommanderPlanStore(tmp_path)
    store.create(
        {
            "run_id": "canonical-reload-run",
            "objective": "reconstruct the compatibility projection after restart",
            "base_revision": "a" * 40,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "codex",
                    "status": "HOST_VERIFIED",
                    "canonical_execution": binding.to_dict(),
                }
            ],
        }
    )

    path = store.path_for("canonical-reload-run")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["tasks"][0]["status"] = "READY"
    raw["status"] = "READY"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    restored = store.load("canonical-reload-run")

    assert restored["status"] == "HOST_VERIFIED"
    assert restored["tasks"][0]["status"] == "HOST_VERIFIED"
    assert restored["tasks"][0]["canonical_execution"]["verification_id"] == "verification-1"
