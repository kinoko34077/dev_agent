"""Explicit Phase 7 evaluation-to-escalation coordination.

The coordinator joins two already bounded mechanisms without becoming an
agent runtime: deterministic host evidence is recorded durably, and only a
matching, finite escalation plan is returned to the caller.  Provider
dispatch, task mutation, and model self-promotion remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..domain.protocol import Event
from .escalation import BoundedEscalationPolicy, EscalationContext, EscalationDispatchRequest, EscalationPlan
from .evaluator import EvaluationEvidence, EvaluationRecorder, EvaluationResult, EvaluatorDecision, TaskEvaluator


@dataclass(frozen=True)
class EvaluationCycle:
    """The durable evaluation event and optional bounded next-step plan."""

    result: EvaluationResult
    event: Event
    plan: EscalationPlan | None = None
    plan_event: Event | None = None
    escalation_context: EscalationContext | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "event": self.event.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "plan_event": self.plan_event.to_dict() if self.plan_event is not None else None,
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
        plan_event = self._recorder.record_plan(plan) if plan is not None else None
        return EvaluationCycle(
            result=result,
            event=event,
            plan=plan,
            plan_event=plan_event,
            escalation_context=escalation_context,
        )

    def review_plan(
        self,
        cycle: EvaluationCycle,
        *,
        actor: str,
        approved: bool,
        approval_reference: str,
        reason: str | None = None,
    ) -> Event:
        """Record explicit host acceptance or rejection of a returned plan.

        Review is intentionally the final operation in this boundary.  It
        records authority and the exact plan identity, but it never dispatches
        a Provider, mutates a Task, or applies a Worker artifact.
        """
        if not isinstance(cycle, EvaluationCycle):
            raise TypeError("cycle must be EvaluationCycle")
        plan = self._validated_plan(cycle, purpose="review")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean")
        return self._recorder.record_review(
            plan,
            actor=actor,
            approved=approved,
            approval_reference=approval_reference,
            reason=reason,
        )

    def prepare_dispatch(
        self,
        cycle: EvaluationCycle,
        review_event: Event,
        *,
        provider_binding_id: str | None = None,
        dispatch_id: str | None = None,
        attempt: int | None = None,
    ) -> EscalationDispatchRequest:
        """Create a durable, approved dispatch handoff without dispatching.

        The returned request intentionally omits provider selection and model
        input.  A later executor must re-check task state, policy, quota,
        budget, and lease ownership through the existing control plane.
        """
        plan = self._validated_plan(cycle, purpose="dispatch")
        if not isinstance(review_event, Event):
            raise TypeError("review_event must be Event")
        if review_event.event_type != "escalation.accepted":
            raise ValueError("dispatch requires an accepted plan review")
        if review_event.task_id != plan.task_id:
            raise ValueError("review event task does not match the plan")
        payload = review_event.payload
        if payload.get("review") != "accepted":
            raise ValueError("dispatch requires an accepted plan review")
        if payload.get("plan_id") != plan.plan_id:
            raise ValueError("review event plan does not match the plan")
        approved_by = payload.get("actor")
        approval_reference = payload.get("approval_reference")
        request = EscalationDispatchRequest(
            task_id=plan.task_id,
            plan_id=plan.plan_id,
            decision=plan.decision,
            target=plan.target,
            next_tier=plan.next_tier,
            approved_by=approved_by,
            approval_reference=approval_reference,
            dispatch_id=dispatch_id or str(uuid4()),
            attempt=(attempt if attempt is not None else (cycle.escalation_context.attempt + 1 if cycle.escalation_context is not None else 1)),
            provider_binding_id=provider_binding_id,
            current_tier=cycle.escalation_context.current_tier if cycle.escalation_context is not None else None,
            allowed_tiers=cycle.escalation_context.allowed_tiers if cycle.escalation_context is not None else None,
        )
        self._recorder.record_dispatch_ready(request)
        return request

    @staticmethod
    def _validated_plan(cycle: EvaluationCycle, *, purpose: str) -> EscalationPlan:
        if not isinstance(cycle, EvaluationCycle):
            raise TypeError("cycle must be EvaluationCycle")
        if cycle.plan is None or cycle.plan_event is None:
            raise ValueError(f"cycle does not contain a plan to {purpose}")
        plan = cycle.plan
        plan_event = cycle.plan_event
        if plan_event.event_type != "escalation.planned" or plan_event.task_id != plan.task_id:
            raise ValueError("plan event does not match the plan")
        if plan_event.payload.get("plan_id") != plan.plan_id:
            raise ValueError("plan event identity does not match the plan")
        if cycle.result.task_id != plan.task_id or cycle.result.decision is not plan.decision:
            raise ValueError("evaluation result does not match the plan")
        return plan

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
