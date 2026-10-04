from __future__ import annotations

from dataclasses import replace

import pytest

from src.dev_agent.domain.execution import (
    CanonicalExecutionBinding,
    ExecutionLifecycleStage,
    devfarm_lifecycle_stage,
    operation_lifecycle_stage,
)
from src.dev_agent.domain.protocol import ProtocolError, TaskStatus


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
