from __future__ import annotations

from dev_agent.domain.protocol import Task, TaskStatus
from dev_agent.scheduler.queue import DurableQueue
from dev_agent.scheduler.worker import WorkerRunner
from dev_agent.state.sqlite_store import SQLiteStateStore


class _WaitingController:
    def __init__(self, store, status: TaskStatus, metadata=None):
        self.store = store
        self.status = status
        self.metadata = dict(metadata or {})

    def resume(self, task_id, *, execution_context=None):
        task = self.store.load_task(task_id)
        task.status = self.status
        task.metadata.update(self.metadata)
        self.store.save_task(task)
        return task


def test_worker_persists_typed_event_wait_reason_for_legacy_reconciliation(tmp_path) -> None:
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="wait for reconciliation")
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        result = WorkerRunner(
            queue,
            _WaitingController(store, TaskStatus.WAITING_RECONCILIATION),
            worker_id="wait-worker",
        ).run_once()

    assert result is not None
    parked = queue.snapshot(task.task_id)
    assert parked.state == "waiting"
    assert parked.wake_reason == "reconciliation"


def test_worker_uses_deadline_from_typed_user_delay_condition(tmp_path) -> None:
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="wait until the deadline")
    metadata = {
        "wait_reason": "user_delay",
        "wait_until_epoch": 2_000_000_000.0,
        "wait_condition": {
            "kind": "user_delay",
            "subject": "task",
            "created_from": "controller:user_delay",
            "wake_authority": "runtime_maintenance",
            "wake_predicate": "wake_at_reached",
            "deadline_epoch": 2_000_000_000.0,
            "replay_policy": "resume_checkpoint",
            "queue_reason": "user_delay",
        },
    }
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        result = WorkerRunner(
            queue,
            _WaitingController(store, TaskStatus.WAITING_DEPENDENCY, metadata),
            worker_id="wait-worker",
        ).run_once()

    assert result is not None
    parked = queue.snapshot(task.task_id)
    assert parked.state == "waiting"
    assert parked.wake_reason == "user_delay"
    assert parked.wake_at.timestamp() == 2_000_000_000.0

