from datetime import datetime, timezone, timedelta

import pytest

from src.dev_agent.scheduler.queue import DurableQueue, StaleLease, QueueEmpty
from src.dev_agent.scheduler.worker import WorkerRunner
from src.dev_agent.domain.protocol import Task
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime


def test_queue_survives_restart_and_claims_once(tmp_path):
    path = tmp_path / "queue.sqlite3"
    queue = DurableQueue(path)
    queue.enqueue("task-1", priority=10)
    first = queue.claim("worker-a", lease_seconds=30)
    assert first.task_id == "task-1"
    reopened = DurableQueue(path)
    with pytest.raises(QueueEmpty):
        reopened.claim("worker-b", lease_seconds=30)


def test_expired_lease_can_be_reclaimed_but_stale_worker_is_fenced(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("task-1")
    first = queue.claim("worker-a", lease_seconds=1)
    expired_at = first.lease_until + timedelta(seconds=1)
    second = queue.claim("worker-b", now=expired_at, lease_seconds=30)
    assert second.lease_owner == "worker-b"
    with pytest.raises(StaleLease):
        queue.complete("task-1", worker_id="worker-a", state_version=first.state_version)
    queue.complete("task-1", worker_id="worker-b", state_version=second.state_version)
    assert queue.snapshot("task-1").state == "completed"


def test_queue_orders_priority_and_tracks_attempts(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("low", priority=1)
    queue.enqueue("high", priority=9)
    item = queue.claim("worker", lease_seconds=30)
    assert item.task_id == "high"
    queue.fail("high", worker_id="worker", state_version=item.state_version, retry=True)
    retry = queue.claim("worker-2", lease_seconds=30)
    assert retry.task_id == "high"
    assert retry.attempts == 2


def test_worker_claims_durable_task_runs_controller_and_completes_queue_item(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="worker task")
    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo", handler=lambda args: args))
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        result = WorkerRunner(queue, Controller(FakeProvider(), ToolRuntime(registry), store), worker_id="worker-a").run_once()
    assert result is not None and result.status.value == "completed"
    assert queue.snapshot(task.task_id).state == "completed"
