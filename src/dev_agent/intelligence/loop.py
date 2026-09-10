"""A thin, explicit bridge from host evaluation to bounded dispatch.

``EvaluationCoordinator`` owns deterministic evidence, planning, and review
events.  ``EscalationExecutor`` owns the safety recheck and canonical Provider
dispatch.  This module only joins those existing boundaries for one cycle; it
does not approve a plan, mutate a Task, or create another effect ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain.protocol import Event, ModelRequest
from .coordination import EvaluationCoordinator, EvaluationCycle
from .escalation import EscalationContext, EscalationDispatchRequest
from .evaluator import EvaluationEvidence
from .execution import (
    EscalationExecutionError,
    EscalationExecutionResult,
    EscalationExecutionStatus,
    EscalationExecutor,
)


class EvaluationDispatchStatus(str, Enum):
    """Outcome of one host-controlled evaluation/dispatch cycle."""

    TERMINAL = "terminal"
    AWAITING_REVIEW = "awaiting_review"
    REVIEW_REJECTED = "review_rejected"
    DISPATCHED = "dispatched"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True)
class EvaluationDispatchCycle:
    """Durable evaluation context plus an optional reviewed dispatch result."""

    evaluation: EvaluationCycle
    status: EvaluationDispatchStatus
    review_event: Event | None = None
    dispatch_request: EscalationDispatchRequest | None = None
    execution: EscalationExecutionResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation": self.evaluation.to_dict(),
            "status": self.status.value,
            "review_event": self.review_event.to_dict() if self.review_event is not None else None,
            "dispatch_request": self.dispatch_request.to_dict() if self.dispatch_request is not None else None,
            "execution": self.execution.to_dict() if self.execution is not None else None,
        }


class EvaluationDispatchCoordinator:
    """Join one evaluation, explicit review, and one bounded dispatch.

    The two public methods intentionally preserve the human boundary:
    :meth:`evaluate` never dispatches, and :meth:`dispatch` accepts only an
    already-recorded review event.  A caller may therefore persist or inspect
    the evaluation before deciding whether to continue.
    """

    def __init__(
        self,
        store,
        *,
        evaluation_coordinator: EvaluationCoordinator | None = None,
        executor: EscalationExecutor | None = None,
    ) -> None:
        self._evaluation = evaluation_coordinator or EvaluationCoordinator(store)
        self._executor = executor

    def evaluate(
        self,
        evidence: EvaluationEvidence,
        *,
        escalation_context: EscalationContext | None = None,
    ) -> EvaluationDispatchCycle:
        """Record host evidence and expose either a terminal result or review wait."""

        cycle = self._evaluation.evaluate_and_plan(
            evidence,
            escalation_context=escalation_context,
        )
        status = (
            EvaluationDispatchStatus.AWAITING_REVIEW
            if cycle.plan is not None
            else EvaluationDispatchStatus.TERMINAL
        )
        return EvaluationDispatchCycle(evaluation=cycle, status=status)

    def dispatch(
        self,
        cycle: EvaluationDispatchCycle,
        review_event: Event | None,
        *,
        model_request: ModelRequest,
        provider_binding_id: str | None = None,
        dispatch_id: str | None = None,
        attempt: int | None = None,
    ) -> EvaluationDispatchCycle:
        """Dispatch one explicitly reviewed plan through ``EscalationExecutor``.

        Rejected reviews are returned as a non-dispatching outcome.  Accepted
        reviews are converted to a durable handoff by the existing
        ``EvaluationCoordinator`` and then executed by the injected executor.
        No model/provider or credential is accepted from the review event.
        """

        if not isinstance(cycle, EvaluationDispatchCycle):
            raise TypeError("cycle must be EvaluationDispatchCycle")
        if cycle.evaluation.plan is None:
            raise ValueError("evaluation cycle has no dispatchable plan")
        if cycle.status is not EvaluationDispatchStatus.AWAITING_REVIEW:
            raise ValueError("evaluation cycle is no longer awaiting review")
        if not isinstance(review_event, Event):
            raise TypeError("review_event must be Event")
        if not isinstance(model_request, ModelRequest):
            raise TypeError("model_request must be ModelRequest")
        plan = cycle.evaluation.plan
        if model_request.task_id != plan.task_id or review_event.task_id != plan.task_id:
            raise ValueError("review and model request task must match the plan")
        if review_event.payload.get("plan_id") != plan.plan_id:
            raise ValueError("review event plan does not match the plan")
        if review_event.event_type == "escalation.rejected":
            return EvaluationDispatchCycle(
                evaluation=cycle.evaluation,
                status=EvaluationDispatchStatus.REVIEW_REJECTED,
                review_event=review_event,
            )
        if review_event.event_type != "escalation.accepted":
            raise ValueError("dispatch requires an accepted or rejected plan review")
        if self._executor is None:
            raise ValueError("an EscalationExecutor is required for accepted dispatch")

        request = self._evaluation.prepare_dispatch(
            cycle.evaluation,
            review_event,
            provider_binding_id=provider_binding_id,
            dispatch_id=dispatch_id,
            attempt=attempt,
        )
        try:
            execution = self._executor.execute(request, model_request)
        except EscalationExecutionError as exc:
            if not exc.requires_reconciliation:
                raise
            # EscalationExecutor deliberately raises for an unresolved
            # external outcome so direct callers cannot mistake it for
            # success.  At this explicit lifecycle boundary, preserve that
            # safety signal as a typed cycle that TaskLifecycleCoordinator can
            # durably park in WAITING_RECONCILIATION.
            execution = EscalationExecutionResult(
                dispatch_id=request.dispatch_id,
                plan_id=request.plan_id,
                task_id=request.task_id,
                attempt=request.attempt,
                status=EscalationExecutionStatus.UNKNOWN,
                provider_binding_id=request.provider_binding_id,
                error_category=exc.category,
            )
            return EvaluationDispatchCycle(
                evaluation=cycle.evaluation,
                status=EvaluationDispatchStatus.RECONCILIATION_REQUIRED,
                review_event=review_event,
                dispatch_request=request,
                execution=execution,
            )
        status = (
            EvaluationDispatchStatus.RECONCILIATION_REQUIRED
            if execution.status.value == "unknown"
            else EvaluationDispatchStatus.DISPATCHED
        )
        return EvaluationDispatchCycle(
            evaluation=cycle.evaluation,
            status=status,
            review_event=review_event,
            dispatch_request=request,
            execution=execution,
        )


__all__ = [
    "EvaluationDispatchCoordinator",
    "EvaluationDispatchCycle",
    "EvaluationDispatchStatus",
]
