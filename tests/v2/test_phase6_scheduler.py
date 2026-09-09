from datetime import datetime, timezone, timedelta
import multiprocessing
import sqlite3
import time

import pytest

from src.dev_agent.scheduler.queue import DurableQueue, LeaseProof, MaintenanceMode, StaleLease, QueueEmpty
from src.dev_agent.scheduler.worker import WorkerRunner
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime


def test_queue_runs_ordered_migration_for_legacy_lease_schema(tmp_path):
    path = tmp_path / "legacy-queue.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE queue_items (task_id TEXT PRIMARY KEY, priority INTEGER NOT NULL, run_at REAL NOT NULL, state TEXT NOT NULL, lease_owner TEXT, lease_until REAL, state_version INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE scheduler_control (id INTEGER PRIMARY KEY CHECK (id=1), maintenance INTEGER NOT NULL DEFAULT 0);
        """
    )
    connection.commit()
    connection.close()

    queue = DurableQueue(path)
    columns = {row[1] for row in queue.connection.execute("PRAGMA table_info(queue_items)")}
    version = queue.connection.execute("SELECT value FROM scheduler_schema_meta WHERE key='schema_version'").fetchone()[0]

    assert {"lease_token", "max_attempts"} <= columns
    assert version == "3"


def _claim_in_process(path, worker_id, result_queue):
    queue = DurableQueue(path)
    try:
        result_queue.put(queue.claim(worker_id, lease_seconds=30).task_id)
    except QueueEmpty:
        result_queue.put(None)


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


def test_expired_lease_cannot_finish_without_reclaim(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("task-1")
    item = queue.claim("worker-a", lease_seconds=0.01)
    time.sleep(0.05)
    with pytest.raises(StaleLease):
        queue.complete("task-1", worker_id="worker-a", state_version=item.state_version)
    assert queue.snapshot("task-1").state == "leased"


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


def test_queue_fails_after_finite_default_attempts(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("bounded")
    for attempt in range(DurableQueue.DEFAULT_MAX_ATTEMPTS):
        item = queue.claim(f"worker-{attempt}", lease_seconds=30)
        queue.fail("bounded", worker_id=f"worker-{attempt}", state_version=item.state_version, retry=True)
        if attempt < DurableQueue.DEFAULT_MAX_ATTEMPTS - 1:
            assert queue.snapshot("bounded").state == "queued"
    assert queue.snapshot("bounded").state == "failed"
    assert queue.snapshot("bounded").attempts == DurableQueue.DEFAULT_MAX_ATTEMPTS


def test_expired_worker_crashes_are_finite_and_terminal(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("crash-loop", max_attempts=2)

    first = queue.claim("worker-a", lease_seconds=30)
    second = queue.claim("worker-b", now=first.lease_until.timestamp() + 1, lease_seconds=30)
    assert second.attempts == 2

    with pytest.raises(QueueEmpty):
        queue.claim("worker-c", now=second.lease_until.timestamp() + 1, lease_seconds=30)
    assert queue.snapshot("crash-loop").state == "failed"
    assert queue.snapshot("crash-loop").attempts == 2


def test_reap_expired_terminalizes_exhausted_crash_loop(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("reaped-loop", max_attempts=1)
    item = queue.claim("worker-a", lease_seconds=30)

    assert queue.reap_expired(now=item.lease_until.timestamp() + 1) == 1
    assert queue.snapshot("reaped-loop").state == "failed"
    with pytest.raises(QueueEmpty):
        queue.claim("worker-b")


def test_worker_uses_task_retry_limit_as_total_attempt_bound(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="bounded worker", limits={"max_retries": 1})

    class RetryingController:
        def __init__(self, store):
            self.store = store
            self.lease_guard = None
            self.lease_proof = None

        def resume(self, task_id):
            return self.store.load_task(task_id)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        runner = WorkerRunner(queue, RetryingController(store), worker_id="worker-a")
        first = runner.run_once()
        assert first.status == TaskStatus.QUEUED
        assert queue.snapshot(task.task_id).state == "queued"
        second = runner.run_once()
        assert second.status == TaskStatus.QUEUED
        assert queue.snapshot(task.task_id).state == "failed"
        assert queue.snapshot(task.task_id).attempts == 2


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


def test_worker_defers_waiting_task_until_explicit_wake(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="waiting task")

    class WaitingController:
        def __init__(self, store):
            self.store = store
            self.lease_guard = None
            self.lease_proof = None

        def resume(self, task_id):
            waiting = self.store.load_task(task_id)
            waiting.status = TaskStatus.WAITING_RECONCILIATION
            return waiting

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        runner = WorkerRunner(queue, WaitingController(store), worker_id="worker-a")
        result = runner.run_once()
        assert result is not None and result.status == TaskStatus.WAITING_RECONCILIATION
        assert queue.snapshot(task.task_id).state == "waiting"
        assert runner.run_once() is None
        queue.wake(task.task_id)
        assert queue.snapshot(task.task_id).state == "queued"


def test_same_database_state_store_rejects_stale_lease_proof(tmp_path):
    from src.dev_agent.state.sqlite_store import SQLiteStateStore

    path = tmp_path / "shared.sqlite3"
    queue = DurableQueue(path)
    queue.enqueue("task-1")
    first = queue.claim("worker-a", lease_seconds=1)
    proof = first.lease_proof
    assert isinstance(proof, LeaseProof)
    with SQLiteStateStore(path) as store:
        task = Task(objective="shared")
        task.task_id = "task-1"
        store.save_task(task)
        queue.claim("worker-b", now=first.lease_until.timestamp() + 1, lease_seconds=30)
        with pytest.raises(StaleLease):
            store.commit_transition(task=task, lease_proof=proof)


def test_maintenance_mode_rejects_new_claims_and_can_resume(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("task-1")
    queue.set_maintenance(True)
    with pytest.raises(MaintenanceMode):
        queue.claim("worker-a")
    queue.set_maintenance(False)
    assert queue.claim("worker-a").task_id == "task-1"


def test_lease_renewal_rejects_non_positive_duration(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("task-1")
    item = queue.claim("worker-a")

    with pytest.raises(ValueError, match="positive lease_seconds"):
        queue.renew("task-1", worker_id="worker-a", state_version=item.state_version, lease_seconds=0)


def test_worker_renews_lease_during_long_controller_execution(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="long worker task")

    class SlowController:
        def __init__(self, store):
            self.store = store
            self.lease_guard = None
            self.lease_proof = None

        def resume(self, task_id):
            time.sleep(1.5)
            self.lease_guard()
            task = self.store.load_task(task_id)
            task.status = TaskStatus.COMPLETED
            return task

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        queue.enqueue(task.task_id)
        controller = SlowController(store)
        completed = WorkerRunner(queue, controller, worker_id="worker-a", lease_seconds=1.0).run_once()

    assert completed is not None and completed.status == TaskStatus.COMPLETED
    assert queue.snapshot(task.task_id).state == "completed"


def test_queue_claim_is_atomic_across_independent_processes(tmp_path):
    path = str(tmp_path / "queue.sqlite3")
    queue = DurableQueue(path)
    queue.enqueue("task-1")
    # Do not keep the parent's SQLite handle open while Windows spawn workers
    # contend for the same database.  The claim transaction itself is still
    # exercised by two independent process-owned connections.
    queue.close()
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [context.Process(target=_claim_in_process, args=(path, f"worker-{index}", result_queue)) for index in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sum(result_queue.get(timeout=2) is not None for _ in processes) == 1
