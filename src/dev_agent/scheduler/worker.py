"""Lease-fenced queue worker that invokes the durable Controller boundary."""

from __future__ import annotations

from threading import Event, Thread

from ..domain.protocol import Task, TaskStatus
from ..runtime.controller import Controller, ExecutionContext
from .queue import DurableQueue, QueueEmpty, StaleLease


class _LeaseHeartbeat:
    """Renew one lease while the Controller owns a long-running task."""

    def __init__(self, queue: DurableQueue, *, task_id: str, worker_id: str, state_version: int, lease_seconds: float) -> None:
        self.queue = queue
        self.task_id = task_id
        self.worker_id = worker_id
        self.state_version = state_version
        self.lease_seconds = lease_seconds
        self._stop = Event()
        self._failure: Exception | None = None
        self._thread = Thread(target=self._run, name=f"lease-heartbeat:{task_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        interval = min(1.0, max(0.01, self.lease_seconds / 3.0))
        while not self._stop.wait(interval):
            try:
                self.queue.renew(self.task_id, worker_id=self.worker_id, state_version=self.state_version, lease_seconds=self.lease_seconds)
            except Exception as exc:
                self._failure = exc
                return

    def assert_healthy(self) -> None:
        if self._failure is not None:
            raise StaleLease(self.task_id) from self._failure

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.lease_seconds))


class WorkerRunner:
    _DEFERRED_STATUSES = {
        TaskStatus.WAITING_DEPENDENCY,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WAITING_RECONCILIATION,
        TaskStatus.BLOCKED_QUOTA,
        TaskStatus.BLOCKED_BUDGET,
    }

    def __init__(self, queue: DurableQueue, controller: Controller, *, worker_id: str, lease_seconds: float = 30.0, max_attempts: int | None = None) -> None:
        self.queue = queue
        self.controller = controller
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        if max_attempts is not None and (isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0):
            raise ValueError("max_attempts must be a positive integer")
        self.max_attempts = max_attempts

    def run_once(self) -> Task | None:
        try:
            item = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        except QueueEmpty:
            return None
        task = self.controller.store.load_task(item.task_id)
        if task is None:
            self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
            raise RuntimeError(f"queued task is missing from StateStore: {item.task_id}")
        max_attempts = self.max_attempts if self.max_attempts is not None else task.limits.max_retries + 1
        item = self.queue.set_max_attempts(item.task_id, worker_id=self.worker_id, state_version=item.state_version, max_attempts=max_attempts)
        item = self.queue.set_max_execution_attempts(item.task_id, worker_id=self.worker_id, state_version=item.state_version, max_attempts=max_attempts)
        self.queue.renew(item.task_id, worker_id=self.worker_id, state_version=item.state_version, lease_seconds=self.lease_seconds)
        heartbeat = _LeaseHeartbeat(self.queue, task_id=item.task_id, worker_id=self.worker_id, state_version=item.state_version, lease_seconds=self.lease_seconds)
        heartbeat.start()
        def assert_active_lease() -> None:
            heartbeat.assert_healthy()
            self.queue.assert_lease(item.task_id, worker_id=self.worker_id, state_version=item.state_version, lease_token=item.lease_token)

        execution_context = ExecutionContext(lease_guard=assert_active_lease, lease_proof=item.lease_proof)
        try:
            result = self.controller.resume(task.task_id, execution_context=execution_context)
        except StaleLease:
            # A heartbeat/ownership failure is not a task failure.  If this
            # worker still owns the lease, return the item to the finite retry
            # queue; if another worker reclaimed it, the queue fence rejects
            # this write and the new owner remains authoritative.
            try:
                self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version, retry=True, max_attempts=max_attempts)
            except StaleLease:
                pass
            raise
        except Exception:
            self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version, execution_attempt=True)
            raise
        finally:
            heartbeat.stop()
        try:
            # A heartbeat may have failed after its last successful renewal.
            # Confirm ownership synchronously immediately before publishing
            # the Controller result; this also avoids finalization racing the
            # lease deadline.  An expired lease must never be resurrected.
            self.queue.renew(item.task_id, worker_id=self.worker_id, state_version=item.state_version, lease_seconds=self.lease_seconds)
            if result.status == TaskStatus.COMPLETED:
                self.queue.complete(item.task_id, worker_id=self.worker_id, state_version=item.state_version, execution_attempt=True)
            elif result.status == TaskStatus.CANCELLED:
                self.queue.cancel(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
            elif result.status in self._DEFERRED_STATUSES:
                # Waiting states require an external event (approval, budget
                # replenishment, or reconciliation).  Requeueing immediately
                # can duplicate an ambiguous external effect or spin forever.
                wait_reason = result.metadata.get("wait_reason") if isinstance(result.metadata, dict) else None
                wait_until = result.metadata.get("wait_until_epoch") if isinstance(result.metadata, dict) else None
                if isinstance(wait_until, (int, float)) and not isinstance(wait_until, bool) and wait_reason:
                    self.queue.defer_until(
                        item.task_id,
                        worker_id=self.worker_id,
                        state_version=item.state_version,
                        wake_at=wait_until,
                        reason=wait_reason,
                    )
                elif (
                    wait_reason == "resource:provider_execution_saturated"
                    or wait_reason.startswith("resource:provider_execution_saturated:")
                ):
                    self.queue.defer_for_event(
                        item.task_id,
                        worker_id=self.worker_id,
                        state_version=item.state_version,
                        reason=wait_reason,
                    )
                else:
                    self.queue.defer(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
            else:
                self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version, retry=result.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}, max_attempts=max_attempts, execution_attempt=True)
        except StaleLease:
            # The result cannot be published after ownership is lost.  If the
            # item is still ours and within its lease, return it to the finite
            # retry path; otherwise a newer owner or the reaper is authoritative.
            try:
                self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version, retry=True, max_attempts=max_attempts)
            except StaleLease:
                pass
            raise
        return result
