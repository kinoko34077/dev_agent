"""Bounded Phase 8 role and instance projections.

Role manifests describe what a role may propose or execute.  They do not
create authority, select a Provider/model, or persist a second task state.
Role instances are references to existing Process Coordination peers; lease
freshness remains owned by ``PeerRecord.is_live``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import posixpath
import re
from typing import Any

from ..domain.protocol import IntelligenceTier, RiskLevel, Task, TaskType


class RoleManifestError(ValueError):
    """A role manifest or task projection is outside the bounded contract."""


class RoleAssignmentError(ValueError):
    """A role assignment cannot be safely represented in one proposal."""


_ROLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_MAX_TEXT = 2_048
_MAX_ITEMS = 32
_MAX_PATHS = 64
_MAX_PATH_CHARS = 512
_TIERS = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)
_RISKS = (RiskLevel.LOW, RiskLevel.NORMAL, RiskLevel.HIGH, RiskLevel.CRITICAL)
_SENSITIVITIES = ("public", "normal", "internal", "sensitive")
_AUTONOMY_CEILINGS = frozenset({"proposal_only", "task_execution", "review_proposal"})
_REVIEW_REQUIREMENTS = frozenset({"none", "host", "codex", "human"})
_ROLE_ACTIONS = frozenset(
    {
        "decompose",
        "propose_task",
        "implement",
        "propose_tests",
        "review",
        "propose_rework",
        "integrate",
        "approve_integration",
        "consume_approval",
        "change_authority",
        "dispatch_provider",
        "modify_gate",
        "change_budget",
        "restart_process",
    }
)
_NEVER_ALLOWED_ACTIONS = frozenset(
    {
        "integrate",
        "approve_integration",
        "consume_approval",
        "change_authority",
        "dispatch_provider",
        "modify_gate",
        "change_budget",
        "restart_process",
    }
)
_DEFAULT_FORBIDDEN_ACTIONS = (
    "integrate",
    "approve_integration",
    "consume_approval",
    "change_authority",
    "dispatch_provider",
    "modify_gate",
    "change_budget",
    "restart_process",
)


def _text(value: Any, name: str, *, max_chars: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoleManifestError(f"{name} must be non-empty text")
    result = value.strip()
    if len(result) > max_chars:
        raise RoleManifestError(f"{name} exceeds its bound")
    return result


def _identifier(value: Any, name: str) -> str:
    result = _text(value, name, max_chars=128)
    if _IDENTIFIER.fullmatch(result) is None:
        raise RoleManifestError(f"{name} must be a bounded identifier")
    return result


def _role_identifier(value: Any, name: str) -> str:
    result = _text(value, name, max_chars=64)
    if _ROLE_ID.fullmatch(result) is None:
        raise RoleManifestError(f"{name} must be a bounded lowercase role identifier")
    return result


def _items(value: Any, name: str, *, max_items: int = _MAX_ITEMS, max_chars: int = 512) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RoleManifestError(f"{name} must be a sequence")
    if len(value) > max_items:
        raise RoleManifestError(f"{name} exceeds its item bound")
    result: list[str] = []
    for item in value:
        normalized = _text(item, f"{name}[]", max_chars=max_chars)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _enum(value: Any, enum_type: type[Enum], name: str) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise RoleManifestError(f"{name} must be one of: {allowed}") from exc


def _positive_int(value: Any, name: str, *, maximum: int = 64) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise RoleManifestError(f"{name} must be between 1 and {maximum}")
    return value


def _sensitivity(value: Any, name: str = "sensitivity") -> str:
    result = _text(value, name, max_chars=32).lower()
    if result not in _SENSITIVITIES:
        raise RoleManifestError(f"{name} must be one of: {', '.join(_SENSITIVITIES)}")
    return result


def _tier_at_least(left: IntelligenceTier, right: IntelligenceTier) -> bool:
    return _TIERS.index(left) >= _TIERS.index(right)


def _risk_at_least(left: RiskLevel, right: RiskLevel) -> bool:
    return _RISKS.index(left) >= _RISKS.index(right)


def _sensitivity_at_least(left: str, right: str) -> bool:
    return _SENSITIVITIES.index(left) >= _SENSITIVITIES.index(right)


def _peer_value(peer: Any, name: str) -> Any:
    try:
        return getattr(peer, name)
    except AttributeError as exc:
        raise RoleAssignmentError(f"peer is missing {name}") from exc


@dataclass(frozen=True)
class RoleTaskProfile:
    """Host-owned task shape for Commander IDs that are not Kernel UUIDs."""

    task_id: str
    task_type: TaskType
    required_capabilities: tuple[str, ...]
    intelligence_tier: IntelligenceTier
    risk: RiskLevel
    sensitivity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id", max_chars=128))
        object.__setattr__(self, "task_type", _enum(self.task_type, TaskType, "task_type"))
        object.__setattr__(
            self,
            "required_capabilities",
            _items(self.required_capabilities, "required_capabilities", max_chars=128),
        )
        object.__setattr__(
            self,
            "intelligence_tier",
            _enum(self.intelligence_tier, IntelligenceTier, "intelligence_tier"),
        )
        object.__setattr__(self, "risk", _enum(self.risk, RiskLevel, "risk"))
        object.__setattr__(self, "sensitivity", _sensitivity(self.sensitivity))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "required_capabilities": list(self.required_capabilities),
            "intelligence_tier": self.intelligence_tier.value,
            "risk": self.risk.value,
            "sensitivity": self.sensitivity,
        }


@dataclass(frozen=True)
class RoleManifest:
    """Data-only role definition with bounded policy references."""

    role_id: str
    responsibility: str
    allowed_task_types: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    minimum_intelligence_tier: IntelligenceTier
    maximum_intelligence_tier: IntelligenceTier
    autonomy_ceiling: str
    tool_policy_ref: str
    provider_policy_ref: str
    privacy_ceiling: str
    risk_ceiling: RiskLevel
    budget_ref: str
    max_concurrency: int
    allowed_input_artifact_types: tuple[str, ...]
    allowed_output_artifact_types: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    review_requirement: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _role_identifier(self.role_id, "role_id"))
        object.__setattr__(self, "responsibility", _text(self.responsibility, "responsibility"))
        task_types = _items(self.allowed_task_types, "allowed_task_types", max_chars=64)
        if not task_types:
            raise RoleManifestError("allowed_task_types must not be empty")
        allowed_task_values = {item.value for item in TaskType}
        if any(item not in allowed_task_values for item in task_types):
            raise RoleManifestError("allowed_task_types contains an unknown TaskType")
        object.__setattr__(self, "allowed_task_types", task_types)
        object.__setattr__(
            self,
            "required_capabilities",
            _items(self.required_capabilities, "required_capabilities", max_chars=128),
        )
        minimum = _enum(self.minimum_intelligence_tier, IntelligenceTier, "minimum_intelligence_tier")
        maximum = _enum(self.maximum_intelligence_tier, IntelligenceTier, "maximum_intelligence_tier")
        if _TIERS.index(minimum) > _TIERS.index(maximum):
            raise RoleManifestError("minimum_intelligence_tier cannot exceed maximum_intelligence_tier")
        object.__setattr__(self, "minimum_intelligence_tier", minimum)
        object.__setattr__(self, "maximum_intelligence_tier", maximum)
        autonomy = _text(self.autonomy_ceiling, "autonomy_ceiling", max_chars=64).lower()
        if autonomy not in _AUTONOMY_CEILINGS:
            raise RoleManifestError("autonomy_ceiling is outside the bounded role vocabulary")
        object.__setattr__(self, "autonomy_ceiling", autonomy)
        object.__setattr__(self, "tool_policy_ref", _identifier(self.tool_policy_ref, "tool_policy_ref"))
        object.__setattr__(self, "provider_policy_ref", _identifier(self.provider_policy_ref, "provider_policy_ref"))
        object.__setattr__(self, "privacy_ceiling", _sensitivity(self.privacy_ceiling, "privacy_ceiling"))
        object.__setattr__(self, "risk_ceiling", _enum(self.risk_ceiling, RiskLevel, "risk_ceiling"))
        object.__setattr__(self, "budget_ref", _identifier(self.budget_ref, "budget_ref"))
        object.__setattr__(self, "max_concurrency", _positive_int(self.max_concurrency, "max_concurrency"))
        object.__setattr__(
            self,
            "allowed_input_artifact_types",
            _items(self.allowed_input_artifact_types, "allowed_input_artifact_types"),
        )
        object.__setattr__(
            self,
            "allowed_output_artifact_types",
            _items(self.allowed_output_artifact_types, "allowed_output_artifact_types"),
        )
        allowed_actions = _items(self.allowed_actions, "allowed_actions", max_chars=64)
        forbidden_actions = _items(self.forbidden_actions, "forbidden_actions", max_chars=64)
        unknown_actions = (set(allowed_actions) | set(forbidden_actions)) - _ROLE_ACTIONS
        if unknown_actions:
            raise RoleManifestError(f"unknown action: {sorted(unknown_actions)[0]}")
        prohibited = set(allowed_actions) & _NEVER_ALLOWED_ACTIONS
        if prohibited:
            raise RoleManifestError(f"unknown action or prohibited authority action: {sorted(prohibited)[0]}")
        if set(allowed_actions) & set(forbidden_actions):
            raise RoleManifestError("allowed_actions and forbidden_actions must not overlap")
        object.__setattr__(self, "allowed_actions", allowed_actions)
        object.__setattr__(self, "forbidden_actions", forbidden_actions)
        review = _text(self.review_requirement, "review_requirement", max_chars=32).lower()
        if review not in _REVIEW_REQUIREMENTS:
            raise RoleManifestError("review_requirement is outside the bounded role vocabulary")
        object.__setattr__(self, "review_requirement", review)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "responsibility": self.responsibility,
            "allowed_task_types": list(self.allowed_task_types),
            "required_capabilities": list(self.required_capabilities),
            "minimum_intelligence_tier": self.minimum_intelligence_tier.value,
            "maximum_intelligence_tier": self.maximum_intelligence_tier.value,
            "autonomy_ceiling": self.autonomy_ceiling,
            "tool_policy_ref": self.tool_policy_ref,
            "provider_policy_ref": self.provider_policy_ref,
            "privacy_ceiling": self.privacy_ceiling,
            "risk_ceiling": self.risk_ceiling.value,
            "budget_ref": self.budget_ref,
            "max_concurrency": self.max_concurrency,
            "allowed_input_artifact_types": list(self.allowed_input_artifact_types),
            "allowed_output_artifact_types": list(self.allowed_output_artifact_types),
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "review_requirement": self.review_requirement,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RoleManifest":
        if not isinstance(value, Mapping):
            raise RoleManifestError("role manifest must be an object")
        expected = {
            "role_id",
            "responsibility",
            "allowed_task_types",
            "required_capabilities",
            "minimum_intelligence_tier",
            "maximum_intelligence_tier",
            "autonomy_ceiling",
            "tool_policy_ref",
            "provider_policy_ref",
            "privacy_ceiling",
            "risk_ceiling",
            "budget_ref",
            "max_concurrency",
            "allowed_input_artifact_types",
            "allowed_output_artifact_types",
            "allowed_actions",
            "forbidden_actions",
            "review_requirement",
        }
        unknown = set(value) - expected
        if unknown:
            raise RoleManifestError(f"unknown role manifest field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise RoleManifestError(f"invalid role manifest: {exc}") from exc

    def validate_task(self, task: Task, intelligence_decision: Any) -> "RoleTaskAdmission":
        """Validate a task projection without choosing a concrete resource."""

        if not isinstance(task, Task):
            raise TypeError("task must be Task")
        if not hasattr(intelligence_decision, "minimum_tier"):
            raise TypeError("intelligence_decision must expose minimum_tier")
        task_type = task.task_type.value
        if task_type not in self.allowed_task_types:
            raise RoleManifestError("task type is not allowed by role manifest")
        task_capabilities = tuple(task.required_capabilities)
        allowed_capabilities = set(self.required_capabilities)
        if any(capability not in allowed_capabilities for capability in task_capabilities):
            raise RoleManifestError("task capability is outside role manifest capability set")
        tier = _enum(intelligence_decision.minimum_tier, IntelligenceTier, "intelligence_decision.minimum_tier")
        if not _tier_at_least(tier, self.minimum_intelligence_tier) or _TIERS.index(tier) > _TIERS.index(self.maximum_intelligence_tier):
            raise RoleManifestError("task intelligence tier is outside role manifest bounds")
        profile = RoleTaskProfile(
            task_id=task.task_id,
            task_type=task.task_type,
            required_capabilities=task_capabilities,
            intelligence_tier=tier,
            risk=task.risk,
            sensitivity=task.sensitivity,
        )
        return self.validate_profile(profile)

    def validate_profile(self, profile: RoleTaskProfile) -> "RoleTaskAdmission":
        """Validate a Host-projected Commander task profile."""

        if not isinstance(profile, RoleTaskProfile):
            raise TypeError("profile must be RoleTaskProfile")
        task_type = profile.task_type.value
        if task_type not in self.allowed_task_types:
            raise RoleManifestError("task type is not allowed by role manifest")
        if any(capability not in set(self.required_capabilities) for capability in profile.required_capabilities):
            raise RoleManifestError("task capability is outside role manifest capability set")
        tier = profile.intelligence_tier
        if not _tier_at_least(tier, self.minimum_intelligence_tier) or _TIERS.index(tier) > _TIERS.index(self.maximum_intelligence_tier):
            raise RoleManifestError("task intelligence tier is outside role manifest bounds")
        risk = profile.risk
        if _risk_at_least(risk, self.risk_ceiling) and risk is not self.risk_ceiling:
            raise RoleManifestError("task risk exceeds role manifest ceiling")
        sensitivity = profile.sensitivity
        if _sensitivity_at_least(sensitivity, self.privacy_ceiling) and sensitivity != self.privacy_ceiling:
            raise RoleManifestError("task sensitivity exceeds role privacy ceiling")
        return RoleTaskAdmission(
            role_id=self.role_id,
            task_id=profile.task_id,
            task_type=task_type,
            required_capabilities=profile.required_capabilities,
            intelligence_tier=tier,
            risk=risk,
            sensitivity=sensitivity,
            review_requirement=self.review_requirement,
        )


@dataclass(frozen=True)
class RoleTaskAdmission:
    """A role/task compatibility projection with no Provider identity."""

    role_id: str
    task_id: str
    task_type: str
    required_capabilities: tuple[str, ...]
    intelligence_tier: IntelligenceTier
    risk: RiskLevel
    sensitivity: str
    review_requirement: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _role_identifier(self.role_id, "role_id"))
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id", max_chars=128))
        task_type = _text(self.task_type, "task_type", max_chars=64)
        if task_type not in {item.value for item in TaskType}:
            raise RoleManifestError("task_type is unknown")
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "required_capabilities", _items(self.required_capabilities, "required_capabilities", max_chars=128))
        object.__setattr__(self, "intelligence_tier", _enum(self.intelligence_tier, IntelligenceTier, "intelligence_tier"))
        object.__setattr__(self, "risk", _enum(self.risk, RiskLevel, "risk"))
        object.__setattr__(self, "sensitivity", _sensitivity(self.sensitivity))
        review = _text(self.review_requirement, "review_requirement", max_chars=32).lower()
        if review not in _REVIEW_REQUIREMENTS:
            raise RoleManifestError("review_requirement is unknown")
        object.__setattr__(self, "review_requirement", review)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "required_capabilities": list(self.required_capabilities),
            "intelligence_tier": self.intelligence_tier.value,
            "risk": self.risk.value,
            "sensitivity": self.sensitivity,
            "review_requirement": self.review_requirement,
            "provider_id": None,
            "provider_binding_id": None,
            "model_id": None,
        }


@dataclass(frozen=True)
class RoleInstance:
    """A role reference bound to an existing process peer identity."""

    role_id: str
    instance_id: str
    generation: int
    peer_role: str
    peer_instance_id: str
    peer_generation: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _role_identifier(self.role_id, "role_id"))
        object.__setattr__(self, "instance_id", _identifier(self.instance_id, "instance_id"))
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        object.__setattr__(self, "peer_role", _identifier(self.peer_role, "peer_role"))
        object.__setattr__(self, "peer_instance_id", _identifier(self.peer_instance_id, "peer_instance_id"))
        object.__setattr__(self, "peer_generation", _positive_int(self.peer_generation, "peer_generation"))
        if self.instance_id != self.peer_instance_id or self.generation != self.peer_generation:
            raise RoleManifestError("role instance identity must match its peer reference")

    @classmethod
    def from_peer(cls, manifest: RoleManifest, peer: Any) -> "RoleInstance":
        if not isinstance(manifest, RoleManifest):
            raise TypeError("manifest must be RoleManifest")
        return cls(
            role_id=manifest.role_id,
            instance_id=_peer_value(peer, "instance_id"),
            generation=_peer_value(peer, "generation"),
            peer_role=_peer_value(peer, "role"),
            peer_instance_id=_peer_value(peer, "instance_id"),
            peer_generation=_peer_value(peer, "generation"),
        )

    def is_current(self, peer: Any, *, now: str | None = None) -> bool:
        """Return the existing peer's strict status-plus-lease decision."""

        if (
            _peer_value(peer, "role") != self.peer_role
            or _peer_value(peer, "instance_id") != self.peer_instance_id
            or _peer_value(peer, "generation") != self.peer_generation
        ):
            raise RoleAssignmentError("peer identity does not match role instance")
        checker = getattr(peer, "is_live", None)
        if not callable(checker):
            raise RoleAssignmentError("peer does not expose strict lease predicate")
        timestamp = now or datetime.now(timezone.utc).isoformat()
        return bool(checker(timestamp))

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "peer_role": self.peer_role,
            "peer_instance_id": self.peer_instance_id,
            "peer_generation": self.peer_generation,
        }


@dataclass(frozen=True)
class RoleTaskAssignment:
    """Proposal-local role ownership projection; Commander remains global authority."""

    task_id: str
    role_id: str
    instance_id: str
    generation: int
    owned_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id", max_chars=128))
        object.__setattr__(self, "role_id", _role_identifier(self.role_id, "role_id"))
        object.__setattr__(self, "instance_id", _identifier(self.instance_id, "instance_id"))
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        paths = _items(self.owned_paths, "owned_paths", max_items=_MAX_PATHS, max_chars=_MAX_PATH_CHARS)
        normalized: list[str] = []
        for path in paths:
            candidate = path.replace("\\", "/")
            if candidate.startswith("/") or candidate == "." or candidate.startswith("../") or "/../" in candidate or candidate == "..":
                raise RoleAssignmentError("owned path escapes the repository")
            candidate = posixpath.normpath(candidate)
            if candidate == "." or candidate.startswith("../"):
                raise RoleAssignmentError("owned path escapes the repository")
            normalized.append(candidate)
        object.__setattr__(self, "owned_paths", tuple(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role_id": self.role_id,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "owned_paths": list(self.owned_paths),
        }


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def validate_assignment_set(
    assignments: Sequence[RoleTaskAssignment],
    *,
    peers: Sequence[Any] = (),
    now: str | None = None,
) -> tuple[RoleTaskAssignment, ...]:
    """Validate one proposal's assignments without replacing Commander ownership."""

    if isinstance(assignments, (str, bytes)) or not isinstance(assignments, Sequence):
        raise RoleAssignmentError("assignments must be a sequence")
    if len(assignments) > _MAX_ITEMS:
        raise RoleAssignmentError("assignments exceeds its bound")
    normalized = tuple(assignments)
    seen_tasks: set[str] = set()
    seen_paths: list[tuple[str, str]] = []
    peer_index: dict[tuple[str, int], Any] = {}
    for peer in peers:
        try:
            peer_index[(_peer_value(peer, "instance_id"), _peer_value(peer, "generation"))] = peer
        except RoleAssignmentError:
            raise
    for assignment in normalized:
        if not isinstance(assignment, RoleTaskAssignment):
            raise RoleAssignmentError("assignments must contain RoleTaskAssignment values")
        if assignment.task_id in seen_tasks:
            raise RoleAssignmentError("duplicate task assignment")
        seen_tasks.add(assignment.task_id)
        if peers:
            peer = peer_index.get((assignment.instance_id, assignment.generation))
            if peer is None:
                raise RoleAssignmentError("assignment peer identity is not present")
            checker = getattr(peer, "is_live", None)
            if not callable(checker) or not checker(now or datetime.now(timezone.utc).isoformat()):
                raise RoleAssignmentError("assignment peer lease is not live")
        for path in assignment.owned_paths:
            for prior_path, prior_task in seen_paths:
                if _paths_overlap(path, prior_path):
                    raise RoleAssignmentError(f"ownership overlap between {prior_task} and {assignment.task_id}")
            seen_paths.append((path, assignment.task_id))
    return normalized


def builtin_role_manifests() -> dict[str, RoleManifest]:
    """Return the initial three data-defined roles without authority grants."""

    common = {
        "tool_policy_ref": "existing-task-tool-policy",
        "provider_policy_ref": "existing-resource-policy",
        "budget_ref": "existing-budget-policy",
        "forbidden_actions": _DEFAULT_FORBIDDEN_ACTIONS,
    }
    return {
        "planner": RoleManifest(
            role_id="planner",
            responsibility="bounded objective decomposition",
            allowed_task_types=(TaskType.REASONING.value, TaskType.DELEGATED_AGENT.value),
            required_capabilities=("planning", "decomposition", "dependency"),
            minimum_intelligence_tier=IntelligenceTier.L2,
            maximum_intelligence_tier=IntelligenceTier.L2,
            autonomy_ceiling="proposal_only",
            privacy_ceiling="normal",
            risk_ceiling=RiskLevel.NORMAL,
            max_concurrency=1,
            allowed_input_artifact_types=("objective", "context"),
            allowed_output_artifact_types=("task_proposal", "dependency_proposal"),
            allowed_actions=("decompose", "propose_task"),
            review_requirement="host",
            **common,
        ),
        "implementer": RoleManifest(
            role_id="implementer",
            responsibility="narrow implementation proposal",
            allowed_task_types=(TaskType.WORKER.value,),
            required_capabilities=("coding", "testing"),
            minimum_intelligence_tier=IntelligenceTier.L1,
            maximum_intelligence_tier=IntelligenceTier.L1,
            autonomy_ceiling="task_execution",
            privacy_ceiling="normal",
            risk_ceiling=RiskLevel.NORMAL,
            max_concurrency=4,
            allowed_input_artifact_types=("task", "manifest"),
            allowed_output_artifact_types=("change_proposal", "test_proposal"),
            allowed_actions=("implement", "propose_tests"),
            review_requirement="host",
            **common,
        ),
        "reviewer": RoleManifest(
            role_id="reviewer",
            responsibility="evidence-grounded review proposal",
            allowed_task_types=(TaskType.WORKER.value, TaskType.REASONING.value),
            required_capabilities=("review", "evidence"),
            minimum_intelligence_tier=IntelligenceTier.L2,
            maximum_intelligence_tier=IntelligenceTier.L2,
            autonomy_ceiling="proposal_only",
            privacy_ceiling="normal",
            risk_ceiling=RiskLevel.NORMAL,
            max_concurrency=2,
            allowed_input_artifact_types=("review_packet", "verification"),
            allowed_output_artifact_types=("review_proposal", "rework_proposal"),
            allowed_actions=("review", "propose_rework"),
            review_requirement="codex",
            **common,
        ),
    }


__all__ = [
    "RoleAssignmentError",
    "RoleInstance",
    "RoleManifest",
    "RoleManifestError",
    "RoleTaskAdmission",
    "RoleTaskAssignment",
    "RoleTaskProfile",
    "builtin_role_manifests",
    "validate_assignment_set",
]
