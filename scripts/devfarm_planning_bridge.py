"""Host-side conversion from a validated planner proposal to a DevFarm plan.

The bridge is deliberately a candidate builder.  It does not write
``.devfarm`` and it does not dispatch a provider.  A caller must explicitly
write the returned manifest candidates through the existing ``write_manifest``
boundary and then pass the returned plan to ``create_plan``.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from scripts.devfarm_contracts import validate_manifest
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_commander import validate_plan
from src.dev_agent.coordination import WorkAddress
from src.dev_agent.domain.protocol import RiskLevel, Task, TaskType
from src.dev_agent.intelligence.planner import (
    PlannerDependencyType,
    PlanningValidationError,
    RootPlanningProposal,
    RootPlanningValidator,
)


class PlanningBridgeError(DevFarmError):
    """A proposal cannot be represented safely as a development plan."""


@dataclass(frozen=True)
class DevelopmentPlanCandidate:
    """A validated plan plus immutable manifest candidates for Host commit."""

    plan: dict[str, Any]
    manifests: tuple[tuple[str, dict[str, Any]], ...]
    corrections: tuple[str, ...] = ()
    proposal_id: str = ""


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_SUPPORTED_DEVELOPMENT_DEPENDENCY = PlannerDependencyType.CODE_INTEGRATED
_CODEX_FORCED_TYPES = frozenset({TaskType.PROTECTED, TaskType.EXPERT, TaskType.REASONING})
_CODEX_FORCED_RISKS = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})


class DevelopmentPlanningBridge:
    """Convert a Host-validated proposal without granting model authority."""

    def __init__(self, root: str | Any) -> None:
        # Keep the root as an explicit composition input even though the
        # candidate builder currently has no filesystem side effect.  This
        # prevents a future caller from accidentally making the bridge a
        # process-global plan writer.
        self.root = root

    def build_candidate(
        self,
        parent: Task,
        proposal: RootPlanningProposal,
        *,
        run_id: str,
        base_revision: str,
        task_specs: Mapping[str, Mapping[str, Any]],
        existing_tasks: tuple[Task, ...] = (),
        objective: str | None = None,
        parent_work_address: WorkAddress | str | None = None,
    ) -> DevelopmentPlanCandidate:
        """Build a Commander plan candidate from a Host-validated proposal.

        ``task_specs`` is Host-owned execution detail: paths, tests, and the
        selected Worker assignment are never invented from model text.  The
        proposal supplies the bounded task objective/type/dependency shape;
        existing validators remain the final authority.
        """

        if not isinstance(parent, Task):
            raise TypeError("parent must be a Task")
        if not isinstance(proposal, RootPlanningProposal):
            raise TypeError("proposal must be a RootPlanningProposal")
        run_id = self._safe_run_id(run_id)
        base_revision = self._safe_revision(base_revision)
        plan_objective = self._text(objective if objective is not None else parent.objective, "objective", 4000)
        parent_address = self._safe_work_address(parent_work_address)
        if not isinstance(task_specs, Mapping):
            raise PlanningBridgeError("task_specs must be an object")
        proposal_keys = {child.child_key for child in proposal.children}
        spec_keys = set(task_specs)
        if spec_keys != proposal_keys:
            missing = sorted(proposal_keys - spec_keys)
            extra = sorted(spec_keys - proposal_keys)
            detail = f"missing task spec: {missing[0]}" if missing else f"unknown task spec: {extra[0]}"
            raise PlanningBridgeError(detail)
        try:
            RootPlanningValidator.validate(parent, proposal, existing_tasks=existing_tasks)
        except PlanningValidationError as exc:
            raise PlanningBridgeError(str(exc)) from exc

        task_ids = {child.child_key: self._task_id(run_id, proposal.proposal_id, child.child_key) for child in proposal.children}
        corrections: list[str] = []
        manifests: list[tuple[str, dict[str, Any]]] = []
        tasks: list[dict[str, Any]] = []
        for child in proposal.children:
            raw_spec = task_specs[child.child_key]
            if not isinstance(raw_spec, Mapping):
                raise PlanningBridgeError(f"task spec must be an object: {child.child_key}")
            self._require_supported_dependencies(child)
            owner, reason = self._owner(child, raw_spec)
            if reason is not None:
                corrections.append(f"{child.child_key}: owner corrected to codex ({reason})")
            task_id = task_ids[child.child_key]
            dependencies = [task_ids[item] for item in child.dependencies]
            task: dict[str, Any] = {
                "task_id": task_id,
                "owner": owner,
                "task_type": child.task_type.value,
                "risk": child.risk.value,
                "sensitivity": child.sensitivity or parent.sensitivity,
                "dependencies": dependencies,
                "dependency_types": {
                    task_ids[dependency]: child.dependency_types[dependency].value
                    for dependency in child.dependencies
                },
                "ownership": self._paths(raw_spec.get("ownership", []), "ownership"),
                "max_attempts": self._max_attempts(raw_spec.get("max_attempts", 2)),
                "worker_candidate": owner == "worker",
                "delegation_reason": reason or (
                    "planner_worker_candidate" if owner == "worker" else "planner_suggested_codex"
                ),
                "assignment": dict(raw_spec.get("assignment", {})) if isinstance(raw_spec.get("assignment", {}), Mapping) else {},
            }
            if raw_spec.get("work_address") is not None:
                task["work_address"] = self._safe_work_address(raw_spec["work_address"])
            elif parent_address is not None:
                # The Commander normalizer allocates a collision-free child
                # under this external parent.  The parent Task itself need
                # not be duplicated into the development plan.
                task["work_address_parent"] = parent_address
            if raw_spec.get("work_address_kind") is not None:
                task["work_address_kind"] = self._work_address_kind(raw_spec["work_address_kind"])
            if owner == "worker":
                raw_manifest = raw_spec.get("manifest")
                if not isinstance(raw_manifest, Mapping):
                    raise PlanningBridgeError(f"worker task requires a manifest spec: {child.child_key}")
                manifest = dict(raw_manifest)
                # Proposal and Host inputs are authoritative for these three
                # fields; a model cannot move the task to another revision or
                # silently change the objective being verified.
                manifest["task_id"] = task_id
                manifest["task_type"] = child.task_type.value
                manifest["objective"] = child.objective
                manifest["base_revision"] = base_revision
                try:
                    normalized_manifest = validate_manifest(manifest)
                except DevFarmError as exc:
                    raise PlanningBridgeError(f"invalid manifest for {child.child_key}: {exc}") from exc
                manifest_path = f".devfarm/tasks/{task_id}.json"
                task["manifest_path"] = manifest_path
                task["ownership"] = list(normalized_manifest["allowed_files"])
                assignment = task["assignment"]
                if not isinstance(assignment, dict) or not assignment.get("provider_id") or not assignment.get("model_id"):
                    raise PlanningBridgeError(f"worker task requires provider assignment: {child.child_key}")
                manifests.append((manifest_path, normalized_manifest))
            elif raw_spec.get("manifest") is not None:
                raise PlanningBridgeError(f"codex task cannot carry a Worker manifest: {child.child_key}")
            tasks.append(task)

        raw_plan = {
            "run_id": run_id,
            "objective": plan_objective,
            "base_revision": base_revision,
            "tasks": tasks,
        }
        try:
            normalized_plan = validate_plan(raw_plan)
        except DevFarmError as exc:
            raise PlanningBridgeError(f"invalid Commander plan candidate: {exc}") from exc
        return DevelopmentPlanCandidate(
            plan=normalized_plan,
            manifests=tuple(manifests),
            corrections=tuple(corrections),
            proposal_id=proposal.proposal_id,
        )

    @staticmethod
    def _owner(child: Any, spec: Mapping[str, Any]) -> tuple[str, str | None]:
        requested = spec.get("owner", child.suggested_owner)
        if not isinstance(requested, str) or requested.strip().lower() not in {"worker", "codex"}:
            raise PlanningBridgeError(f"owner must be worker or codex: {child.child_key}")
        requested = requested.strip().lower()
        if child.task_type in _CODEX_FORCED_TYPES or child.risk in _CODEX_FORCED_RISKS:
            if requested != "codex":
                reason = "protected" if child.task_type is TaskType.PROTECTED else "high_risk"
                return "codex", reason
            return "codex", "protected" if child.task_type is TaskType.PROTECTED else None
        if requested == "codex" and child.suggested_owner == "worker":
            reason = spec.get("codex_direct_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise PlanningBridgeError(f"codex_direct_reason is required: {child.child_key}")
            return "codex", reason.strip()[:1000]
        return requested, None

    @staticmethod
    def _require_supported_dependencies(child: Any) -> None:
        for dependency in child.dependencies:
            dependency_type = child.dependency_types.get(dependency, PlannerDependencyType.TASK_COMPLETED)
            if dependency_type is not _SUPPORTED_DEVELOPMENT_DEPENDENCY:
                raise PlanningBridgeError(
                    f"development Commander currently requires CODE_INTEGRATED dependency semantics: "
                    f"{child.child_key} -> {dependency} ({dependency_type.value})"
                )

    @staticmethod
    def _task_id(run_id: str, proposal_id: str, child_key: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"dev-agent:development-plan:{run_id}:{proposal_id}:{child_key}"))

    @staticmethod
    def _safe_run_id(value: Any) -> str:
        if not isinstance(value, str) or not _RUN_ID.fullmatch(value.strip()):
            raise PlanningBridgeError("run_id contains unsafe characters")
        return value.strip()

    @staticmethod
    def _safe_revision(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or value.strip().startswith("-") or any(char.isspace() for char in value):
            raise PlanningBridgeError("base_revision must be a safe Git revision")
        return value.strip()

    @staticmethod
    def _safe_work_address(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return str(value if isinstance(value, WorkAddress) else WorkAddress.parse(value))
        except (TypeError, ValueError) as exc:
            raise PlanningBridgeError("work address is invalid") from exc

    @staticmethod
    def _work_address_kind(value: Any) -> str:
        if not isinstance(value, str) or value.strip().lower() not in {"numeric", "letter"}:
            raise PlanningBridgeError("work_address_kind must be numeric or letter")
        return value.strip().lower()

    @staticmethod
    def _text(value: Any, name: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise PlanningBridgeError(f"{name} must be a non-empty string of at most {maximum} characters")
        return value.strip()

    @staticmethod
    def _paths(value: Any, name: str) -> list[str]:
        if not isinstance(value, list):
            raise PlanningBridgeError(f"{name} must be a list")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise PlanningBridgeError(f"{name} must contain non-empty paths")
            normalized = item.strip().replace("\\", "/")
            if normalized not in result:
                result.append(normalized)
        return result

    @staticmethod
    def _max_attempts(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PlanningBridgeError("max_attempts must be a positive integer")
        return value


__all__ = ["DevelopmentPlanCandidate", "DevelopmentPlanningBridge", "PlanningBridgeError"]
