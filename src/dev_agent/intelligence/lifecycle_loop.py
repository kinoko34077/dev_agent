"""Finite composition of host evaluation, review, dispatch, and Task state.

The existing evaluators, reviewed dispatch coordinator, and lifecycle
coordinator remain the owners of their respective contracts.  This module is
only a small caller-facing facade: it counts evaluation cycles, applies the
durable Task transition at each boundary, and stops when an explicit review or
reconciliation boundary is reached.  It never approves, retries, or invents
host evidence on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.protocol import Event, ModelRequest
from .escalation import EscalationContext
from .evaluator import EvaluationEvidence
from .lifecycle import TaskLifecycleCoordinator, TaskLifecycleTransition
from .loop import EvaluationDispatchCoordinator, EvaluationDispatchCycle, EvaluationDispatchStatus


class LifecycleLimitExceeded(RuntimeError):
    """The caller attempted to start more bounded evaluation cycles."""


@dataclass(frozen=True)
class LifecycleStep:
    """One evaluation or reviewed-dispatch boundary and its Task transition."""

    cycle_number: int
    phase: str
    dispatch_cycle: EvaluationDispatchCycle
    transition: TaskLifecycleTransition

    def __post_init__(self) -> None:
        if isinstance(self.cycle_number, bool) or not isinstance(self.cycle_number, int) or self.cycle_number <= 0:
            raise ValueError("cycle_number must be a positive integer")
        if self.phase not in {"evaluation", "dispatch"}:
            raise ValueError("phase must be evaluation or dispatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_number": self.cycle_number,
            "phase": self.phase,
            "status": self.dispatch_cycle.status.value,
            "dispatch_cycle": self.dispatch_cycle.to_dict(),
            "transition": {
                "task_id": self.transition.task.task_id,
                "task_status": self.transition.task.status.value,
                "event_id": self.transition.event.event_id,
                "event_type": self.transition.event.event_type,
                "replayed": self.transition.replayed,
            },
        }


class FiniteLifecycleLoop:
    """Connect existing Phase 7 boundaries while enforcing a finite budget.

    A normal call sequence is::

        evaluate_and_apply(...)
        # operator reviews the returned plan, if any
        dispatch_and_apply(step, review_event, model_request=...)
        # caller supplies fresh host evidence for the next evaluation

    The loop intentionally pauses between these calls.  It does not turn a
    missing review, an unknown external result, or a missing evidence sample
    into an automatic retry.
    """

    def __init__(
        self,
        evaluation_dispatch: EvaluationDispatchCoordinator,
        lifecycle: TaskLifecycleCoordinator,
        *,
        max_cycles: int = 3,
    ) -> None:
        if not isinstance(evaluation_dispatch, EvaluationDispatchCoordinator):
            raise TypeError("evaluation_dispatch must be EvaluationDispatchCoordinator")
        if not isinstance(lifecycle, TaskLifecycleCoordinator):
            raise TypeError("lifecycle must be TaskLifecycleCoordinator")
        if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles <= 0:
            raise ValueError("max_cycles must be a positive integer")
        self._evaluation_dispatch = evaluation_dispatch
        self._lifecycle = lifecycle
        self.max_cycles = max_cycles
        self._evaluations = 0

    @property
    def evaluations_used(self) -> int:
        return self._evaluations

    @property
    def remaining_cycles(self) -> int:
        return self.max_cycles - self._evaluations

    def evaluate_and_apply(
        self,
        evidence: EvaluationEvidence,
        *,
        escalation_context: EscalationContext | None = None,
    ) -> LifecycleStep:
        self._reserve_evaluation()
        cycle = self._evaluation_dispatch.evaluate(evidence, escalation_context=escalation_context)
        transition = self._lifecycle.apply_evaluation(cycle)
        return LifecycleStep(self._evaluations, "evaluation", cycle, transition)

    def dispatch_and_apply(
        self,
        step: LifecycleStep,
        review_event: Event,
        *,
        model_request: ModelRequest,
        provider_binding_id: str | None = None,
        dispatch_id: str | None = None,
        attempt: int | None = None,
    ) -> LifecycleStep:
        if not isinstance(step, LifecycleStep):
            raise TypeError("step must be LifecycleStep")
        if step.dispatch_cycle.status is not EvaluationDispatchStatus.AWAITING_REVIEW:
            raise ValueError("step is not awaiting explicit review")
        cycle = self._evaluation_dispatch.dispatch(
            step.dispatch_cycle,
            review_event,
            model_request=model_request,
            provider_binding_id=provider_binding_id,
            dispatch_id=dispatch_id,
            attempt=attempt,
        )
        transition = self._lifecycle.apply_dispatch(cycle)
        return LifecycleStep(step.cycle_number, "dispatch", cycle, transition)

    def _reserve_evaluation(self) -> None:
        if self._evaluations >= self.max_cycles:
            raise LifecycleLimitExceeded("finite lifecycle evaluation ceiling reached")
        self._evaluations += 1


__all__ = ["FiniteLifecycleLoop", "LifecycleLimitExceeded", "LifecycleStep"]
