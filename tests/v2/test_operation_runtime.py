from __future__ import annotations

import json

import pytest

from src.dev_agent.domain.protocol import TaskStatus
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.operation_runtime import (
    RuntimeCoordinator,
    RuntimeCoordinatorError,
    read_runtime_health,
)


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="runtime-test-worker",
        idle_sleep_seconds=0.01,
    )


def test_runtime_coordinator_runs_existing_operation_boundary_once(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "run through the persistent runtime coordinator")

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        result = runtime.run_once()
        health = runtime.health()

    assert result is not None
    assert result.task_id == task.task_id
    assert health["status"] == "READY"
    assert health["component"] == "operation_runtime"
    assert health["peer"]["role"] == "agent"
    assert health["peer"]["instance_id"] == "runtime-1"
    assert health["peer"]["generation"] == 1
    assert health["task_counts"][TaskStatus.COMPLETED.value] == 1


def test_runtime_coordinator_keeps_deterministic_local_resource_fresh_after_idle(tmp_path):
    config = _config(tmp_path)

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        runtime.operation.ledger.observe(
            "fake:default",
            available=1,
            health="degraded",
            confidence=0.0,
            observed_at="2020-01-01T00:00:00+00:00",
        )
        task = OperationService.submit(config, "resume a local deterministic task after idle")

        result = runtime.run_once()

    assert result is not None
    assert result.task_id == task.task_id
    assert result.status is TaskStatus.COMPLETED


def test_runtime_coordinator_serve_is_bounded_and_idle_light(tmp_path):
    config = _config(tmp_path)
    sleeps = []

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        result = runtime.serve(max_cycles=3, wait_fn=sleeps.append)

    assert result["mode"] == "serve"
    assert result["cycles"] == 3
    assert result["bounded"] is True
    assert sleeps == [0.01, 0.01]
    assert result["last_task_id"] is None


def test_runtime_coordinator_wakes_for_task_submitted_during_idle(tmp_path):
    config = _config(tmp_path)
    submitted = []
    sleeps = []

    def wait_for_next_cycle(delay):
        sleeps.append(delay)
        if not submitted:
            submitted.append(
                OperationService.submit(
                    config,
                    "run a durable task submitted while the coordinator is idle",
                )
            )

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        result = runtime.serve(max_cycles=2, wait_fn=wait_for_next_cycle)

    assert len(submitted) == 1
    assert result["status"] == "CYCLE_LIMIT"
    assert result["cycles"] == 2
    assert result["last_task_id"] == submitted[0].task_id
    assert sleeps == [0.01]
    assert OperationService.read_status(config, submitted[0].task_id)["state"] == TaskStatus.COMPLETED.value


def test_runtime_coordinator_restart_preserves_durable_waiting_state(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state import SQLiteStateStore

    config = _config(tmp_path)
    task = Task(objective="preserve waiting state across coordinator restart", status=TaskStatus.WAITING_RECONCILIATION)
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(task)
        store.checkpoint(
            task_id=task.task_id,
            step_id="provider-step",
            phase="waiting_reconciliation",
            state={"provider_reconciliation": {"status": "unknown"}},
        )
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(task.task_id)

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        result = runtime.run_once()
        assert result is not None
        assert result.status is TaskStatus.WAITING_RECONCILIATION

    health = read_runtime_health(config.data_dir, role="agent", instance_id="runtime-1")
    assert health["task_counts"][TaskStatus.WAITING_RECONCILIATION.value] == 1
    assert health["peer"]["generation"] == 1

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as restarted:
        assert restarted.peer.generation == 2
        restarted_health = restarted.health()

    assert restarted_health["peer"]["generation"] == 2
    assert restarted_health["task_counts"][TaskStatus.WAITING_RECONCILIATION.value] == 1


def test_runtime_coordinator_continues_ready_work_around_reconciliation_wait(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state import SQLiteStateStore

    config = _config(tmp_path)
    waiting = Task(
        objective="keep an unknown external effect parked",
        status=TaskStatus.WAITING_RECONCILIATION,
    )
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(waiting)
        store.checkpoint(
            task_id=waiting.task_id,
            step_id="provider-step",
            phase="waiting_reconciliation",
            state={"provider_reconciliation": {"status": "unknown"}},
        )
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(waiting.task_id, priority=10)
    ready = OperationService.submit(config, "continue unrelated ready work", priority=0)

    with RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1") as runtime:
        first = runtime.run_once()
        second = runtime.run_once()

    assert first is not None and first.task_id == waiting.task_id
    assert first.status is TaskStatus.WAITING_RECONCILIATION
    assert second is not None and second.task_id == ready.task_id
    assert second.status is TaskStatus.COMPLETED
    waiting_status = OperationService.read_status(config, waiting.task_id)
    assert waiting_status["state"] == TaskStatus.WAITING_RECONCILIATION.value
    assert waiting_status["queue_state"] == "waiting"
    assert waiting_status["reconciliation"] is True


def test_runtime_coordinator_restarts_durable_phase8_plan_without_duplicate_attempts(tmp_path):
    from src.dev_agent.domain.protocol import Task, TaskType
    from src.dev_agent.intelligence.planner import (
        ChildTaskProposal,
        PlannerDependencyType,
        RootPlanningProposal,
    )
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state import SQLiteStateStore

    config = _config(tmp_path)
    root = Task(
        objective="durable Phase 8 root",
        status=TaskStatus.PLANNING,
        task_type=TaskType.REASONING,
    )
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(root)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        proposal_id="phase8-durable-proposal",
        rationale="two independent implementers followed by integrated continuation",
        children=(
            ChildTaskProposal(
                child_key="implementer-a",
                objective="complete independent implementation A",
                task_type=TaskType.WORKER,
                required_capabilities=("coding",),
            ),
            ChildTaskProposal(
                child_key="implementer-b",
                objective="complete independent implementation B",
                task_type=TaskType.WORKER,
                required_capabilities=("coding",),
            ),
            ChildTaskProposal(
                child_key="dependent-continuation",
                objective="continue after both implementations are integrated",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("implementer-a", "implementer-b"),
                dependency_types={
                    "implementer-a": PlannerDependencyType.CODE_INTEGRATED,
                    "implementer-b": PlannerDependencyType.CODE_INTEGRATED,
                },
            ),
        ),
    )

    with RuntimeCoordinator.open(config, revision="phase8-test-revision", instance_id="runtime-1") as runtime:
        children = runtime.operation.apply_planning_proposal(proposal)
        assert [child.status for child in children] == [
            TaskStatus.QUEUED,
            TaskStatus.QUEUED,
            TaskStatus.WAITING_DEPENDENCY,
        ]
        first = runtime.run_once()
        second = runtime.run_once()
        assert {first.task_id, second.task_id} == {
            children[0].task_id,
            children[1].task_id,
        }
        for child in children[:2]:
            persisted = runtime.operation.store.load_task(child.task_id)
            assert persisted is not None
            persisted.metadata["integration_status"] = "INTEGRATED"
            persisted.metadata["integration_revision"] = "a" * 40
            runtime.operation.store.save_task(persisted)
        released = runtime.operation.release_planner_dependencies(
            proposal_id=proposal.proposal_id,
        )
        assert [task.task_id for task in released] == [children[2].task_id]

    with RuntimeCoordinator.open(config, revision="phase8-test-revision", instance_id="runtime-1") as restarted:
        reused = restarted.operation.apply_planning_proposal(proposal)
        assert [task.task_id for task in reused] == [task.task_id for task in children]
        continuation = restarted.run_once()
        assert continuation is not None
        assert continuation.task_id == children[2].task_id
        assert continuation.status is TaskStatus.COMPLETED

    for task in children:
        status = OperationService.read_status(config, task.task_id)
        assert status["claim_count"] == 1
        assert status["execution_attempts"] == 1
    with DurableQueue(config.queue_path) as queue:
        assert queue.snapshot(children[2].task_id).state == "completed"


def test_stale_runtime_generation_stops_itself_without_global_stop(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "do not let stale runtime claim this task")

    first = RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1")
    second = RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1")
    try:
        assert first.peer.generation == 1
        assert second.peer.generation == 2
        with pytest.raises(RuntimeCoordinatorError, match="stale runtime peer"):
            first.run_once()
        assert second.operation.control.stop_requested() is False
        result = second.run_once()
        assert result is not None and result.task_id == task.task_id
    finally:
        first.close()
        second.close()


def test_stale_runtime_cannot_clear_current_generation_stop_request(tmp_path):
    from src.dev_agent.operation import OperationControl

    config = _config(tmp_path)
    first = RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1")
    second = RuntimeCoordinator.open(config, revision="test-revision", instance_id="runtime-1")
    control = OperationControl(config.queue_path)
    try:
        control.request_stop()
        with pytest.raises(RuntimeCoordinatorError, match="stale runtime peer"):
            first.run_once()
        assert second.operation.control.stop_requested() is True
    finally:
        control.close()
        first.close()
        second.close()


def test_runtime_health_is_bounded_json_and_does_not_open_provider(tmp_path):
    config = _config(tmp_path)
    OperationService.submit(config, "health projection")

    payload = read_runtime_health(config.data_dir, role="agent", instance_id="runtime-1")

    assert payload["status"] == "NOT_ATTACHED"
    assert payload["component"] == "operation_runtime"
    assert payload["task_counts"][TaskStatus.QUEUED.value] == 1
    assert payload["external_network"] == "owned_by_operation_provider_boundary"
    json.dumps(payload, ensure_ascii=False)
