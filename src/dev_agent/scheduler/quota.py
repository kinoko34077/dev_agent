"""Reset-aware scheduling helpers for quota-blocked work."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Protocol

from .queue import DurableQueue, QueueItem


class QuotaReadView(Protocol):
    def list_quota_observations(self) -> list[dict[str, object]]: ...


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_reset(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _utc(parsed)


class QuotaWakeScheduler:
    """Expose durable quota wake boundaries without automatic provider revival.

    This component does not probe a Provider and does not clear a quota block.
    An operator or a bounded requalification job must publish a newer normal
    observation before routing can use the resource again.
    """

    def __init__(self, ledger: QuotaReadView, queue: DurableQueue | None = None) -> None:
        self.ledger = ledger
        self.queue = queue

    def due_domains(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = _utc(now)
        domains: set[str] = set()
        for observation in self.ledger.list_quota_observations():
            reason = observation.get("block_reason")
            if not isinstance(reason, str) or not reason.strip() or reason.strip().lower() in {"authorization", "permission", "blocked_external"}:
                continue
            reset = _parse_reset(observation.get("blocked_until"))
            domain = observation.get("quota_domain")
            if reset is not None and reset <= current and isinstance(domain, str) and domain.strip():
                domains.add(domain.strip())
        return tuple(sorted(domains))

    def next_wake_at(self, *, now: datetime | None = None) -> datetime | None:
        current = _utc(now)
        candidates: list[datetime] = []
        for observation in self.ledger.list_quota_observations():
            reason = observation.get("block_reason")
            if not isinstance(reason, str) or not reason.strip() or reason.strip().lower() in {"authorization", "permission", "blocked_external"}:
                continue
            reset = _parse_reset(observation.get("blocked_until"))
            if reset is not None and reset >= current:
                candidates.append(reset)
        return min(candidates) if candidates else None

    def park(self, task_id: str, *, worker_id: str, state_version: int, wake_at: datetime | float | int) -> QueueItem:
        if self.queue is None:
            raise RuntimeError("a DurableQueue is required to park a task")
        return self.queue.defer_until(
            task_id,
            worker_id=worker_id,
            state_version=state_version,
            wake_at=wake_at,
            reason="quota",
        )

    def wake_due(self, *, now: datetime | float | int | None = None) -> int:
        if self.queue is None:
            raise RuntimeError("a DurableQueue is required to wake tasks")
        return self.queue.wake_due(now=now, reason="quota")


__all__ = ["QuotaReadView", "QuotaWakeScheduler"]
