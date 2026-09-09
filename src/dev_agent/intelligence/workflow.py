"""Bounded, evidence-based Workflow promotion proposals for Phase 7E.

This module can identify a repeatable task as eligible for a Workflow
proposal, but it never activates or executes that Workflow.  Promotion
requires deterministic host evidence, explicit safety reviews, and a later
operator action outside this boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from ..domain.protocol import Event, TaskType


class WorkflowPromotionDecision(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


@dataclass(frozen=True)
class WorkflowPromotionEvidence:
    """Host-collected evidence for one possible Workflow promotion."""

    workflow_key: str
    task_id: str
    successful_runs: int
    required_successes: int
    deterministic_checks_passed: bool
    policy_compliant: bool
    security_reviewed: bool
    budget_quota_reviewed: bool
    rollback_defined: bool
    rollback_ref: str | None
    operator_owner: str
    manifest_ref: str
    task_type: TaskType = TaskType.WORKER

    def __post_init__(self) -> None:
        for name, value in (
            ("workflow_key", self.workflow_key),
            ("operator_owner", self.operator_owner),
            ("manifest_ref", self.manifest_ref),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        try:
            UUID(self.task_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("task_id must be a UUID string") from exc
        object.__setattr__(self, "task_id", self.task_id.strip())
        for name, value in (("successful_runs", self.successful_runs), ("required_successes", self.required_successes)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.required_successes <= 0:
            raise ValueError("required_successes must be positive")
        for name, value in (
            ("deterministic_checks_passed", self.deterministic_checks_passed),
            ("policy_compliant", self.policy_compliant),
            ("security_reviewed", self.security_reviewed),
            ("budget_quota_reviewed", self.budget_quota_reviewed),
            ("rollback_defined", self.rollback_defined),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        if self.rollback_ref is not None:
            if not isinstance(self.rollback_ref, str) or not self.rollback_ref.strip():
                raise ValueError("rollback_ref must be a non-empty string when provided")
            object.__setattr__(self, "rollback_ref", self.rollback_ref.strip())
        if self.rollback_defined and self.rollback_ref is None:
            raise ValueError("rollback_defined requires rollback_ref")
        if not isinstance(self.task_type, TaskType):
            try:
                object.__setattr__(self, "task_type", TaskType(self.task_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("task_type must be a TaskType") from exc

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task_type"] = self.task_type.value
        return data


@dataclass(frozen=True)
class WorkflowPromotionResult:
    workflow_key: str
    task_id: str
    decision: WorkflowPromotionDecision
    reasons: tuple[str, ...]
    evidence: WorkflowPromotionEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_key": self.workflow_key,
            "task_id": self.task_id,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True)
class WorkflowPromotionCandidate:
    """A proposal that still requires an explicit human review."""

    workflow_key: str
    task_id: str
    evidence: WorkflowPromotionEvidence
    reasons: tuple[str, ...]
    candidate_id: str = field(default_factory=lambda: str(uuid4()))
    requires_human_review: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, WorkflowPromotionEvidence):
            raise TypeError("evidence must be WorkflowPromotionEvidence")
        if not isinstance(self.workflow_key, str) or not self.workflow_key.strip():
            raise ValueError("workflow_key must be a non-empty string")
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if self.task_id != self.evidence.task_id:
            raise ValueError("candidate task_id must match evidence task_id")
        if not isinstance(self.reasons, tuple) or any(not isinstance(reason, str) for reason in self.reasons):
            raise ValueError("reasons must be a tuple of strings")
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        if self.requires_human_review is not True:
            raise ValueError("workflow promotion candidates always require human review")
        object.__setattr__(self, "workflow_key", self.workflow_key.strip())
        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "candidate_id", self.candidate_id.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "workflow_key": self.workflow_key,
            "task_id": self.task_id,
            "evidence": self.evidence.to_dict(),
            "reasons": list(self.reasons),
            "requires_human_review": self.requires_human_review,
        }


class WorkflowPromotionPolicy:
    """Deterministically evaluate whether a Workflow proposal is eligible."""

    def evaluate(self, evidence: WorkflowPromotionEvidence) -> WorkflowPromotionResult:
        if not isinstance(evidence, WorkflowPromotionEvidence):
            raise TypeError("evidence must be WorkflowPromotionEvidence")
        reasons: list[str] = []
        if evidence.successful_runs < evidence.required_successes:
            reasons.append("success_history_insufficient")
        if not evidence.deterministic_checks_passed:
            reasons.append("deterministic_checks_missing")
        if not evidence.policy_compliant:
            reasons.append("policy_non_compliant")
        if not evidence.security_reviewed:
            reasons.append("security_review_missing")
        if not evidence.budget_quota_reviewed:
            reasons.append("budget_quota_review_missing")
        if not evidence.rollback_defined or evidence.rollback_ref is None:
            reasons.append("rollback_path_missing")
        if reasons:
            decision = WorkflowPromotionDecision.NOT_ELIGIBLE
        else:
            decision = WorkflowPromotionDecision.ELIGIBLE
            reasons.append("promotion_criteria_met")
        return WorkflowPromotionResult(
            workflow_key=evidence.workflow_key,
            task_id=evidence.task_id,
            decision=decision,
            reasons=tuple(reasons),
            evidence=evidence,
        )


@dataclass(frozen=True)
class WorkflowPromotionCycle:
    result: WorkflowPromotionResult
    evaluation_event: Event
    candidate: WorkflowPromotionCandidate | None = None
    proposal_event: Event | None = None


class WorkflowPromotionRecorder:
    """Persist proposal lifecycle events without activating a Workflow."""

    def __init__(self, store, *, actor: str = "host-workflow-evaluator") -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        self._store = store
        self._actor = actor.strip()

    def record_result(self, result: WorkflowPromotionResult) -> Event:
        if not isinstance(result, WorkflowPromotionResult):
            raise TypeError("result must be WorkflowPromotionResult")
        event = Event(
            event_type="workflow.promotion.evaluated",
            task_id=result.task_id,
            provider=self._actor,
            payload={
                "actor": self._actor,
                "decision": result.decision.value,
                "reasons": list(result.reasons),
                "evidence": result.evidence.to_dict(),
            },
        )
        self._store.append_event(event)
        return event

    def record_proposal(self, candidate: WorkflowPromotionCandidate) -> Event:
        if not isinstance(candidate, WorkflowPromotionCandidate):
            raise TypeError("candidate must be WorkflowPromotionCandidate")
        candidate_payload = candidate.to_dict()
        event = Event(
            event_type="workflow.promotion.proposed",
            task_id=candidate.task_id,
            provider=self._actor,
            payload={
                "actor": self._actor,
                "candidate_id": candidate.candidate_id,
                "candidate": candidate_payload,
                **candidate_payload,
            },
        )
        self._store.append_event(event)
        return event

    def record_review(
        self,
        candidate: WorkflowPromotionCandidate,
        *,
        actor: str,
        approved: bool,
        approval_reference: str,
        reason: str | None = None,
    ) -> Event:
        if not isinstance(candidate, WorkflowPromotionCandidate):
            raise TypeError("candidate must be WorkflowPromotionCandidate")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean")
        if not isinstance(approval_reference, str) or not approval_reference.strip():
            raise ValueError("approval_reference must be a non-empty string")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("reason must be a non-empty string when provided")
        if not approved and reason is None:
            raise ValueError("reason is required when a candidate is rejected")
        status = "accepted" if approved else "rejected"
        event = Event(
            event_type="workflow.promotion.reviewed",
            task_id=candidate.task_id,
            provider=self._actor,
            payload={
                "actor": actor.strip(),
                "approval_reference": approval_reference.strip(),
                "review": status,
                "candidate_id": candidate.candidate_id,
                "candidate": candidate.to_dict(),
                "activation": "not_performed",
                "reason": reason.strip() if reason is not None else None,
            },
        )
        self._store.append_event(event)
        return event


class WorkflowPromotionCoordinator:
    """Coordinate evidence, proposal, and explicit review only."""

    def __init__(
        self,
        store,
        *,
        policy: WorkflowPromotionPolicy | None = None,
        recorder: WorkflowPromotionRecorder | None = None,
        actor: str = "host-workflow-evaluator",
    ) -> None:
        self._policy = policy or WorkflowPromotionPolicy()
        self._recorder = recorder or WorkflowPromotionRecorder(store, actor=actor)

    def evaluate_and_propose(self, evidence: WorkflowPromotionEvidence) -> WorkflowPromotionCycle:
        result = self._policy.evaluate(evidence)
        evaluation_event = self._recorder.record_result(result)
        if result.decision is not WorkflowPromotionDecision.ELIGIBLE:
            return WorkflowPromotionCycle(result=result, evaluation_event=evaluation_event)
        candidate = WorkflowPromotionCandidate(
            workflow_key=result.workflow_key,
            task_id=result.task_id,
            evidence=evidence,
            reasons=result.reasons,
        )
        proposal_event = self._recorder.record_proposal(candidate)
        return WorkflowPromotionCycle(
            result=result,
            evaluation_event=evaluation_event,
            candidate=candidate,
            proposal_event=proposal_event,
        )

    def review_candidate(
        self,
        cycle: WorkflowPromotionCycle,
        *,
        actor: str,
        approved: bool,
        approval_reference: str,
        reason: str | None = None,
    ) -> Event:
        if not isinstance(cycle, WorkflowPromotionCycle):
            raise TypeError("cycle must be WorkflowPromotionCycle")
        if cycle.candidate is None or cycle.proposal_event is None:
            raise ValueError("cycle does not contain a candidate to review")
        candidate = cycle.candidate
        proposal = cycle.proposal_event
        if proposal.event_type != "workflow.promotion.proposed" or proposal.task_id != candidate.task_id:
            raise ValueError("proposal event does not match the candidate")
        if proposal.payload.get("candidate_id") != candidate.candidate_id:
            raise ValueError("proposal event identity does not match the candidate")
        if cycle.result.decision is not WorkflowPromotionDecision.ELIGIBLE:
            raise ValueError("only eligible candidates can be reviewed")
        return self._recorder.record_review(
            candidate,
            actor=actor,
            approved=approved,
            approval_reference=approval_reference,
            reason=reason,
        )


__all__ = [
    "WorkflowPromotionCandidate",
    "WorkflowPromotionCoordinator",
    "WorkflowPromotionCycle",
    "WorkflowPromotionDecision",
    "WorkflowPromotionEvidence",
    "WorkflowPromotionPolicy",
    "WorkflowPromotionRecorder",
]
