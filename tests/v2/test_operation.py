import json
from datetime import datetime, timezone

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


def test_start_once_runs_the_bounded_maintenance_boundary(tmp_path, monkeypatch):
    config = _config(tmp_path)
    task = OperationService.submit(config, "run after maintenance")
    calls = []

    with OperationService.open(config) as service:
        monkeypatch.setattr(
            service,
            "maintenance_tick",
            lambda **kwargs: calls.append(kwargs) or [],
        )
        result = service.start(once=True)

    assert result is not None and result.task_id == task.task_id
    assert calls == [{"probe": None}]


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


def test_process_restart_resumes_durable_queue_and_leaves_terminal_state(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "resume after operation process restart")

    # The first service instance represents the process that accepted the
    # Task.  It closes before a Worker claim, so the second instance must use
    # only the durable Task and Queue rows.
    with OperationService.open(config):
        pass

    with OperationService.open(config) as restarted:
        result = restarted.start(once=True)

    assert result is not None and result.task_id == task.task_id
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.COMPLETED.value
    assert status["queue_state"] == "completed"
    assert status["last_event"]["event_type"] == "task.completed"


def test_process_restart_preserves_waiting_reconciliation_in_operation_status(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state import SQLiteStateStore

    config = _config(tmp_path)
    task = Task(objective="preserve external uncertainty", status=TaskStatus.WAITING_RECONCILIATION)
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

    with OperationService.open(config) as service:
        result = service.start(once=True)

    assert result is not None and result.status is TaskStatus.WAITING_RECONCILIATION
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.WAITING_RECONCILIATION.value
    assert status["queue_state"] == "waiting"
    assert status["reconciliation"] is True


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


def test_durable_stop_signal_stops_a_foreground_loop_from_another_process(tmp_path):
    from threading import Thread
    from time import sleep
    from src.dev_agent.operation import OperationControl

    config = _config(tmp_path)
    result_box = []
    with OperationService.open(config) as service:
        runner = Thread(target=lambda: result_box.append(service.start()), daemon=True)
        runner.start()
        sleep(0.05)
        external_control = OperationControl(config.queue_path)
        try:
            external_control.request_stop()
        finally:
            external_control.close()
        runner.join(2)

    assert not runner.is_alive()
    assert result_box and result_box[0]["stopped"] is True


def test_status_is_json_serializable(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "json status")

    status = OperationService.read_status(config, task.task_id)

    json.dumps(status, ensure_ascii=False)


def test_operation_maintenance_tick_requalifies_due_quota_and_wakes_queue(tmp_path):
    from src.dev_agent.scheduler.quota import QuotaWakeScheduler

    config = _config(tmp_path)
    task = OperationService.submit(config, "wait for quota recovery")
    with OperationService.open(config) as service:
        service.ledger.register_resource(
            "fake:default",
            provider_id="fake",
            provider_binding_id="fake:default",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            quota_domain="fake-domain",
            metadata={"provider_binding_id": "fake:default", "model_id": "deterministic", "intelligence_tier": "L1"},
            intelligence_tier="L1",
        )
        service.ledger.observe_quota(
            "fake:default",
            unit="requests",
            metric="rpm",
            window="minute",
            request_limit=10,
            request_remaining=0,
            blocked_until="2026-09-10T11:59:00+00:00",
            block_reason="rate_limit",
            observed_at="2026-09-10T11:00:00+00:00",
        )
        item = service.queue.claim("quota-maintenance-worker", lease_seconds=30)
        QuotaWakeScheduler(service.ledger, service.queue).park(
            task.task_id,
            worker_id="quota-maintenance-worker",
            state_version=item.state_version,
            wake_at=datetime(2026, 9, 10, 11, 59, tzinfo=timezone.utc),
        )
        calls = []

        results = service.maintenance_tick(
            now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            max_probes=1,
            probe=lambda resource_id, domain: calls.append((resource_id, domain)) or {
                "unit": "requests",
                "metric": "rpm",
                "window": "minute",
                "request_limit": 10,
                "request_remaining": 9,
            },
        )

        assert results[0]["status"] == "requalified"
        assert calls == [("fake:default", "fake-domain")]
        assert service.ledger.get_quota_observation("fake:default")["block_reason"] is None
        assert results[0]["woken_tasks"] == 1


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
