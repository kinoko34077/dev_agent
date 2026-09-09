"""Independent, deterministic evaluation decisions for Phase 7C.

The evaluator consumes host-observed evidence.  It does not call the model
that produced a result, infer success from a model claim, or grant itself a
higher intelligence tier.  Persistence and orchestration are deliberately
left to a later integration slice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, TYPE_CHECKING

from ..domain.protocol import Event

if TYPE_CHECKING:
    from ..state.store import StateStore


class EvaluatorDecision(str, Enum):
    PASS = "PASS"
    RETRY_SAME = "RETRY_SAME"
    RETRY_OTHER_PROVIDER = "RETRY_OTHER_PROVIDER"
    ESCALATE = "ESCALATE"
    WAIT_HUMAN = "WAIT_HUMAN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class EvaluationEvidence:
    """Host-side evidence used to make one bounded evaluation decision."""

    task_id: str
    attempt: int
    max_attempts: int
    objective_met: bool | None
    deterministic_checks_passed: bool | None
    policy_compliant: bool
    external_outcome_known: bool
    retryable_failure: bool
    alternate_provider_available: bool
    human_approval_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        for name, value in (("attempt", self.attempt), ("max_attempts", self.max_attempts)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        for name, value in (
            ("objective_met", self.objective_met),
            ("deterministic_checks_passed", self.deterministic_checks_passed),
        ):
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")
        for name, value in (
            ("policy_compliant", self.policy_compliant),
            ("external_outcome_known", self.external_outcome_known),
            ("retryable_failure", self.retryable_failure),
            ("alternate_provider_available", self.alternate_provider_available),
            ("human_approval_required", self.human_approval_required),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(self, "task_id", self.task_id.strip())


@dataclass(frozen=True)
class EvaluationResult:
    task_id: str
    decision: EvaluatorDecision
    reasons: tuple[str, ...]
    evidence: EvaluationEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "evidence": asdict(self.evidence),
        }


class TaskEvaluator:
    """Turn deterministic evidence into a finite, auditable decision."""

    def evaluate(self, evidence: EvaluationEvidence) -> EvaluationResult:
        if not isinstance(evidence, EvaluationEvidence):
            raise TypeError("evidence must be EvaluationEvidence")

        if not evidence.policy_compliant:
            return self._result(evidence, EvaluatorDecision.WAIT_HUMAN, "policy_non_compliant")
        if evidence.human_approval_required:
            return self._result(evidence, EvaluatorDecision.WAIT_HUMAN, "human_approval_required")
        if not evidence.external_outcome_known:
            return self._result(evidence, EvaluatorDecision.WAIT_HUMAN, "external_outcome_unknown")

        if evidence.objective_met is True and evidence.deterministic_checks_passed is True:
            return self._result(
                evidence,
                EvaluatorDecision.PASS,
                "objective_met",
                "deterministic_checks_passed",
            )

        failure_reasons = tuple(
            reason
            for reason, failed in (
                ("objective_not_met", evidence.objective_met is False),
                ("deterministic_checks_failed", evidence.deterministic_checks_passed is False),
            )
            if failed
        )
        if evidence.attempt >= evidence.max_attempts:
            return self._result(evidence, EvaluatorDecision.FAIL, *failure_reasons, "attempt_ceiling_reached")
        if evidence.retryable_failure and evidence.alternate_provider_available:
            return self._result(evidence, EvaluatorDecision.RETRY_OTHER_PROVIDER, *failure_reasons, "retryable_failure")
        if evidence.retryable_failure:
            return self._result(evidence, EvaluatorDecision.RETRY_SAME, *failure_reasons, "retryable_failure")
        return self._result(evidence, EvaluatorDecision.ESCALATE, *failure_reasons, "deterministic_evidence_incomplete")

    @staticmethod
    def _result(evidence: EvaluationEvidence, decision: EvaluatorDecision, *reasons: str) -> EvaluationResult:
        return EvaluationResult(
            task_id=evidence.task_id,
            decision=decision,
            reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
            evidence=evidence,
        )


class EvaluationRecorder:
    """Persist evaluator evidence through the existing durable Event API."""

    def __init__(self, store: "StateStore", *, actor: str = "host-evaluator") -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        self._store = store
        self._actor = actor.strip()

    def record(self, result: EvaluationResult) -> Event:
        if not isinstance(result, EvaluationResult):
            raise TypeError("result must be EvaluationResult")
        event = Event(
            event_type="evaluation.recorded",
            task_id=result.task_id,
            provider=self._actor,
            payload={
                "actor": self._actor,
                "decision": result.decision.value,
                "reasons": list(result.reasons),
                "evidence": result.to_dict()["evidence"],
            },
        )
        self._store.append_event(event)
        return event


__all__ = ["EvaluationEvidence", "EvaluationRecorder", "EvaluationResult", "EvaluatorDecision", "TaskEvaluator"]
