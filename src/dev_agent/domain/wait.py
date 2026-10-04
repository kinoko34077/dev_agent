"""Canonical durable wait conditions.

The queue still stores the small legacy ``wake_reason`` string because that is
the durable compatibility boundary used by existing wake authorities.  This
module gives that string a typed meaning without creating another scheduler or
wait state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping

from .protocol import TaskStatus


class WaitKind(str, Enum):
    USER_DELAY = "user_delay"
    RESOURCE = "resource"
    PROVIDER_CAPACITY = "provider_capacity"
    QUOTA = "quota"
    MAINTENANCE = "maintenance"
    HUMAN = "human"
    APPROVAL = "approval"
    DEPENDENCY = "dependency"
    INTERRUPT = "interrupt"
    RECONCILIATION = "reconciliation"
    BUDGET = "budget"


class WaitReplayPolicy(str, Enum):
    """How a wake may resume the existing task."""

    RESUME_CHECKPOINT = "resume_checkpoint"
    EVENT_ONLY = "event_only"
    NO_EXTERNAL_REPLAY = "no_external_replay"


@dataclass(frozen=True)
class WaitRegistryEntry:
    kind: WaitKind
    wake_authority: str
    wake_predicate: str
    replay_policy: WaitReplayPolicy


WAIT_CONDITION_REGISTRY: dict[TaskStatus, WaitRegistryEntry] = {
    TaskStatus.WAITING_DEPENDENCY: WaitRegistryEntry(
        WaitKind.DEPENDENCY,
        "dependency_graph",
        "dependency_state_changed",
        WaitReplayPolicy.RESUME_CHECKPOINT,
    ),
    TaskStatus.WAITING_APPROVAL: WaitRegistryEntry(
        WaitKind.APPROVAL,
        "approval_authority",
        "approval_decision_recorded",
        WaitReplayPolicy.RESUME_CHECKPOINT,
    ),
    TaskStatus.WAITING_HUMAN: WaitRegistryEntry(
        WaitKind.HUMAN,
        "human_interaction",
        "human_response_recorded",
        WaitReplayPolicy.RESUME_CHECKPOINT,
    ),
    TaskStatus.WAITING_RECONCILIATION: WaitRegistryEntry(
        WaitKind.RECONCILIATION,
        "reconciliation",
        "effect_outcome_confirmed",
        WaitReplayPolicy.NO_EXTERNAL_REPLAY,
    ),
    TaskStatus.BLOCKED_QUOTA: WaitRegistryEntry(
        WaitKind.QUOTA,
        "quota_requalification",
        "quota_reset_or_requalified",
        WaitReplayPolicy.RESUME_CHECKPOINT,
    ),
    TaskStatus.BLOCKED_BUDGET: WaitRegistryEntry(
        WaitKind.BUDGET,
        "budget_authority",
        "budget_available_or_period_changed",
        WaitReplayPolicy.EVENT_ONLY,
    ),
}


def _bounded_text(value: Any, name: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return normalized


def _deadline(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("deadline_epoch must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("deadline_epoch must be a finite non-negative number")
    return result


@dataclass(frozen=True)
class WaitCondition:
    """The durable meaning of one non-terminal task wait."""

    kind: WaitKind
    subject: str
    created_from: str
    wake_authority: str
    wake_predicate: str
    deadline_epoch: float | None = None
    replay_policy: WaitReplayPolicy = WaitReplayPolicy.EVENT_ONLY
    queue_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", self.kind if isinstance(self.kind, WaitKind) else WaitKind(self.kind))
        object.__setattr__(self, "replay_policy", self.replay_policy if isinstance(self.replay_policy, WaitReplayPolicy) else WaitReplayPolicy(self.replay_policy))
        for name in ("subject", "created_from", "wake_authority", "wake_predicate"):
            object.__setattr__(self, name, _bounded_text(getattr(self, name), name))
        object.__setattr__(self, "deadline_epoch", _deadline(self.deadline_epoch))
        if self.queue_reason is not None:
            object.__setattr__(self, "queue_reason", _bounded_text(self.queue_reason, "queue_reason", limit=128))
        if self.kind is WaitKind.RECONCILIATION and self.replay_policy is not WaitReplayPolicy.NO_EXTERNAL_REPLAY:
            raise ValueError("reconciliation waits must forbid external replay")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "created_from": self.created_from,
            "wake_authority": self.wake_authority,
            "wake_predicate": self.wake_predicate,
            "deadline_epoch": self.deadline_epoch,
            "replay_policy": self.replay_policy.value,
            "queue_reason": self.queue_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WaitCondition":
        if not isinstance(data, Mapping):
            raise ValueError("wait_condition must be an object")
        try:
            return cls(
                kind=data["kind"],
                subject=data["subject"],
                created_from=data["created_from"],
                wake_authority=data["wake_authority"],
                wake_predicate=data["wake_predicate"],
                deadline_epoch=data.get("deadline_epoch"),
                replay_policy=data["replay_policy"],
                queue_reason=data.get("queue_reason"),
            )
        except KeyError as exc:
            raise ValueError(f"wait_condition missing field: {exc.args[0]}") from exc

    def with_queue_reason(self, reason: str) -> "WaitCondition":
        return WaitCondition(
            kind=self.kind,
            subject=self.subject,
            created_from=self.created_from,
            wake_authority=self.wake_authority,
            wake_predicate=self.wake_predicate,
            deadline_epoch=self.deadline_epoch,
            replay_policy=self.replay_policy,
            queue_reason=reason,
        )


def _entry_condition(entry: WaitRegistryEntry, *, subject: str, created_from: str, deadline_epoch: float | None, queue_reason: str | None) -> WaitCondition:
    return WaitCondition(
        kind=entry.kind,
        subject=subject,
        created_from=created_from,
        wake_authority=entry.wake_authority,
        wake_predicate=entry.wake_predicate,
        deadline_epoch=deadline_epoch,
        replay_policy=entry.replay_policy,
        queue_reason=queue_reason,
    )


def condition_from_task_metadata(status: TaskStatus | str, metadata: Mapping[str, Any] | None) -> WaitCondition:
    """Load the typed condition or derive one from legacy wait metadata.

    Derivation is deliberately bounded and deterministic.  It exists for
    tasks written before Issue #70; newly produced waits persist the typed
    dictionary directly.
    """

    try:
        normalized_status = status if isinstance(status, TaskStatus) else TaskStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported task status for wait condition: {status!r}") from exc
    values = dict(metadata or {})
    if normalized_status not in WAIT_CONDITION_REGISTRY:
        raise ValueError(f"task status is not a durable wait: {normalized_status.value}")

    reason = values.get("wait_reason")
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
    deadline = values.get("wait_until_epoch")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        deadline = None
    else:
        deadline = float(deadline)
        if not math.isfinite(deadline) or deadline < 0:
            deadline = None

    stored = values.get("wait_condition")
    if isinstance(stored, Mapping):
        # A partially written or older typed projection must not make a
        # durable wait unobservable.  Fall back to the legacy reason and
        # rebuild a bounded condition below; the legacy fields are retained
        # precisely for this restart-compatible migration path.
        try:
            candidate = WaitCondition.from_dict(stored)
        except (KeyError, TypeError, ValueError):
            candidate = None
        if candidate is not None:
            expected_kind = WAIT_CONDITION_REGISTRY[normalized_status].kind
            kind_matches_status = normalized_status is TaskStatus.WAITING_DEPENDENCY or candidate.kind is expected_kind
            reason_matches = reason is None or candidate.queue_reason == reason
            deadline_matches = "wait_until_epoch" not in values or candidate.deadline_epoch == deadline
            if kind_matches_status and reason_matches and deadline_matches:
                return candidate

    if normalized_status is TaskStatus.WAITING_RECONCILIATION:
        return _entry_condition(
            WAIT_CONDITION_REGISTRY[normalized_status],
            subject=str(values.get("request_id") or values.get("provider_request_id") or "effect"),
            created_from="controller:reconciliation",
            deadline_epoch=None,
            queue_reason=reason or "reconciliation",
        )

    if normalized_status is TaskStatus.WAITING_HUMAN:
        return _entry_condition(
            WAIT_CONDITION_REGISTRY[normalized_status],
            subject=str(values.get("human_request_id") or "human"),
            created_from="controller:human",
            deadline_epoch=None,
            queue_reason=reason or "human",
        )

    if normalized_status is TaskStatus.WAITING_APPROVAL:
        return _entry_condition(
            WAIT_CONDITION_REGISTRY[normalized_status],
            subject=str(values.get("approval_id") or "approval"),
            created_from="controller:approval",
            deadline_epoch=None,
            queue_reason=reason or "approval",
        )

    if normalized_status is TaskStatus.BLOCKED_QUOTA:
        subject = reason.split(":", 1)[1] if reason and reason.startswith("quota:") else str(values.get("quota_domain") or "quota")
        return _entry_condition(
            WAIT_CONDITION_REGISTRY[normalized_status],
            subject=subject,
            created_from="controller:quota",
            deadline_epoch=deadline,
            queue_reason=reason or "quota",
        )

    if normalized_status is TaskStatus.BLOCKED_BUDGET:
        return _entry_condition(
            WAIT_CONDITION_REGISTRY[normalized_status],
            subject=str(values.get("budget_domain") or "budget"),
            created_from="controller:budget",
            deadline_epoch=deadline,
            queue_reason=reason or "budget",
        )

    # WAITING_DEPENDENCY has several existing, intentionally targeted reasons.
    if reason == "user_delay":
        return WaitCondition(
            kind=WaitKind.USER_DELAY,
            subject="task",
            created_from="controller:user_delay",
            wake_authority="runtime_maintenance",
            wake_predicate="wake_at_reached",
            deadline_epoch=deadline,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason,
        )
    if reason and reason.startswith("resource:provider_execution_saturated"):
        parts = reason.split(":", 2)
        subject = parts[2] if len(parts) == 3 and parts[2] else "pool"
        predicate = "pool_capacity_available" if subject == "pool" else "binding_capacity_available"
        return WaitCondition(
            kind=WaitKind.PROVIDER_CAPACITY,
            subject=subject,
            created_from="controller:provider_capacity",
            wake_authority="provider_capacity",
            wake_predicate=predicate,
            deadline_epoch=None,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason,
        )
    if reason and (reason.startswith("quota:") or reason.startswith("quota_unknown:")):
        subject = reason.split(":", 1)[1] or "quota"
        return WaitCondition(
            kind=WaitKind.QUOTA,
            subject=subject,
            created_from="controller:quota",
            wake_authority="quota_requalification",
            wake_predicate="quota_reset_or_requalified",
            deadline_epoch=deadline,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason,
        )
    if reason == "maintenance":
        return WaitCondition(
            kind=WaitKind.MAINTENANCE,
            subject="runtime",
            created_from="controller:maintenance",
            wake_authority="maintenance",
            wake_predicate="maintenance_released",
            deadline_epoch=None,
            replay_policy=WaitReplayPolicy.EVENT_ONLY,
            queue_reason=reason,
        )
    if reason in {"interrupt", "task.waiting_interrupt"} or isinstance(values.get("interrupt"), Mapping):
        return WaitCondition(
            kind=WaitKind.INTERRUPT,
            subject=str(values.get("interrupt_child_task_id") or values.get("child_task_id") or "interrupt_child"),
            created_from="controller:interrupt",
            wake_authority="interrupt_resume",
            wake_predicate="interrupt_child_terminal",
            deadline_epoch=None,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason or "interrupt",
        )
    if reason == "approval":
        entry = WAIT_CONDITION_REGISTRY[TaskStatus.WAITING_APPROVAL]
        return _entry_condition(entry, subject="approval", created_from="controller:approval", deadline_epoch=deadline, queue_reason=reason)
    if reason in {"planner_children", "planner_dependency", "devfarm_handoff"} or (reason and reason.startswith("dependency")):
        return WaitCondition(
            kind=WaitKind.DEPENDENCY,
            subject=reason or "dependency",
            created_from="controller:dependency",
            wake_authority="dependency_graph",
            wake_predicate="dependency_state_changed",
            deadline_epoch=deadline,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason or "dependency",
        )
    if reason and reason.startswith("resource:"):
        category = reason.split(":", 1)[1] or "resource"
        kind = WaitKind.QUOTA if category in {"quota", "quota_unknown"} else WaitKind.RESOURCE
        authority = "quota_requalification" if kind is WaitKind.QUOTA else "resource_observation"
        predicate = "quota_reset_or_requalified" if kind is WaitKind.QUOTA else "eligible_route_observed"
        return WaitCondition(
            kind=kind,
            subject=category,
            created_from="controller:resource",
            wake_authority=authority,
            wake_predicate=predicate,
            deadline_epoch=deadline,
            replay_policy=WaitReplayPolicy.RESUME_CHECKPOINT,
            queue_reason=reason,
        )

    entry = WAIT_CONDITION_REGISTRY[normalized_status]
    return _entry_condition(
        entry,
        subject="dependency",
        created_from="legacy:status",
        deadline_epoch=deadline,
        queue_reason=reason or "dependency",
    )


def attach_wait_condition(metadata: Mapping[str, Any], condition: WaitCondition) -> dict[str, Any]:
    """Return metadata with the typed condition while retaining old fields."""

    result = dict(metadata)
    result["wait_condition"] = condition.to_dict()
    if condition.queue_reason and not result.get("wait_reason"):
        result["wait_reason"] = condition.queue_reason
    if condition.deadline_epoch is not None and "wait_until_epoch" not in result:
        result["wait_until_epoch"] = condition.deadline_epoch
    return result
