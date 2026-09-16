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
