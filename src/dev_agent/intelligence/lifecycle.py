"""Explicit Task-state transitions for the Phase 7 evaluation cycle.

The lifecycle component is deliberately separate from both the Controller and
the Provider dispatcher.  It applies only host-produced evaluation or
execution outcomes, writes the Task and its transition event through the
StateStore transaction facade, and uses a deterministic event identity so a
replayed cycle is harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from ..domain.protocol import Event, Task, TaskStatus
from .evaluator import EvaluatorDecision
from .loop import EvaluationDispatchCycle, EvaluationDispatchStatus


@dataclass(frozen=True)
class TaskLifecycleTransition:
    """One atomically persisted Task state transition."""

    task: Task
    event: Event
    replayed: bool = False


class TaskLifecycleCoordinator:
    """Apply host evaluation and reviewed-dispatch outcomes to a Task."""

    def __init__(self, store, *, actor: str = "host-task-lifecycle") -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        self._store = store
        self._actor = actor.strip()

    def apply_evaluation(self, cycle: EvaluationDispatchCycle) -> TaskLifecycleTransition:
        """Persist the Task state implied by one evaluated cycle.

        A retry or escalation is parked as ``READY`` until its explicit review
        and dispatch occur.  PASS/FAIL become terminal.  ``WAIT_HUMAN`` is
        represented by the existing approval state, except for an unknown
        external outcome, which remains in reconciliation.
        """

        if not isinstance(cycle, EvaluationDispatchCycle):
            raise TypeError("cycle must be EvaluationDispatchCycle")
        result = cycle.evaluation.result
        task = self._load_task(result.task_id)
        decision = result.decision
        if decision is EvaluatorDecision.PASS:
            event_type = "task.completed"
            target = TaskStatus.COMPLETED
            payload = {"source": "host_evaluator", "decision": decision.value}
        elif decision is EvaluatorDecision.FAIL:
            event_type = "task.failed"
            target = TaskStatus.FAILED
            payload = {
                "source": "host_evaluator",
                "decision": decision.value,
                "reasons": list(result.reasons),
            }
        elif decision is EvaluatorDecision.WAIT_HUMAN:
            if not result.evidence.external_outcome_known:
                event_type = "task.waiting_reconciliation"
                target = TaskStatus.WAITING_RECONCILIATION
                payload = {
                    "source": "host_evaluator",
                    "decision": decision.value,
                    "category": "reconciliation_required",
                    "cause": "external_outcome_unknown",
                }
            else:
                event_type = "task.waiting_approval"
                target = TaskStatus.WAITING_APPROVAL
                payload = {
                    "source": "host_evaluator",
                    "decision": decision.value,
                    "category": "human_approval_required",
                }
        elif decision in {
            EvaluatorDecision.RETRY_SAME,
            EvaluatorDecision.RETRY_OTHER_PROVIDER,
            EvaluatorDecision.ESCALATE,
        }:
            if cycle.evaluation.plan is None:
                raise ValueError("retry or escalation evaluation must contain a plan")
            event_type = "task.retry_scheduled"
            target = TaskStatus.READY
            payload = {
                "source": "host_evaluator",
                "decision": decision.value,
                "plan_id": cycle.evaluation.plan.plan_id,
                "target": cycle.evaluation.plan.target.value,
                "attempt": result.evidence.attempt,
                "next_attempt": result.evidence.attempt + 1,
            }
        else:
            raise ValueError(f"unsupported evaluator decision: {decision}")

        return self._transition(
            task,
            event_type=event_type,
            target=target,
            payload=payload,
            identity=f"evaluation:{cycle.evaluation.event.event_id}",
        )

    def apply_dispatch(self, cycle: EvaluationDispatchCycle) -> TaskLifecycleTransition:
        """Persist the Task state implied by a reviewed dispatch outcome."""

        if not isinstance(cycle, EvaluationDispatchCycle):
            raise TypeError("cycle must be EvaluationDispatchCycle")
        result = cycle.execution
        if cycle.status is EvaluationDispatchStatus.DISPATCHED:
            if result is None or result.response is None:
                raise ValueError("dispatched cycle must contain a response")
            event_type = "task.dispatch_started"
            target = TaskStatus.RUNNING
            payload = {
                "source": "escalation_executor",
                "dispatch_id": result.dispatch_id,
                "plan_id": result.plan_id,
                "attempt": result.attempt,
                "provider_binding_id": result.provider_binding_id,
                "status": result.status.value,
            }
            identity = f"dispatch:{result.dispatch_id}:started"
        elif cycle.status is EvaluationDispatchStatus.RECONCILIATION_REQUIRED:
            if result is None:
                raise ValueError("reconciliation cycle must contain an execution result")
            event_type = "task.waiting_reconciliation"
            target = TaskStatus.WAITING_RECONCILIATION
            payload = {
                "source": "escalation_executor",
                "category": "reconciliation_required",
                "dispatch_id": result.dispatch_id,
                "plan_id": result.plan_id,
                "attempt": result.attempt,
                "provider_binding_id": result.provider_binding_id,
                "error_category": result.error_category,
            }
            identity = f"dispatch:{result.dispatch_id}:reconciliation"
        elif cycle.status is EvaluationDispatchStatus.REVIEW_REJECTED:
            if cycle.review_event is None:
                raise ValueError("rejected cycle must contain its review event")
            plan_id = cycle.review_event.payload.get("plan_id")
            event_type = "task.waiting_approval"
            target = TaskStatus.WAITING_APPROVAL
            payload = {
                "source": "host_review",
                "category": "escalation_rejected",
                "plan_id": plan_id,
                "approval_reference": cycle.review_event.payload.get("approval_reference"),
            }
            identity = f"review:{cycle.review_event.event_id}:rejected"
        else:
            raise ValueError("cycle has no dispatch outcome to apply")

        task = self._load_task(cycle.evaluation.result.task_id)
        return self._transition(
            task,
            event_type=event_type,
            target=target,
            payload=payload,
            identity=identity,
        )

    def _transition(
        self,
        task: Task,
        *,
        event_type: str,
        target: TaskStatus,
        payload: dict[str, object],
        identity: str,
    ) -> TaskLifecycleTransition:
        event = Event(
            event_id=str(uuid5(NAMESPACE_URL, f"dev-agent/task-lifecycle/{identity}/{event_type}")),
            event_type=event_type,
            task_id=task.task_id,
            provider=self._actor,
            payload=payload,
        )
        if self._has_event(event.event_id):
            return TaskLifecycleTransition(task=task, event=event, replayed=True)
        self._ensure_transition_allowed(task.status, target)
        task.status = target
        self._store.commit_transition(task=task, event=event)
        return TaskLifecycleTransition(task=task, event=event)

    def _load_task(self, task_id: str) -> Task:
        task = self._store.load_task(task_id)
        if task is None:
            raise ValueError(f"task state is missing: {task_id}")
        return task

    def _has_event(self, event_id: str) -> bool:
        snapshot = self._store.snapshot()
        events = snapshot.get("events", []) if isinstance(snapshot, dict) else []
        return any(isinstance(item, dict) and item.get("event_id") == event_id for item in events)

    @staticmethod
    def _ensure_transition_allowed(current: TaskStatus, target: TaskStatus) -> None:
        if current is target:
            return
        if current is TaskStatus.CANCELLED:
            raise ValueError("cancelled task cannot be resumed by evaluator lifecycle")
        if current is TaskStatus.COMPLETED and target is not TaskStatus.COMPLETED:
            raise ValueError("completed task cannot move back into execution")
        if current is TaskStatus.WAITING_RECONCILIATION and target not in {
            TaskStatus.WAITING_RECONCILIATION,
            TaskStatus.WAITING_APPROVAL,
        }:
            raise ValueError("reconciliation must be resolved before execution resumes")


__all__ = ["TaskLifecycleCoordinator", "TaskLifecycleTransition"]
