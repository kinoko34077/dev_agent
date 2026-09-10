"""Reset-aware scheduling helpers for quota-blocked work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .queue import DurableQueue, QueueItem


class QuotaReadView(Protocol):
    def list_quota_observations(self) -> list[dict[str, object]]: ...


class QuotaWriteView(QuotaReadView, Protocol):
    def get_quota_observation(self, resource_id: str) -> dict[str, Any] | None: ...

    def get_resource(self, resource_id: str) -> dict[str, Any]: ...

    def ingest_quota_observation(
        self,
        resource_id: str,
        usage: Mapping[str, Any],
        *,
        observed_at: str | None = None,
        source: str = "provider-response",
    ) -> bool: ...


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


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


_EXTERNAL_BLOCKS = frozenset({"authorization", "permission", "blocked_external"})


class QuotaProbeStatus(str, Enum):
    """Outcome of one explicitly requested, bounded quota probe."""

    NOT_BLOCKED = "not_blocked"
    NOT_DUE = "not_due"
    BLOCKED_EXTERNAL = "blocked_external"
    PROBE_FAILED = "probe_failed"
    INVALID_OBSERVATION = "invalid_observation"
    STILL_BLOCKED = "still_blocked"
    REQUALIFIED = "requalified"


@dataclass(frozen=True)
class QuotaProbeResult:
    """Auditable result of one probe attempt.

    The coordinator never retries a callback, starts a provider request on its
    own, or revives a resource from a clock crossing alone.  The caller must
    explicitly invoke :meth:`QuotaRequalificationCoordinator.probe_once`.
    """

    resource_id: str
    quota_domain: str | None
    status: QuotaProbeStatus
    observed_at: str | None = None
    observation_persisted: bool = False
    woken_tasks: int = 0
    error_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "quota_domain": self.quota_domain,
            "status": self.status.value,
            "observed_at": self.observed_at,
            "observation_persisted": self.observation_persisted,
            "woken_tasks": self.woken_tasks,
            "error_category": self.error_category,
        }


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


class QuotaRequalificationCoordinator:
    """Run one explicit post-reset probe and publish its fresh observation.

    ``QuotaWakeScheduler`` deliberately has no Provider dependency.  This
    companion keeps that property: a caller supplies a single probe callback,
    and the coordinator only validates/persists the returned provider-neutral
    observation.  It is safe to use from a maintenance/operator command or a
    future bounded scheduler job, but it is not a busy polling loop.
    """

    def __init__(self, ledger: QuotaWriteView, wake_scheduler: QuotaWakeScheduler | None = None) -> None:
        self.ledger = ledger
        self.wake_scheduler = wake_scheduler

    def probe_once(
        self,
        resource_id: str,
        probe: Callable[[str, str], Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> QuotaProbeResult:
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("resource_id must be a non-empty string")
        if not callable(probe):
            raise TypeError("probe must be callable")
        resource_id = resource_id.strip()
        current = _utc(now)
        latest = self.ledger.get_quota_observation(resource_id)
        if latest is None:
            return QuotaProbeResult(resource_id, None, QuotaProbeStatus.NOT_BLOCKED)
        reason = latest.get("block_reason")
        if not isinstance(reason, str) or not reason.strip():
            return QuotaProbeResult(resource_id, self._domain(latest), QuotaProbeStatus.NOT_BLOCKED)
        reason = reason.strip().lower()
        domain = self._domain(latest)
        if reason in _EXTERNAL_BLOCKS:
            return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.BLOCKED_EXTERNAL, error_category=reason)
        blocked_until = _parse_reset(latest.get("blocked_until"))
        if blocked_until is None or blocked_until > current:
            return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.NOT_DUE)
        if domain is None:
            return QuotaProbeResult(resource_id, None, QuotaProbeStatus.INVALID_OBSERVATION)

        # Exactly one callback invocation is intentional.  Provider adapters
        # own transport/error normalization; this boundary only records a
        # bounded failure category and never turns it into a retry storm.
        try:
            raw = probe(resource_id, domain)
        except Exception as exc:
            category = getattr(exc, "category", None)
            return QuotaProbeResult(
                resource_id,
                domain,
                QuotaProbeStatus.PROBE_FAILED,
                error_category=category.strip().lower() if isinstance(category, str) and category.strip() else "probe_error",
            )
        payload = self._payload(raw, domain)
        if payload is None:
            return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.INVALID_OBSERVATION)
        observed_at = _iso(current)
        payload["observed_at"] = observed_at
        try:
            persisted = self.ledger.ingest_quota_observation(
                resource_id,
                {"quota_observation": payload},
                observed_at=observed_at,
                source="quota-requalification-probe",
            )
        except (TypeError, ValueError, KeyError):
            persisted = False
        if not persisted:
            return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.INVALID_OBSERVATION, observed_at=observed_at)
        refreshed = self.ledger.get_quota_observation(resource_id)
        if not isinstance(refreshed, dict) or refreshed.get("observed_at") != observed_at:
            return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.INVALID_OBSERVATION, observed_at=observed_at, observation_persisted=True)
        refreshed_reason = refreshed.get("block_reason")
        if isinstance(refreshed_reason, str) and refreshed_reason.strip():
            status = QuotaProbeStatus.BLOCKED_EXTERNAL if refreshed_reason.strip().lower() in _EXTERNAL_BLOCKS else QuotaProbeStatus.STILL_BLOCKED
            return QuotaProbeResult(resource_id, domain, status, observed_at=observed_at, observation_persisted=True, error_category=refreshed_reason.strip().lower())
        woken = (
            self.wake_scheduler.wake_due(now=current)
            if self.wake_scheduler is not None and self.wake_scheduler.queue is not None
            else 0
        )
        return QuotaProbeResult(resource_id, domain, QuotaProbeStatus.REQUALIFIED, observed_at=observed_at, observation_persisted=True, woken_tasks=woken)

    @staticmethod
    def _domain(observation: Mapping[str, Any]) -> str | None:
        domain = observation.get("quota_domain")
        return domain.strip() if isinstance(domain, str) and domain.strip() else None

    @staticmethod
    def _payload(raw: Mapping[str, Any], domain: str) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        candidate = raw.get("quota_observation", raw.get("quota", raw))
        if not isinstance(candidate, Mapping):
            return None
        payload = dict(candidate)
        reported_domain = payload.get("quota_domain")
        if reported_domain is not None and reported_domain != domain:
            return None
        payload["quota_domain"] = domain
        return payload


__all__ = ["QuotaProbeResult", "QuotaProbeStatus", "QuotaReadView", "QuotaRequalificationCoordinator", "QuotaWakeScheduler", "QuotaWriteView"]
