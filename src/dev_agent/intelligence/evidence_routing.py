"""Evidence-based advisory ordering for already-eligible Worker bindings.

The production ResourceRouter remains the authority for capability, privacy,
health, quota, budget, and concurrency constraints.  This module only ranks a
caller-supplied, hard-filtered set of bindings using host-observed Worker
metrics.  Missing, stale, or regressed evidence never creates a new route and
never overrides an upstream policy decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Protocol


class EvidenceRoutingError(ValueError):
    """Evidence or routing input is malformed and cannot be used safely."""


class EvidenceSummarySource(Protocol):
    """Read-only source of host-verified Worker summaries."""

    def summarize(
        self,
        *,
        task_type: str | None = None,
        provider_binding_id: str | None = None,
        minimum_samples: int = 1,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]: ...


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise EvidenceRoutingError(f"{name} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvidenceRoutingError(f"{name} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceRoutingError(f"{name} must be a non-negative integer")
    return value


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise EvidenceRoutingError(f"{name} must be a finite non-negative number")
    return float(value)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceRoutingError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceRoutingError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceRoutingError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class EvidenceRouteDecision:
    """One deterministic recommendation for a caller-supplied binding."""

    binding_id: str
    eligible: bool
    preferred: bool
    reason: str
    sample_count: int
    acceptance_rate: float | None
    average_elapsed_ms: float | None
    average_retry_count: float | None
    latest_recorded_at: str | None = None
    rollback_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "eligible": self.eligible,
            "preferred": self.preferred,
            "reason": self.reason,
            "sample_count": self.sample_count,
            "acceptance_rate": self.acceptance_rate,
            "average_elapsed_ms": self.average_elapsed_ms,
            "average_retry_count": self.average_retry_count,
            "latest_recorded_at": self.latest_recorded_at,
            "rollback_triggered": self.rollback_triggered,
        }


@dataclass
class _Aggregate:
    sample_count: int = 0
    accepted_count: int = 0
    elapsed_total: float = 0.0
    retry_total: float = 0.0
    latest: datetime | None = None

    def add(self, summary: Mapping[str, Any]) -> None:
        sample_count = _positive_int(summary.get("sample_count"), "sample_count")
        accepted_count = _nonnegative_integer(summary.get("accepted_count"), "accepted_count")
        if accepted_count > sample_count:
            raise EvidenceRoutingError("accepted_count cannot exceed sample_count")
        elapsed = _finite_nonnegative(summary.get("average_elapsed_ms"), "average_elapsed_ms")
        retries = _finite_nonnegative(summary.get("average_retry_count"), "average_retry_count")
        latest_value = summary.get("latest_recorded_at")
        latest = _timestamp(latest_value, "latest_recorded_at") if latest_value is not None else None
        self.sample_count += sample_count
        self.accepted_count += accepted_count
        self.elapsed_total += elapsed * sample_count
        self.retry_total += retries * sample_count
        if latest is not None and (self.latest is None or latest > self.latest):
            self.latest = latest


class EvidenceBasedRoutingPolicy:
    """Rank only bindings already admitted by the authoritative hard filter.

    ``hard_filtered_bindings`` is intentionally the only candidate input.  A
    caller must first apply the normal ResourceRouter and policy checks; this
    class has no API that can discover or add resources.  Eligible history is
    advisory and can be disabled by returning the original candidate order
    when ``preferred_bindings`` is empty.
    """

    def __init__(
        self,
        source: EvidenceSummarySource,
        *,
        minimum_samples: int = 3,
        max_age_seconds: float | None = 86_400.0,
        minimum_acceptance_rate: float = 0.8,
        max_average_retry_count: float | None = None,
    ) -> None:
        if not hasattr(source, "summarize") or not callable(source.summarize):
            raise TypeError("source must provide a callable summarize method")
        self.source = source
        self.minimum_samples = _positive_int(minimum_samples, "minimum_samples")
        if max_age_seconds is not None:
            max_age_seconds = _finite_nonnegative(max_age_seconds, "max_age_seconds")
        self.max_age_seconds = max_age_seconds
        if isinstance(minimum_acceptance_rate, bool) or not isinstance(minimum_acceptance_rate, (int, float)) or not math.isfinite(float(minimum_acceptance_rate)) or not 0 <= minimum_acceptance_rate <= 1:
            raise EvidenceRoutingError("minimum_acceptance_rate must be between 0 and 1")
        self.minimum_acceptance_rate = float(minimum_acceptance_rate)
        if max_average_retry_count is not None:
            max_average_retry_count = _finite_nonnegative(max_average_retry_count, "max_average_retry_count")
        self.max_average_retry_count = max_average_retry_count

    def rank(
        self,
        task_type: str,
        hard_filtered_bindings: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> tuple[EvidenceRouteDecision, ...]:
        """Return deterministic evidence decisions for the given candidates."""

        task_type = _text(task_type, "task_type", max_length=64)
        if isinstance(hard_filtered_bindings, (str, bytes)) or not isinstance(hard_filtered_bindings, Sequence):
            raise EvidenceRoutingError("hard_filtered_bindings must be a sequence")
        bindings: list[str] = []
        for value in hard_filtered_bindings:
            binding = _text(value, "provider_binding_id")
            if binding in bindings:
                raise EvidenceRoutingError(f"duplicate provider binding: {binding}")
            bindings.append(binding)
        if not bindings:
            return ()
        current = _utc(now)
        raw_summaries = self.source.summarize(
            task_type=task_type,
            minimum_samples=1,
            max_age_seconds=None,
            now=current,
        )
        if not isinstance(raw_summaries, Sequence) or isinstance(raw_summaries, (str, bytes)):
            raise EvidenceRoutingError("evidence source must return a sequence")
        aggregates: dict[str, _Aggregate] = {}
        candidate_set = set(bindings)
        for raw in raw_summaries:
            if not isinstance(raw, Mapping):
                raise EvidenceRoutingError("evidence summaries must be objects")
            summary_task_type = _text(raw.get("task_type"), "task_type", max_length=64)
            binding = _text(raw.get("provider_binding_id"), "provider_binding_id")
            # Validate every returned summary before filtering it.  A broken
            # evidence source must fail closed rather than silently influence a
            # subset of candidates.
            if summary_task_type != task_type or binding not in candidate_set:
                continue
            aggregates.setdefault(binding, _Aggregate()).add(raw)

        decisions: list[EvidenceRouteDecision] = []
        for binding in bindings:
            aggregate = aggregates.get(binding)
            if aggregate is None:
                decisions.append(EvidenceRouteDecision(binding, False, False, "insufficient_evidence", 0, None, None, None))
                continue
            sample_count = aggregate.sample_count
            acceptance_rate = aggregate.accepted_count / sample_count
            average_elapsed = aggregate.elapsed_total / sample_count
            average_retry = aggregate.retry_total / sample_count
            latest = aggregate.latest.isoformat() if aggregate.latest is not None else None
            if sample_count < self.minimum_samples:
                decisions.append(EvidenceRouteDecision(binding, False, False, "insufficient_samples", sample_count, acceptance_rate, average_elapsed, average_retry, latest))
                continue
            if aggregate.latest is None:
                decisions.append(EvidenceRouteDecision(binding, False, False, "evidence_freshness_missing", sample_count, acceptance_rate, average_elapsed, average_retry))
                continue
            age = (current - aggregate.latest).total_seconds()
            if age < 0:
                decisions.append(EvidenceRouteDecision(binding, False, False, "evidence_not_yet_observed", sample_count, acceptance_rate, average_elapsed, average_retry, latest))
                continue
            if self.max_age_seconds is not None and age > self.max_age_seconds:
                decisions.append(EvidenceRouteDecision(binding, False, False, "evidence_expired", sample_count, acceptance_rate, average_elapsed, average_retry, latest))
                continue
            if acceptance_rate < self.minimum_acceptance_rate or (
                self.max_average_retry_count is not None and average_retry > self.max_average_retry_count
            ):
                decisions.append(EvidenceRouteDecision(binding, False, False, "rollback_threshold", sample_count, acceptance_rate, average_elapsed, average_retry, latest, True))
                continue
            decisions.append(EvidenceRouteDecision(binding, True, True, "evidence_eligible", sample_count, acceptance_rate, average_elapsed, average_retry, latest))

        decision_by_binding = {decision.binding_id: decision for decision in decisions}
        preferred = sorted(
            (decision for decision in decisions if decision.preferred),
            key=lambda decision: (
                -(decision.acceptance_rate or 0.0),
                decision.average_elapsed_ms if decision.average_elapsed_ms is not None else math.inf,
                decision.average_retry_count if decision.average_retry_count is not None else math.inf,
                decision.binding_id,
            ),
        )
        fallback = [decision_by_binding[binding] for binding in bindings if not decision_by_binding[binding].preferred]
        return tuple(preferred + fallback)

    def preferred_bindings(
        self,
        task_type: str,
        hard_filtered_bindings: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Return only evidence-eligible bindings, never new candidates."""

        return tuple(decision.binding_id for decision in self.rank(task_type, hard_filtered_bindings, now=now) if decision.preferred)


__all__ = ["EvidenceBasedRoutingPolicy", "EvidenceRouteDecision", "EvidenceRoutingError", "EvidenceSummarySource"]
