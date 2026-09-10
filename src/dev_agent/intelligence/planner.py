"""Host-validated, finite planning proposals for root Task decomposition.

The planner types are deliberately proposals, not Tasks and not authority
records. A model or Commander may produce them, but only the host validator
may turn a validated proposal into durable child Tasks through the existing
TaskGraph and Operation boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from ..domain.protocol import RiskLevel, Task, TaskType
from .capabilities import classify_task_capabilities


_SENSITIVITY_RANK = {"public": 0, "normal": 1, "internal": 2, "sensitive": 3}


class PlanningValidationError(ValueError):
    """A planning proposal is not safe to convert into child Tasks."""


@dataclass(frozen=True)
class ChildTaskProposal:
    """One bounded child proposal before TaskGraph insertion."""

    child_key: str
    objective: str
    task_type: TaskType
    risk: RiskLevel = RiskLevel.NORMAL
    sensitivity: str | None = None
    required_capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    suggested_owner: str = "worker"

    def __post_init__(self) -> None:
        if not isinstance(self.child_key, str) or not self.child_key.strip():
            raise PlanningValidationError("child_key must be a non-empty string")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise PlanningValidationError("child objective must be a non-empty string")
        try:
            task_type = self.task_type if isinstance(self.task_type, TaskType) else TaskType(self.task_type)
        except (TypeError, ValueError) as exc:
            raise PlanningValidationError("invalid child task_type") from exc
        try:
            risk = self.risk if isinstance(self.risk, RiskLevel) else RiskLevel(self.risk)
        except (TypeError, ValueError) as exc:
            raise PlanningValidationError("invalid child risk") from exc
        sensitivity = self.sensitivity
        if sensitivity is not None:
            if not isinstance(sensitivity, str) or sensitivity.strip().lower() not in _SENSITIVITY_RANK:
                raise PlanningValidationError("child sensitivity must be public, normal, internal, or sensitive")
            sensitivity = sensitivity.strip().lower()
        owner = self.suggested_owner.strip().lower() if isinstance(self.suggested_owner, str) else ""
        if owner not in {"worker", "codex"}:
            raise PlanningValidationError("suggested_owner must be worker or codex")
        capabilities = _string_tuple(self.required_capabilities, "required_capabilities")
        dependencies = _string_tuple(self.dependencies, "dependencies")
        object.__setattr__(self, "child_key", self.child_key.strip())
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "sensitivity", sensitivity)
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "suggested_owner", owner)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["task_type"] = self.task_type.value
        value["risk"] = self.risk.value
        value["required_capabilities"] = list(self.required_capabilities)
        value["dependencies"] = list(self.dependencies)
        return value


@dataclass(frozen=True)
class RootPlanningProposal:
    """A finite, host-reviewable decomposition proposal."""

    parent_task_id: str
    rationale: str
    children: tuple[ChildTaskProposal, ...]
    planning_cycle: int = 1
    proposal_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.parent_task_id, str) or not self.parent_task_id.strip():
            raise PlanningValidationError("parent_task_id must be a non-empty string")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise PlanningValidationError("planning rationale must be a non-empty string")
        if isinstance(self.planning_cycle, bool) or not isinstance(self.planning_cycle, int) or self.planning_cycle <= 0:
            raise PlanningValidationError("planning_cycle must be a positive integer")
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise PlanningValidationError("proposal_id must be a non-empty string")
        if not isinstance(self.children, tuple) or any(not isinstance(item, ChildTaskProposal) for item in self.children):
            raise PlanningValidationError("children must be a tuple of ChildTaskProposal")
        object.__setattr__(self, "parent_task_id", self.parent_task_id.strip())
        object.__setattr__(self, "rationale", self.rationale.strip())
        object.__setattr__(self, "proposal_id", self.proposal_id.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_task_id": self.parent_task_id,
            "rationale": self.rationale,
            "planning_cycle": self.planning_cycle,
            "proposal_id": self.proposal_id,
            "children": [child.to_dict() for child in self.children],
        }


class RootPlanningValidator:
    """Validate a proposal without granting it Task or authority state."""

    @classmethod
    def validate(
        cls,
        parent: Task,
        proposal: RootPlanningProposal,
        *,
        existing_tasks: tuple[Task, ...] = (),
        max_children_per_plan: int | None = None,
        max_planning_cycles: int = 1,
        max_total_tasks_per_root: int = 128,
    ) -> tuple[ChildTaskProposal, ...]:
        if not isinstance(parent, Task):
            raise TypeError("parent must be a Task")
        if not isinstance(proposal, RootPlanningProposal):
            raise TypeError("proposal must be a RootPlanningProposal")
        if parent.task_id != proposal.parent_task_id:
            raise PlanningValidationError("proposal parent does not match Task")
        if parent.parent_task_id is not None or parent.root_task_id != parent.task_id:
            raise PlanningValidationError("planning proposal requires a root Task")
        if parent.task_type not in {TaskType.REASONING, TaskType.DELEGATED_AGENT}:
            raise PlanningValidationError("planning proposal requires a reasoning root Task")
        if proposal.planning_cycle > max_planning_cycles:
            raise PlanningValidationError("planning cycle limit exceeded")
        if not proposal.children:
            raise PlanningValidationError("planning proposal must contain at least one child")
        if isinstance(max_children_per_plan, bool) or (max_children_per_plan is not None and max_children_per_plan <= 0):
            raise PlanningValidationError("max_children_per_plan must be positive")
        if isinstance(max_total_tasks_per_root, bool) or max_total_tasks_per_root <= 0:
            raise PlanningValidationError("max_total_tasks_per_root must be positive")
        child_limit = min(parent.limits.max_child_tasks, max_children_per_plan or parent.limits.max_child_tasks)
        if len(proposal.children) > child_limit:
            raise PlanningValidationError("planning child limit exceeded")
        existing_root_tasks = sum(
            1 for task in existing_tasks if (task.root_task_id or task.task_id) == parent.task_id
        )
        if existing_root_tasks + len(proposal.children) > max_total_tasks_per_root:
            raise PlanningValidationError("planning total task limit exceeded")

        keys = [child.child_key for child in proposal.children]
        if len(set(keys)) != len(keys):
            raise PlanningValidationError("planning child keys must be unique")
        key_set = set(keys)
        dependencies: dict[str, tuple[str, ...]] = {}
        for child in proposal.children:
            if any(dependency == child.child_key for dependency in child.dependencies):
                raise PlanningValidationError("planning dependency cannot reference itself")
            unknown = [dependency for dependency in child.dependencies if dependency not in key_set]
            if unknown:
                raise PlanningValidationError(f"unknown planning dependency: {unknown[0]}")
            dependencies[child.child_key] = child.dependencies
            try:
                classify_task_capabilities(child.required_capabilities)
            except ValueError as exc:
                raise PlanningValidationError(str(exc)) from exc
            child_sensitivity = child.sensitivity or parent.sensitivity
            if _SENSITIVITY_RANK[child_sensitivity] < _SENSITIVITY_RANK[parent.sensitivity]:
                raise PlanningValidationError("child sensitivity cannot be lower than parent sensitivity")
            if child.task_type is TaskType.PROTECTED and child.suggested_owner != "codex":
                raise PlanningValidationError("protected child cannot be assigned to worker")

        cls._reject_dependency_cycles(dependencies)
        return proposal.children

    @staticmethod
    def _reject_dependency_cycles(dependencies: Mapping[str, tuple[str, ...]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise PlanningValidationError("planning dependency cycle detected")
            if key in visited:
                return
            visiting.add(key)
            for dependency in dependencies.get(key, ()):
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in dependencies:
            visit(key)


def _string_tuple(values: Any, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PlanningValidationError(f"{name} must be a collection of strings")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise PlanningValidationError(f"{name} must be a collection of strings") from exc
    normalized: list[str] = []
    for value in items:
        if not isinstance(value, str) or not value.strip():
            raise PlanningValidationError(f"{name} must contain non-empty strings")
        text = value.strip()
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)


__all__ = [
    "ChildTaskProposal",
    "PlanningValidationError",
    "RootPlanningProposal",
    "RootPlanningValidator",
]
