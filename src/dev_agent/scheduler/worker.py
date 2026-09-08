"""Lease-fenced queue worker that invokes the durable Controller boundary."""

from __future__ import annotations

from ..domain.protocol import Task, TaskStatus
from ..runtime.controller import Controller
from .queue import DurableQueue, QueueEmpty


class WorkerRunner:
    def __init__(self, queue: DurableQueue, controller: Controller, *, worker_id: str, lease_seconds: float = 30.0) -> None:
        self.queue = queue
        self.controller = controller
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def run_once(self) -> Task | None:
        try:
            item = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        except QueueEmpty:
            return None
        task = self.controller.store.load_task(item.task_id)
        if task is None:
            self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
            raise RuntimeError(f"queued task is missing from StateStore: {item.task_id}")
        self.queue.renew(item.task_id, worker_id=self.worker_id, state_version=item.state_version, lease_seconds=self.lease_seconds)
        previous_guard = self.controller.lease_guard
        previous_proof = self.controller.lease_proof
        self.controller.lease_guard = lambda: self.queue.assert_lease(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
        self.controller.lease_proof = item.lease_proof
        try:
            result = self.controller.resume(task.task_id)
        except Exception:
            self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
            raise
        finally:
            self.controller.lease_guard = previous_guard
            self.controller.lease_proof = previous_proof
        if result.status == TaskStatus.COMPLETED:
            self.queue.complete(item.task_id, worker_id=self.worker_id, state_version=item.state_version)
        else:
            self.queue.fail(item.task_id, worker_id=self.worker_id, state_version=item.state_version, retry=result.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED})
        return result
