import json

import pytest

from src.dev_agent.domain.protocol import TaskStatus
from src.dev_agent.operation import OperationConfig, OperationService


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="operation-test-worker",
        idle_sleep_seconds=0.01,
    )


def test_submit_is_durable_and_status_reads_queue_and_events(tmp_path):
    config = _config(tmp_path)

    task = OperationService.submit(config, "persist this task", priority=4)

    status = OperationService.read_status(config, task.task_id)

    assert status["task_id"] == task.task_id
    assert status["state"] == TaskStatus.QUEUED.value
    assert status["queue_state"] == "queued"
    assert status["current_attempt"] == 0
    assert status["waiting"] is False
    assert status["completed"] is False


def test_start_once_uses_canonical_dispatcher_and_finishes_task(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "complete through the operation layer")

    with OperationService.open(config) as service:
        result = service.start(once=True)

    assert result is not None
    assert result.task_id == task.task_id
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.COMPLETED.value
    assert status["queue_state"] == "completed"
    assert status["completed"] is True
    assert status["selected_provider"] == "fake"
    assert status["selected_binding"] == "fake:default"
    assert status["last_event"]["event_type"] == "task.completed"


def test_restart_restores_queued_task_when_enqueue_was_interrupted(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "repair an interrupted enqueue")

    # Simulate the only partial-submit state the operation layer can safely
    # repair: durable Task exists while its queue row is absent.
    from src.dev_agent.scheduler.queue import DurableQueue

    with DurableQueue(config.queue_path) as queue:
        queue.connection.execute("DELETE FROM queue_items WHERE task_id=?", (task.task_id,))
        queue.connection.commit()
        with pytest.raises(KeyError):
            queue.snapshot(task.task_id)

    with OperationService.open(config) as service:
        restored = service.restore_queue()
        assert restored == [task.task_id]
        result = service.start(once=True)
        assert result is not None

    assert OperationService.read_status(config, task.task_id)["state"] == TaskStatus.COMPLETED.value


def test_stop_queued_task_is_cancelled_not_failed(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "cancel before execution")

    with OperationService.open(config) as service:
        status = service.stop(task.task_id)

    assert status["state"] == TaskStatus.CANCELLED.value
    assert status["queue_state"] == "cancelled"
    assert status["failed"] is False
    assert status["last_event"]["event_type"] == "task.cancelled"


def test_stop_requests_worker_shutdown_without_deleting_task(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "leave durable state after stop")

    with OperationService.open(config) as service:
        from threading import Event

        stop_event = Event()
        stop_event.set()
        result = service.start(stop_event=stop_event)

    assert result["stopped"] is True
    assert OperationService.read_status(config, task.task_id)["state"] == TaskStatus.QUEUED.value


def test_status_is_json_serializable(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "json status")

    status = OperationService.read_status(config, task.task_id)

    json.dumps(status, ensure_ascii=False)


def test_external_cancel_of_running_task_is_only_a_durable_request(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.runtime.controller import Controller
    from src.dev_agent.state import SQLiteStateStore
    from src.dev_agent.tools import ToolRegistry, ToolRuntime

    path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(path) as store:
        task = Task(objective="cancel from another operation process", status=TaskStatus.RUNNING)
        store.save_task(task)
        controller = Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store)

        requested = controller.cancel(task.task_id, reason="operator stop")

        assert requested.status == TaskStatus.RUNNING
        persisted = store.load_task(task.task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING
        assert persisted.metadata["cancellation_requested"] is True
        assert persisted.metadata["cancellation_reason"] == "operator stop"
