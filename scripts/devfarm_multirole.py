"""Thin Phase 8 role projection over the existing Commander plan boundary.

This module validates a role/instance projection for an already validated
development plan.  It does not create a scheduler, choose a Provider, persist
another task database, or grant integration authority.  The existing
DevelopmentPlanCandidate remains the persistence handoff to Commander.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_planning_bridge import DevelopmentPlanCandidate
from scripts.devfarm_plan_validation import validate_plan
from src.dev_agent.domain.protocol import Task
from src.dev_agent.intelligence.policy import TaskIntelligencePolicy
from src.dev_agent.intelligence.role_manifest import (
    RoleAssignmentError,
    RoleInstance,
    RoleManifest,
    RoleTaskAssignment,
    RoleTaskProfile,
    validate_assignment_set,
)


class MultiRolePlanError(DevFarmError):
    """A role projection cannot be attached to a Commander candidate."""


@dataclass(frozen=True)
class MultiRolePlanCandidate:
    """A Commander candidate plus a bounded, non-authoritative role view."""

    plan: dict[str, Any]
    manifests: tuple[tuple[str, dict[str, Any]], ...]
    role_manifests: dict[str, dict[str, Any]]
    role_instances: dict[str, dict[str, Any]]
    role_assignments: tuple[RoleTaskAssignment, ...]
    corrections: tuple[str, ...] = ()
    proposal_id: str = ""

    def to_development_candidate(self) -> DevelopmentPlanCandidate:
        """Return the existing bridge candidate for Host persistence."""

        return DevelopmentPlanCandidate(
            plan=deepcopy(self.plan),
            manifests=self.manifests,
            corrections=self.corrections,
            proposal_id=self.proposal_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": deepcopy(self.plan),
            "manifests": deepcopy(list(self.manifests)),
            "role_manifests": deepcopy(self.role_manifests),
            "role_instances": deepcopy(self.role_instances),
            "role_assignments": [item.to_dict() for item in self.role_assignments],
            "corrections": list(self.corrections),
            "proposal_id": self.proposal_id,
        }


class MultiRolePlanAdapter:
    """Attach Role Manifest/Instance metadata without replacing Commander."""

    REQUIRED_ROLE_IDS = frozenset({"planner", "implementer", "reviewer"})

    def __init__(self, root: str | Any) -> None:
        # The root is an explicit composition dependency.  Candidate
        # construction deliberately performs no filesystem or network I/O.
        self.root = root

    def build_candidate(
        self,
        candidate: DevelopmentPlanCandidate,
        *,
        role_manifests: Mapping[str, RoleManifest],
        role_instances: Mapping[str, RoleInstance],
        task_profiles: Mapping[str, Task | RoleTaskProfile],
        assignments: Sequence[RoleTaskAssignment],
        peers: Sequence[Any] = (),
        now: str | None = None,
    ) -> MultiRolePlanCandidate:
        """Validate and project role assignments onto a Commander candidate."""

        if not isinstance(candidate, DevelopmentPlanCandidate):
            raise TypeError("candidate must be DevelopmentPlanCandidate")
        manifests = self._manifests(role_manifests)
        instances = self._instances(role_instances, manifests)
        profiles = self._profiles(task_profiles)
        plan = validate_plan(deepcopy(candidate.plan))
        tasks = plan["tasks"]
        task_by_id = {task["task_id"]: task for task in tasks}
        if len(assignments) != len(tasks) or {item.task_id for item in assignments} != set(task_by_id):
            raise RoleAssignmentError("each plan task requires exactly one role assignment")
        if set(profiles) != set(task_by_id):
            raise RoleAssignmentError("task_profiles must contain exactly one profile per plan task")

        normalized_assignments = validate_assignment_set(assignments, peers=peers, now=now)
        peer_by_identity = {(peer.instance_id, peer.generation): peer for peer in peers}
        counts: dict[str, int] = {}
        policy = TaskIntelligencePolicy()
        for assignment in normalized_assignments:
            task = task_by_id[assignment.task_id]
            manifest = manifests.get(assignment.role_id)
            if manifest is None:
                raise RoleAssignmentError(f"assignment references unknown role: {assignment.role_id}")
            instance = instances.get(assignment.instance_id)
            if instance is None or instance.generation != assignment.generation:
                raise RoleAssignmentError("assignment role instance is not registered")
            if instance.role_id != assignment.role_id:
                raise RoleAssignmentError("assignment role does not match role instance")
            if peers:
                peer = peer_by_identity.get((assignment.instance_id, assignment.generation))
                if peer is None or not instance.is_current(peer, now=now):
                    raise RoleAssignmentError("assignment peer lease is not live")

            profile = profiles[assignment.task_id]
            if profile.task_id != assignment.task_id:
                raise RoleAssignmentError("task profile identity does not match assignment")
            if profile.task_type.value != task["task_type"]:
                raise RoleAssignmentError("task profile type does not match plan task")
            if profile.risk.value != task["risk"] or profile.sensitivity != task["sensitivity"]:
                raise RoleAssignmentError("task profile risk/privacy does not match plan task")
            try:
                if isinstance(profile, Task):
                    manifest.validate_task(profile, policy.decide(profile))
                elif isinstance(profile, RoleTaskProfile):
                    manifest.validate_profile(profile)
                else:
                    raise TypeError("task profile must be Task or RoleTaskProfile")
            except (TypeError, ValueError) as exc:
                raise RoleAssignmentError(f"role task admission failed: {assignment.task_id}") from exc
            if assignment.owned_paths != tuple(task["ownership"]):
                raise RoleAssignmentError("role assignment paths do not match plan ownership")
            if assignment.role_id == "implementer" and task["owner"] != "worker":
                raise RoleAssignmentError("implementer role must use a Worker-owned task")
            counts[assignment.instance_id] = counts.get(assignment.instance_id, 0) + 1
            if counts[assignment.instance_id] > manifest.max_concurrency:
                raise RoleAssignmentError("role instance exceeds manifest concurrency")
            task["role_id"] = assignment.role_id
            task["role_instance_id"] = assignment.instance_id
            task["role_generation"] = assignment.generation

        projected = validate_plan(plan)
        return MultiRolePlanCandidate(
            plan=projected,
            manifests=tuple((path, deepcopy(value)) for path, value in candidate.manifests),
            role_manifests={key: value.to_dict() for key, value in manifests.items()},
            role_instances={key: value.to_dict() for key, value in instances.items()},
            role_assignments=normalized_assignments,
            corrections=candidate.corrections,
            proposal_id=candidate.proposal_id,
        )

    @classmethod
    def _manifests(cls, value: Mapping[str, RoleManifest]) -> dict[str, RoleManifest]:
        if not isinstance(value, Mapping):
            raise RoleAssignmentError("role_manifests must be an object")
        result: dict[str, RoleManifest] = {}
        for key, manifest in value.items():
            if not isinstance(manifest, RoleManifest) or key != manifest.role_id:
                raise RoleAssignmentError("role manifest mapping key does not match role_id")
            result[key] = manifest
        missing = cls.REQUIRED_ROLE_IDS - set(result)
        if missing:
            raise RoleAssignmentError(f"missing required role manifest: {sorted(missing)[0]}")
        return result

    @staticmethod
    def _instances(
        value: Mapping[str, RoleInstance],
        manifests: Mapping[str, RoleManifest],
    ) -> dict[str, RoleInstance]:
        if not isinstance(value, Mapping):
            raise RoleAssignmentError("role_instances must be an object")
        result: dict[str, RoleInstance] = {}
        for key, instance in value.items():
            if not isinstance(instance, RoleInstance) or key != instance.instance_id:
                raise RoleAssignmentError("role instance mapping key does not match instance_id")
            if instance.role_id not in manifests:
                raise RoleAssignmentError(f"role instance references unknown role: {instance.role_id}")
            result[key] = instance
        return result

    @staticmethod
    def _profiles(value: Mapping[str, Task | RoleTaskProfile]) -> dict[str, Task | RoleTaskProfile]:
        if not isinstance(value, Mapping):
            raise RoleAssignmentError("task_profiles must be an object")
        result: dict[str, Task | RoleTaskProfile] = {}
        for key, profile in value.items():
            if not isinstance(profile, (Task, RoleTaskProfile)) or key != profile.task_id:
                raise RoleAssignmentError("task profile mapping key does not match task_id")
            result[key] = profile
        return result


__all__ = ["MultiRolePlanCandidate", "MultiRolePlanAdapter", "MultiRolePlanError"]
