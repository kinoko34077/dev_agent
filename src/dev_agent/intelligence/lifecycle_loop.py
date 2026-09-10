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
        task_id: str | None = None,
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
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a non-empty string or None")
        self._task_id = task_id.strip() if isinstance(task_id, str) else None
        self._evaluations = self._durable_evaluation_count(self._task_id) if self._task_id else 0

    @property
    def evaluations_used(self) -> int:
        if self._task_id is not None:
            self._evaluations = self._durable_evaluation_count(self._task_id)
        return self._evaluations

    @property
    def remaining_cycles(self) -> int:
        return self.max_cycles - self.evaluations_used

    def evaluate_and_apply(
        self,
        evidence: EvaluationEvidence,
        *,
        escalation_context: EscalationContext | None = None,
    ) -> LifecycleStep:
        self._reserve_evaluation(evidence.task_id)
        cycle = self._evaluation_dispatch.evaluate(evidence, escalation_context=escalation_context)
        self._evaluations = self._durable_evaluation_count(self._task_id)
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

    def _reserve_evaluation(self, task_id: str) -> None:
        if self._task_id is None:
            self._task_id = task_id
        elif self._task_id != task_id:
            raise ValueError("evaluation task does not match the lifecycle task")
        self._evaluations = self._durable_evaluation_count(self._task_id)
        if self._evaluations >= self.max_cycles:
            raise LifecycleLimitExceeded("finite lifecycle evaluation ceiling reached")
        task = self._evaluation_dispatch.state_store.load_task(self._task_id)
        if task is not None and task.status.value in {"completed", "failed", "cancelled"} and self._evaluations > 0:
            raise ValueError("terminal task cannot resume in finite lifecycle")
        if task is not None and task.status.value in {"waiting_approval", "waiting_reconciliation"} and self._evaluations > 0:
            raise ValueError("waiting task requires explicit resolution before lifecycle resume")

    def _durable_evaluation_count(self, task_id: str | None) -> int:
        if task_id is None:
            return 0
        snapshot = self._evaluation_dispatch.state_store.snapshot()
        events = snapshot.get("events", []) if isinstance(snapshot, dict) else []
        return sum(
            1
            for event in events
            if isinstance(event, dict)
            and event.get("task_id") == task_id
            and event.get("event_type") == "evaluation.recorded"
        )


__all__ = ["FiniteLifecycleLoop", "LifecycleLimitExceeded", "LifecycleStep"]
