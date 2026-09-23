"""Thin always-on process boundary for the existing Operation loop.

The coordinator in this module is intentionally small.  It owns process
liveness (peer presence, heartbeat, and a local loop) while the existing
``OperationService`` continues to own Task selection, durable wake rules,
Provider routing, retries, and reconciliation.  It is therefore a process
entrypoint, not a second scheduler or Agent framework.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from threading import Event
from typing import Any, Callable, Mapping

from .coordination.protocol import CoordinationConflict, MessageKind, PeerRecord, PeerStatus
from .coordination.service import ProcessCoordinationService
from .human import HumanInteractionPort
from .operation import OperationConfig, OperationError, OperationService
from .state.sqlite_store import SQLiteStateStore


class RuntimeCoordinatorError(RuntimeError):
    """Raised when the local runtime loses its current peer generation."""


def _positive_number(value: int | float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def _task_counts(store: SQLiteStateStore) -> dict[str, int]:
    counts = Counter()
    tasks = store.snapshot().get("tasks", {})
    if isinstance(tasks, Mapping):
        for payload in tasks.values():
            if isinstance(payload, Mapping):
                status = payload.get("status")
                if isinstance(status, str) and status.strip():
                    counts[status.strip()] += 1
    return dict(sorted(counts.items()))


def _health_projection(
    store: SQLiteStateStore,
    coordination: ProcessCoordinationService | None,
    *,
    role: str,
    instance_id: str,
    now: str | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc).isoformat()
    peer: PeerRecord | None = None
    if coordination is not None:
        peer = coordination.store.get_peer(role, instance_id)
    if peer is None:
        status = "NOT_ATTACHED"
    elif peer.is_live(observed_at):
        status = "READY"
    else:
        status = "DEGRADED"
    return {
        "status": status,
        "component": "operation_runtime",
        "observed_at": observed_at,
        "role": role,
        "instance_id": instance_id,
        "peer": peer.to_dict() if peer is not None else None,
        "task_counts": _task_counts(store),
        "process_authority": "operation_service_only",
        "scheduler": "existing_operation_queue",
        "external_network": "owned_by_operation_provider_boundary",
        "os_registration": "not_owned_by_runtime_coordinator",
    }


def read_runtime_health(
    data_dir: str | Path,
    *,
    role: str = "agent",
    instance_id: str = "operation-runtime",
    now: str | None = None,
) -> dict[str, Any]:
    """Read a bounded health projection without constructing a Provider.

    Missing state is reported as ``NOT_INITIALIZED`` without creating a new
    runtime database.  Existing state is read through the established stores;
    credentials and task payloads are never included in the projection.
    """

    root = Path(data_dir).expanduser()
    state_path = root / "state.sqlite3"
    coordination_path = root / "coordination" / "coordination.sqlite3"
    if not state_path.exists():
        return {
            "status": "NOT_INITIALIZED",
            "component": "operation_runtime",
            "role": role,
            "instance_id": instance_id,
            "task_counts": {},
            "process_authority": "operation_service_only",
            "scheduler": "existing_operation_queue",
            "external_network": "owned_by_operation_provider_boundary",
            "os_registration": "not_owned_by_runtime_coordinator",
        }
    with SQLiteStateStore(state_path) as store:
        if not coordination_path.exists():
            return _health_projection(
                store,
                None,
                role=role,
                instance_id=instance_id,
                now=now,
            )
        with ProcessCoordinationService(data_dir=root) as coordination:
            return _health_projection(
                store,
                coordination,
                role=role,
                instance_id=instance_id,
                now=now,
            )


class RuntimeCoordinator:
    """Keep one Operation process alive with durable presence fencing."""

    def __init__(
        self,
        operation: OperationService,
        coordination: ProcessCoordinationService,
        peer: PeerRecord,
        *,
        role: str,
        instance_id: str,
        presence_lease_seconds: int | float = 60.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.operation = operation
        self.coordination = coordination
        self.peer = peer
        self.role = role
        self.instance_id = instance_id
        self.presence_lease_seconds = _positive_number(
            presence_lease_seconds,
            "presence_lease_seconds",
        )
        if not callable(monotonic_fn):
            raise TypeError("monotonic_fn must be callable")
        self._monotonic = monotonic_fn
        self._closed = False
        self._lost_presence = False
        self._prepared = False
        self._cycles = 0
        self._last_task_id: str | None = None
        self._last_error: str | None = None

    @classmethod
    def open(
        cls,
        config: OperationConfig | None = None,
        *,
        revision: str = "working-tree",
        role: str = "agent",
        instance_id: str = "operation-runtime",
        presence_lease_seconds: int | float = 60.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
        human_interaction_port: HumanInteractionPort | None = None,
    ) -> "RuntimeCoordinator":
        """Open the established Operation components and attach one peer."""

        config = config or OperationConfig.from_environment()
        operation = OperationService.open(config)
        if human_interaction_port is not None:
            operation.bind_human_interaction_port(human_interaction_port)
        coordination = ProcessCoordinationService(data_dir=config.data_dir)
        try:
            peer = coordination.attach_peer(
                role,
                revision=revision,
                capabilities=("operation-loop", "maintenance", "durable-resume"),
                pid=os.getpid(),
                instance_id=instance_id,
                lease_seconds=presence_lease_seconds,
            )
            peer = coordination.set_peer_status(peer, PeerStatus.READY)
        except Exception:
            coordination.close()
            operation.close()
            raise
        return cls(
            operation,
            coordination,
            peer,
            role=role,
            instance_id=instance_id,
            presence_lease_seconds=presence_lease_seconds,
            monotonic_fn=monotonic_fn,
        )

    def _heartbeat(self) -> PeerRecord:
        if self._closed:
            raise RuntimeCoordinatorError("runtime coordinator is closed")
        try:
            self.peer = self.coordination.heartbeat(
                self.peer,
                lease_seconds=self.presence_lease_seconds,
            )
        except CoordinationConflict as exc:
            self._lost_presence = True
            raise RuntimeCoordinatorError("stale runtime peer generation") from exc
        return self.peer

    def _prepare(self) -> None:
        if self._prepared:
            return
        self.operation.prepare_runtime()
        self._prepared = True

    def _consume_interrupt_messages(self) -> int:
        """Consume only interrupt intents and hand them to Operation Core."""

        messages = self.coordination.claim_messages(
            self.peer,
            kind=MessageKind.INTERRUPT,
            limit=4,
            lease_seconds=self.presence_lease_seconds,
        )
        consumed = 0
        for message in messages:
            request = None
            for reference in message.artifact_refs:
                if reference.kind != "task_interrupt_request":
                    continue
                request = self.coordination.artifacts.read_json(reference)
                break
            if not isinstance(request, Mapping):
                self.coordination.ack_message(self.peer, message)
                continue
            task_id = request.get("task_id")
            objective = request.get("objective")
            if not isinstance(task_id, str) or not task_id.strip() or not isinstance(objective, str) or not objective.strip():
                self.coordination.ack_message(self.peer, message)
                continue
            try:
                self.operation.interrupt_task(task_id, objective)
            except (OperationError, ValueError) as exc:
                # A terminal/malformed target is a deterministic Core result,
                # not a reason to poison the mailbox forever.  Transient
                # store/queue failures still escape and leave the lease for
                # the existing coordination recovery path.
                self._last_error = f"interrupt:{type(exc).__name__}: {exc}"
            self.coordination.ack_message(self.peer, message)
            consumed += 1
        return consumed

    def run_once(
        self,
        *,
        quota_probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ):
        """Heartbeat, run one existing Operation boundary, heartbeat again."""

        if self._closed:
            raise RuntimeCoordinatorError("runtime coordinator is closed")
        self._heartbeat()
        # Fence the peer before the Operation startup preparation clears the
        # shared compatibility stop flag.  A stale process must never be able
        # to erase a stop request intended for the current generation.
        self._prepare()
        self._consume_interrupt_messages()
        result = self.operation.run_once(quota_probe=quota_probe)
        self._heartbeat()
        self._cycles += 1
        if result is not None:
            self._last_task_id = result.task_id
        return result

    def health(self) -> dict[str, Any]:
        """Return bounded runtime, peer, and Task-count state."""

        return _health_projection(
            self.operation.store,
            self.coordination,
            role=self.role,
            instance_id=self.instance_id,
        )

    def serve(
        self,
        *,
        stop_event: Event | None = None,
        quota_probe: Callable[[str, str], Mapping[str, Any]] | None = None,
        max_cycles: int | None = None,
        max_runtime_seconds: int | float | None = None,
        wait_fn: Callable[[float], Any] | None = None,
    ) -> dict[str, Any]:
        """Keep the thin process alive until stop, deadline, or fencing.

        Every iteration delegates work to ``OperationService.run_once``.  An
        idle iteration sleeps for the existing Operation idle interval, so no
        Provider call or new Task decision is made while the durable queue is
        waiting for an explicit wake condition.
        """

        if max_cycles is not None and (
            isinstance(max_cycles, bool)
            or not isinstance(max_cycles, int)
            or max_cycles <= 0
        ):
            raise ValueError("max_cycles must be a positive integer")
        if max_runtime_seconds is not None:
            max_runtime_seconds = _positive_number(
                max_runtime_seconds,
                "max_runtime_seconds",
            )
        if wait_fn is not None and not callable(wait_fn):
            raise TypeError("wait_fn must be callable or None")
        stop_event = stop_event or Event()
        self._heartbeat()
        self._prepare()
        started = self._monotonic()
        result = None
        status = "STOPPED"
        while not stop_event.is_set() and not self.operation.control.stop_requested():
            if (
                max_runtime_seconds is not None
                and self._monotonic() - started >= max_runtime_seconds
            ):
                status = "DEADLINE"
                break
            try:
                result = self.run_once(quota_probe=quota_probe)
                self._last_error = None
            except RuntimeCoordinatorError as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                status = "LOST_PRESENCE"
                break
            except Exception as exc:
                # The existing WorkerRunner/Controller owns the durable
                # outcome.  Keep this process available for another queue
                # item, matching OperationService.start semantics.
                self._last_error = f"{type(exc).__name__}: {exc}"
                result = None
            if max_cycles is not None and self._cycles >= max_cycles:
                status = "CYCLE_LIMIT"
                break
            if result is None:
                delay = float(self.operation.config.idle_sleep_seconds)
                if wait_fn is not None:
                    wait_fn(delay)
                else:
                    stop_event.wait(delay)
        if stop_event.is_set() or self.operation.control.stop_requested():
            status = "STOPPED"
        return {
            "status": status,
            "mode": "serve",
            "cycles": self._cycles,
            "last_task_id": self._last_task_id,
            "last_error": self._last_error,
            "bounded": max_cycles is not None or max_runtime_seconds is not None,
            "peer": self.peer.to_dict(),
            "task_counts": _task_counts(self.operation.store),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if not self._lost_presence:
                try:
                    self.coordination.detach_peer(self.peer)
                except CoordinationConflict:
                    # A newer generation owns this identity.  Never mutate
                    # the newer peer while closing a stale process.
                    pass
        finally:
            self.coordination.close()
            self.operation.close()

    def __enter__(self) -> "RuntimeCoordinator":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "RuntimeCoordinator",
    "RuntimeCoordinatorError",
    "read_runtime_health",
]
