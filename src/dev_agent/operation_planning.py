"""Host-validated planning operations for the human-facing Operation facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from .domain.protocol import Event as ProtocolEvent
from .domain.protocol import Task, TaskStatus
from .intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
    PlanningValidationError,
    RootPlanningProposal,
    RootPlanningValidator,
)
from .runtime.task_graph import TaskGraph, TaskGraphError
from .scheduler.queue import DurableQueue
from .state.sqlite_store import SQLiteStateStore


@dataclass(frozen=True)
class PlanningContext:
    """One caller-scoped view of durable planning state."""

    parent: Task
    payloads: Mapping[str, Any]
    tasks: tuple[Task, ...]
    graph: TaskGraph


def build_context(store: SQLiteStateStore, parent_task_id: str) -> PlanningContext:
    parent = store.load_task(parent_task_id)
    if parent is None:
        raise PlanningValidationError(f"parent task not found: {parent_task_id}")
    snapshot = store.snapshot()
    payloads = snapshot.get("tasks", {})
    if not isinstance(payloads, Mapping):
        payloads = {}
    persisted = tuple(
        Task.from_persisted_dict(payload)
        for payload in payloads.values()
        if isinstance(payload, dict)
    )
    return PlanningContext(
        parent=parent,
        payloads=payloads,
        tasks=persisted,
        graph=TaskGraph.from_tasks(persisted),
    )


def validate_proposal(
    proposal: RootPlanningProposal,
    *,
    context: PlanningContext,
) -> tuple[ChildTaskProposal, ...]:
    if not isinstance(proposal, RootPlanningProposal):
        raise TypeError("proposal must be a RootPlanningProposal")
    return RootPlanningValidator.validate(
        context.parent,
        proposal,
        existing_tasks=context.tasks,
    )


def apply_proposal(
    store: SQLiteStateStore,
    queue: DurableQueue,
    proposal: RootPlanningProposal,
    *,
    priority: int = 0,
    context: PlanningContext,
) -> tuple[Task, ...]:
    """Persist a finite, host-validated decomposition using existing queue/state."""

    children = validate_proposal(proposal, context=context)
    parent = context.parent
    existing = context.payloads
    if any(
        isinstance(payload, dict)
        and isinstance(payload.get("metadata"), dict)
        and payload["metadata"].get("planning_proposal_id") == proposal.proposal_id
        for payload in existing.values()
    ):
        raise PlanningValidationError("planning proposal has already been applied")

    created: list[Task] = []
    for child in children:
        dependencies = list(child.dependencies)
        status = TaskStatus.WAITING_DEPENDENCY if dependencies else TaskStatus.QUEUED
        metadata = {
            "planning_proposal_id": proposal.proposal_id,
            "planner_child_key": child.child_key,
        }
        if dependencies:
            metadata["wait_reason"] = "planner_dependency"
            metadata["planner_dependency_types"] = {
                dependency: child.dependency_types.get(dependency, PlannerDependencyType.TASK_COMPLETED).value
                for dependency in dependencies
            }
        task = Task(
            objective=child.objective,
            parent_task_id=parent.task_id,
            root_task_id=parent.task_id,
            depth=parent.depth + 1,
            status=status,
            task_type=child.task_type,
            risk=child.risk,
            sensitivity=child.sensitivity or parent.sensitivity,
            required_capabilities=list(child.required_capabilities),
            constraints={
                "planner_proposal_id": proposal.proposal_id,
                "planner_child_key": child.child_key,
                "planner_dependencies": dependencies,
                "planner_dependency_types": {
                    dependency: child.dependency_types.get(dependency, PlannerDependencyType.TASK_COMPLETED).value
                    for dependency in dependencies
                },
            },
            metadata=metadata,
        )
        try:
            context.graph.add(task)
        except TaskGraphError as exc:
            raise PlanningValidationError(str(exc)) from exc
        created.append(task)

    for task in created:
        store.save_task(task)
        if task.status is TaskStatus.QUEUED:
            queue.enqueue(task.task_id, priority=priority, max_attempts=task.limits.max_retries + 1)
    return tuple(created)


def release_dependencies(
    store: SQLiteStateStore,
    queue: DurableQueue,
    *,
    proposal_id: str | None = None,
) -> tuple[Task, ...]:
    """Release or terminalize planner children from durable state."""

    payloads = store.snapshot().get("tasks", {})
    tasks = tuple(
        Task.from_persisted_dict(payload)
        for payload in payloads.values()
        if isinstance(payload, dict)
    )
    by_proposal: dict[str, dict[str, Task]] = {}
    waiting: list[Task] = []
    for task in tasks:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        current_proposal = metadata.get("planning_proposal_id")
        if not isinstance(current_proposal, str) or not current_proposal.strip():
            continue
        if proposal_id is not None and current_proposal != proposal_id:
            continue
        child_key = metadata.get("planner_child_key")
        if isinstance(child_key, str) and child_key.strip():
            by_proposal.setdefault(current_proposal, {})[child_key] = task
        if task.status is TaskStatus.WAITING_DEPENDENCY and metadata.get("wait_reason") == "planner_dependency":
            waiting.append(task)

    changed: list[Task] = []
    for task in waiting:
        metadata = task.metadata
        current_proposal = metadata.get("planning_proposal_id")
        child_key = metadata.get("planner_child_key")
        constraints = task.constraints if isinstance(task.constraints, dict) else {}
        dependencies = constraints.get("planner_dependencies", ())
        if not isinstance(current_proposal, str) or not isinstance(child_key, str) or not isinstance(dependencies, list):
            continue
        siblings = by_proposal.get(current_proposal, {})
        dependency_tasks = [siblings.get(item) for item in dependencies if isinstance(item, str)]
        if len(dependency_tasks) != len(dependencies) or any(item is None for item in dependency_tasks):
            continue
        failed = next((item for item in dependency_tasks if item.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}), None)
        if failed is not None:
            task.status = TaskStatus.FAILED
            task.metadata.pop("wait_reason", None)
            task.metadata["planner_dependency_state"] = "failed"
            task.metadata["planner_failed_dependency"] = failed.task_id
            event_type = "task.planner_dependency_failed"
            payload = {"dependency_task_id": failed.task_id, "dependency_status": failed.status.value}
        dependency_types = constraints.get("planner_dependency_types", {})
        if not isinstance(dependency_types, Mapping):
            dependency_types = {}
        dependency_evidence = [
            _dependency_satisfied(
                dependency_task,
                dependency_types.get(dependency_key, PlannerDependencyType.TASK_COMPLETED.value),
            )
            for dependency_key, dependency_task in zip(dependencies, dependency_tasks)
        ]
        if all(dependency_evidence):
            task.status = TaskStatus.QUEUED
            task.metadata.pop("wait_reason", None)
            task.metadata["planner_dependency_state"] = "released"
            event_type = "task.planner_dependency_released"
            payload = {
                "dependency_task_ids": [item.task_id for item in dependency_tasks],
                "dependency_types": {
                    dependency_key: dependency_types.get(dependency_key, PlannerDependencyType.TASK_COMPLETED.value)
                    for dependency_key in dependencies
                },
            }
        else:
            continue
        event = ProtocolEvent(
            event_id=str(uuid5(NAMESPACE_URL, f"dev-agent:planner:{current_proposal}:{task.task_id}:{event_type}")),
            task_id=task.task_id,
            event_type=event_type,
            payload={"proposal_id": current_proposal, "child_key": child_key, **payload},
        )
        store.commit_transition(task=task, event=event)
        if task.status is TaskStatus.QUEUED:
            queue.enqueue(task.task_id, priority=0, max_attempts=task.limits.max_retries + 1)
        changed.append(task)
    return tuple(changed)


def _dependency_satisfied(task: Task, dependency_type: PlannerDependencyType | str) -> bool:
    """Check only the evidence named by a planner dependency."""

    try:
        normalized = dependency_type if isinstance(dependency_type, PlannerDependencyType) else PlannerDependencyType(dependency_type)
    except (TypeError, ValueError):
        return False
    if normalized is PlannerDependencyType.TASK_COMPLETED:
        return task.status is TaskStatus.COMPLETED
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    if normalized is PlannerDependencyType.ARTIFACT_READY:
        return metadata.get("artifact_ready") is True
    integration_revision = metadata.get("integration_revision")
    return (
        normalized is PlannerDependencyType.CODE_INTEGRATED
        and metadata.get("integration_status") == "INTEGRATED"
        and isinstance(integration_revision, str)
        and bool(integration_revision.strip())
    )
