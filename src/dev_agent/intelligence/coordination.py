"""Explicit Phase 7 evaluation-to-escalation coordination.

The coordinator joins two already bounded mechanisms without becoming an
agent runtime: deterministic host evidence is recorded durably, and only a
matching, finite escalation plan is returned to the caller.  Provider
dispatch, task mutation, and model self-promotion remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.protocol import Event
from .escalation import BoundedEscalationPolicy, EscalationContext, EscalationPlan
from .evaluator import EvaluationEvidence, EvaluationRecorder, EvaluationResult, EvaluatorDecision, TaskEvaluator


@dataclass(frozen=True)
class EvaluationCycle:
    """The durable evaluation event and optional bounded next-step plan."""

    result: EvaluationResult
    event: Event
    plan: EscalationPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "event": self.event.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
        }


class EvaluationCoordinator:
    """Record host evaluation and optionally produce one finite next step."""

    _PLAN_DECISIONS = frozenset(
        {
            EvaluatorDecision.RETRY_SAME,
            EvaluatorDecision.RETRY_OTHER_PROVIDER,
            EvaluatorDecision.ESCALATE,
        }
    )

    def __init__(
        self,
        store,
        *,
        evaluator: TaskEvaluator | None = None,
        recorder: EvaluationRecorder | None = None,
        escalation_policy: BoundedEscalationPolicy | None = None,
        actor: str = "host-evaluator",
    ) -> None:
        self._evaluator = evaluator or TaskEvaluator()
        self._recorder = recorder or EvaluationRecorder(store, actor=actor)
        self._escalation_policy = escalation_policy or BoundedEscalationPolicy()

    def evaluate_and_plan(
        self,
        evidence: EvaluationEvidence,
        *,
        escalation_context: EscalationContext | None = None,
    ) -> EvaluationCycle:
        """Persist one evaluation and return at most one bounded plan.

        The context is checked against the same task/attempt evidence before
        planning.  Only retry/escalation decisions receive a plan; PASS,
        WAIT_HUMAN, and FAIL are terminal or human-boundary outcomes for this
        coordinator and are never silently converted into a dispatch.
        """
        if escalation_context is not None:
            self._validate_context(evidence, escalation_context)

        result = self._evaluator.evaluate(evidence)
        event = self._recorder.record(result)
        plan = None
        if result.decision in self._PLAN_DECISIONS:
            if escalation_context is None:
                raise ValueError("escalation_context is required for a retry or escalation decision")
            plan = self._escalation_policy.plan(escalation_context)
            if plan.decision is not result.decision:
                raise ValueError("evaluation and escalation decisions diverged")
        return EvaluationCycle(result=result, event=event, plan=plan)

    @staticmethod
    def _validate_context(evidence: EvaluationEvidence, context: EscalationContext) -> None:
        if not isinstance(context, EscalationContext):
            raise TypeError("escalation_context must be EscalationContext")
        for name in ("task_id", "attempt", "max_attempts"):
            if getattr(context, name) != getattr(evidence, name):
                raise ValueError(f"escalation context {name} does not match evaluation evidence")
        for name in ("retryable_failure", "alternate_provider_available"):
            if getattr(context, name) != getattr(evidence, name):
                raise ValueError(f"escalation context {name} does not match evaluation evidence")


__all__ = ["EvaluationCoordinator", "EvaluationCycle"]
