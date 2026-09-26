"""Host-validated, finite planning proposals for root Task decomposition.

The planner types are deliberately proposals, not Tasks and not authority
records. A model or Commander may produce them, but only the host validator
may turn a validated proposal into durable child Tasks through the existing
TaskGraph and Operation boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from ..domain.protocol import RiskLevel, Task, TaskType
from .capabilities import classify_task_capabilities


_SENSITIVITY_RANK = {"public": 0, "normal": 1, "internal": 2, "sensitive": 3}


class PlannerDependencyType(str, Enum):
    """The durable evidence required before a child may be released."""

    ARTIFACT_READY = "ARTIFACT_READY"
    TASK_COMPLETED = "TASK_COMPLETED"
    CODE_INTEGRATED = "CODE_INTEGRATED"


class PlanningValidationError(ValueError):
    """A planning proposal is not safe to convert into child Tasks."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        location: str | None = None,
        observed: str | None = None,
        expected: str | None = None,
    ) -> None:
        super().__init__(message)
        # These bounded facts let the adapter construct a useful Host-owned
        # FailureSpec without parsing arbitrary model text.  The legacy
        # message remains available as a compatibility fallback only.
        self.error_code = error_code
        self.location = location
        self.observed = observed
        self.expected = expected


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
    dependency_types: Mapping[str, PlannerDependencyType | str] = field(default_factory=dict)
    suggested_owner: str = "worker"

    def __post_init__(self) -> None:
        if not isinstance(self.child_key, str) or not self.child_key.strip():
            raise PlanningValidationError(
                "child_key must be a non-empty string",
                error_code="empty_child_key",
                location="children[].child_key",
                observed="empty or non-string child_key",
                expected="a non-empty string",
            )
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise PlanningValidationError(
                "child objective must be a non-empty string",
                error_code="empty_child_objective",
                location="children[].objective",
                observed="empty or non-string objective",
                expected="a non-empty string",
            )
        try:
            task_type = self.task_type if isinstance(self.task_type, TaskType) else TaskType(self.task_type)
        except (TypeError, ValueError) as exc:
            raise PlanningValidationError(
                "invalid child task_type",
                error_code="invalid_task_type",
                location="children[].task_type",
                observed="unsupported task_type",
                expected="deterministic, worker, reasoning, expert, delegated_agent, recovery, or protected",
            ) from exc
        try:
            risk = self.risk if isinstance(self.risk, RiskLevel) else RiskLevel(self.risk)
        except (TypeError, ValueError) as exc:
            raise PlanningValidationError(
                "invalid child risk",
                error_code="invalid_risk",
                location="children[].risk",
                observed="unsupported risk",
                expected="low, normal, high, or critical",
            ) from exc
        sensitivity = self.sensitivity
        if sensitivity is not None:
            if not isinstance(sensitivity, str) or sensitivity.strip().lower() not in _SENSITIVITY_RANK:
                raise PlanningValidationError(
                    "child sensitivity must be public, normal, internal, or sensitive",
                    error_code="invalid_sensitivity",
                    location="children[].sensitivity",
                    observed="unsupported sensitivity",
                    expected="public, normal, internal, or sensitive",
                )
            sensitivity = sensitivity.strip().lower()
        owner = self.suggested_owner.strip().lower() if isinstance(self.suggested_owner, str) else ""
        if owner not in {"worker", "codex"}:
            raise PlanningValidationError(
                "suggested_owner must be worker or codex",
                error_code="invalid_suggested_owner",
                location="children[].suggested_owner",
                observed="unsupported suggested_owner",
                expected="worker or codex",
            )
        capabilities = _string_tuple(self.required_capabilities, "required_capabilities")
        dependencies = _string_tuple(self.dependencies, "dependencies")
        if not isinstance(self.dependency_types, Mapping):
            raise PlanningValidationError(
                "dependency_types must be an object",
                error_code="dependency_types_not_object",
                location="children[].dependency_types",
                observed="non-object dependency_types",
                expected="an object mapping dependency keys to dependency types",
            )
        unknown_dependency_types = set(self.dependency_types) - set(dependencies)
        if unknown_dependency_types:
            raise PlanningValidationError(
                f"dependency_types references unknown dependency: {sorted(unknown_dependency_types)[0]}",
                error_code="unknown_dependency_reference",
                location="children[].dependency_types",
                observed="dependency_types contains an unlisted dependency",
                expected="keys matching dependencies exactly",
            )
        normalized_dependency_types: dict[str, PlannerDependencyType] = {}
        for dependency in dependencies:
            value = self.dependency_types.get(dependency, PlannerDependencyType.TASK_COMPLETED)
            try:
                normalized_dependency_types[dependency] = (
                    value if isinstance(value, PlannerDependencyType) else PlannerDependencyType(value)
                )
            except (TypeError, ValueError) as exc:
                raise PlanningValidationError(
                    f"invalid dependency type for {dependency}",
                    error_code="invalid_dependency_type",
                    location=f"children[].dependency_types.{dependency}",
                    observed="unsupported dependency type",
                    expected="ARTIFACT_READY, TASK_COMPLETED, or CODE_INTEGRATED",
                ) from exc
        object.__setattr__(self, "child_key", self.child_key.strip())
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "sensitivity", sensitivity)
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "dependency_types", normalized_dependency_types)
        object.__setattr__(self, "suggested_owner", owner)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["task_type"] = self.task_type.value
        value["risk"] = self.risk.value
        value["required_capabilities"] = list(self.required_capabilities)
        value["dependencies"] = list(self.dependencies)
        value["dependency_types"] = {key: value.value for key, value in self.dependency_types.items()}
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChildTaskProposal":
        """Decode a model-produced child without granting it authority.

        The proposal schema is intentionally strict at this boundary.  A
        model may omit optional fields, but it may not smuggle an untracked
        field into the host-side Task conversion.
        """

        if not isinstance(data, Mapping):
            raise PlanningValidationError(
                "child proposal must be an object",
                error_code="child_not_object",
                location="children[]",
                observed="non-object child proposal",
                expected="one child proposal object",
            )
        allowed = {
            "child_key",
            "objective",
            "task_type",
            "risk",
            "sensitivity",
            "required_capabilities",
            "dependencies",
            "dependency_types",
            "suggested_owner",
        }
        unknown = set(data) - allowed
        if unknown:
            unknown_field = sorted(unknown)[0]
            raise PlanningValidationError(
                f"unknown child proposal field: {unknown_field}",
                error_code="unknown_child_field",
                location=f"children[].{unknown_field}",
                observed="unsupported field",
                expected="only fields from the supplied Host schema",
            )
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise PlanningValidationError(
                f"invalid child proposal: {exc}",
                error_code="invalid_child_proposal",
                location="children[]",
                observed="child proposal does not satisfy the typed contract",
                expected="a valid child proposal object",
            ) from exc


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RootPlanningProposal":
        """Decode a bounded proposal returned by a Planner adapter."""

        if not isinstance(data, Mapping):
            raise PlanningValidationError(
                "planning proposal must be an object",
                error_code="root_not_object",
                location="planning_proposal",
                observed="non-object planning response",
                expected="one planning proposal object",
            )
        allowed = {"parent_task_id", "rationale", "children", "planning_cycle", "proposal_id"}
        unknown = set(data) - allowed
        if unknown:
            unknown_field = sorted(unknown)[0]
            raise PlanningValidationError(
                f"unknown planning proposal field: {unknown_field}",
                error_code="unknown_root_field",
                location=unknown_field,
                observed="unsupported top-level field",
                expected="only fields from the supplied Host schema",
            )
        raw_children = data.get("children")
        if isinstance(raw_children, (str, bytes)) or not isinstance(raw_children, (list, tuple)):
            raise PlanningValidationError(
                "planning proposal children must be a list",
                error_code="children_not_list",
                location="children",
                observed="children is not an array",
                expected="an array of child proposal objects",
            )
        children = tuple(ChildTaskProposal.from_dict(item) for item in raw_children)
        values = dict(data)
        values["children"] = children
        try:
            return cls(**values)
        except TypeError as exc:
            raise PlanningValidationError(
                f"invalid planning proposal: {exc}",
                error_code="invalid_root_proposal",
                location="planning_proposal",
                observed="planning proposal does not satisfy the typed contract",
                expected="a valid planning proposal object",
            ) from exc


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
    "PlannerDependencyType",
    "PlanningValidationError",
    "RootPlanningProposal",
    "RootPlanningValidator",
]
