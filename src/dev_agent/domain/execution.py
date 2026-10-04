"""Canonical execution vocabulary shared by runtime and resource routing.

Provider adapters may keep their native request/response classes, but those
classes must not become a second core protocol.  The v2 kernel already has
``ModelRequest`` and ``ModelResponse`` for that purpose; this module adds the
host-owned execution requirement that determines whether a route may be used.

The presence wrapper is deliberately small.  ``None`` is not overloaded to
mean both "not supplied" and "explicitly disabled": callers must choose one
of ``UNSPECIFIED``, ``VALUE`` or ``EXPLICIT_NONE``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any, Generic, TypeVar

from .protocol import IntelligenceTier, ModelRequest, ModelResponse, ProtocolError, RiskLevel, TaskStatus, TaskType


T = TypeVar("T")


class PresenceState(str, Enum):
    """Whether a bounded request field was omitted, supplied, or disabled."""

    UNSPECIFIED = "UNSPECIFIED"
    VALUE = "VALUE"
    EXPLICIT_NONE = "EXPLICIT_NONE"


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class FieldPresence(Generic[T]):
    """Lossless representation of an optional canonical field.

    ``payload`` is present only for ``VALUE``.  The classmethod is named
    ``value`` so call sites read naturally while the instance attribute keeps
    the state/value distinction explicit.
    """

    state: PresenceState
    payload: T | None = None

    def __post_init__(self) -> None:
        try:
            state = self.state if isinstance(self.state, PresenceState) else PresenceState(self.state)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("field presence state must be UNSPECIFIED, VALUE, or EXPLICIT_NONE") from exc
        object.__setattr__(self, "state", state)
        if state is PresenceState.VALUE:
            if self.payload is None:
                raise ProtocolError("VALUE cannot contain None; use EXPLICIT_NONE")
            try:
                json.dumps(_json_value(self.payload), ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ProtocolError("VALUE payload must be JSON serializable") from exc
        elif self.payload is not None:
            raise ProtocolError("UNSPECIFIED and EXPLICIT_NONE cannot contain a payload")

    @classmethod
    def unspecified(cls) -> "FieldPresence[T]":
        return cls(PresenceState.UNSPECIFIED)

    @classmethod
    def value(cls, payload: T) -> "FieldPresence[T]":
        return cls(PresenceState.VALUE, payload)

    @classmethod
    def explicit_none(cls) -> "FieldPresence[T]":
        return cls(PresenceState.EXPLICIT_NONE)

    @property
    def is_value(self) -> bool:
        return self.state is PresenceState.VALUE

    @property
    def is_unspecified(self) -> bool:
        return self.state is PresenceState.UNSPECIFIED

    @property
    def is_explicit_none(self) -> bool:
        return self.state is PresenceState.EXPLICIT_NONE

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state.value}
        if self.is_value:
            result["value"] = _json_value(self.payload)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldPresence[Any]":
        if not isinstance(data, Mapping):
            raise ProtocolError("field presence must be an object")
        try:
            state = PresenceState(data.get("state"))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("invalid field presence state") from exc
        if state is PresenceState.VALUE:
            if "value" not in data:
                raise ProtocolError("VALUE field presence requires value")
            return cls.value(data["value"])
        if "value" in data:
            raise ProtocolError("non-VALUE field presence must not contain value")
        return cls(state)


class UnsupportedCapabilityError(ProtocolError):
    """A requested execution feature is not supported by the candidate route."""

    code = "UNSUPPORTED_CAPABILITY"

    def __init__(self, capabilities: Iterable[str]) -> None:
        normalized = tuple(sorted({item.strip() for item in capabilities if isinstance(item, str) and item.strip()}))
        if not normalized:
            raise ValueError("capabilities must contain at least one non-empty name")
        self.capabilities = normalized
        super().__init__(f"unsupported execution capabilities: {', '.join(normalized)}")


class ExecutionLifecycleStage(str, Enum):
    """One provider-neutral lifecycle vocabulary for executable work.

    Operation and DevFarm keep their own storage/projection formats at their
    boundaries, but observations crossing that boundary use these stages.
    This is deliberately a vocabulary, not a second scheduler or state store.
    """

    CREATED = "created"
    READY = "ready"
    HANDOFF_PENDING = "handoff_pending"
    HANDED_OFF = "handed_off"
    ATTEMPT_ACTIVE = "attempt_active"
    RESULT_RECEIVED = "result_received"
    HOST_VERIFIED = "host_verified"
    REVIEWED = "reviewed"
    INTEGRATED = "integrated"
    COMPLETED = "completed"
    WAITING = "waiting"
    FAILED = "failed"
    BLOCKED = "blocked"


_LIFECYCLE_ORDER = {
    ExecutionLifecycleStage.CREATED: 0,
    ExecutionLifecycleStage.READY: 1,
    ExecutionLifecycleStage.HANDOFF_PENDING: 2,
    ExecutionLifecycleStage.HANDED_OFF: 3,
    ExecutionLifecycleStage.ATTEMPT_ACTIVE: 4,
    ExecutionLifecycleStage.WAITING: 4.5,
    ExecutionLifecycleStage.RESULT_RECEIVED: 5,
    ExecutionLifecycleStage.HOST_VERIFIED: 6,
    ExecutionLifecycleStage.REVIEWED: 7,
    ExecutionLifecycleStage.INTEGRATED: 8,
    ExecutionLifecycleStage.COMPLETED: 9,
    ExecutionLifecycleStage.FAILED: 9,
    ExecutionLifecycleStage.BLOCKED: 4.5,
}


def operation_lifecycle_stage(
    status: TaskStatus | str,
    *,
    handoff_pending: bool = False,
) -> ExecutionLifecycleStage:
    """Project an Operation ``TaskStatus`` into the canonical vocabulary."""

    try:
        normalized = status if isinstance(status, TaskStatus) else TaskStatus(status)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("unsupported Operation lifecycle status") from exc
    if normalized is TaskStatus.WAITING_DEPENDENCY and handoff_pending:
        return ExecutionLifecycleStage.HANDOFF_PENDING
    if normalized in {TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.READY}:
        return ExecutionLifecycleStage.READY
    if normalized is TaskStatus.RUNNING:
        return ExecutionLifecycleStage.ATTEMPT_ACTIVE
    if normalized in {
        TaskStatus.WAITING_DEPENDENCY,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WAITING_HUMAN,
        TaskStatus.WAITING_RECONCILIATION,
    }:
        return ExecutionLifecycleStage.WAITING
    if normalized in {TaskStatus.BLOCKED_QUOTA, TaskStatus.BLOCKED_BUDGET}:
        return ExecutionLifecycleStage.BLOCKED
    if normalized is TaskStatus.COMPLETED:
        return ExecutionLifecycleStage.COMPLETED
    if normalized in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return ExecutionLifecycleStage.FAILED
    raise ProtocolError("unsupported Operation lifecycle status")


def devfarm_lifecycle_stage(status: str) -> ExecutionLifecycleStage:
    """Project a Commander/DevFarm status into the canonical vocabulary."""

    if not isinstance(status, str) or not status.strip():
        raise ProtocolError("DevFarm lifecycle status must be a non-empty string")
    normalized = status.strip().upper()
    mapping = {
        "PLANNED": ExecutionLifecycleStage.CREATED,
        "READY": ExecutionLifecycleStage.READY,
        "DISPATCHED": ExecutionLifecycleStage.ATTEMPT_ACTIVE,
        "PROPOSED": ExecutionLifecycleStage.RESULT_RECEIVED,
        "HOST_VERIFIED": ExecutionLifecycleStage.HOST_VERIFIED,
        "INTEGRATED": ExecutionLifecycleStage.INTEGRATED,
        "REJECTED": ExecutionLifecycleStage.FAILED,
        "BLOCKED": ExecutionLifecycleStage.BLOCKED,
        "SUPERSEDED": ExecutionLifecycleStage.FAILED,
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ProtocolError("unsupported DevFarm lifecycle status") from exc


def _bounded_identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        raise ProtocolError(f"{name} must be a bounded non-empty string")
    return value.strip()


@dataclass(frozen=True)
class CanonicalExecutionBinding:
    """Stable identity and bounded facts for one logical execution.

    The binding is the small canonical fact envelope shared by Operation and
    DevFarm.  It is not a second queue or state store.  The optional fields
    after ``stage`` describe the current attempt and the authorities that have
    observed it; provider/backend-native payloads remain outside this type.
    """

    logical_execution_id: str
    proposal_id: str
    child_key: str
    executor_kind: str
    backend_task_id: str | None = None
    stage: ExecutionLifecycleStage = ExecutionLifecycleStage.CREATED
    attempt_id: str | None = None
    result_ref: str | None = None
    verification_id: str | None = None
    review_decision_id: str | None = None
    integration_revision: str | None = None
    dependency_satisfied: bool | None = None

    _UNSET = object()

    def __post_init__(self) -> None:
        for name in ("logical_execution_id", "proposal_id", "child_key", "executor_kind"):
            object.__setattr__(self, name, _bounded_identity(getattr(self, name), name))
        if self.backend_task_id is not None:
            object.__setattr__(self, "backend_task_id", _bounded_identity(self.backend_task_id, "backend_task_id"))
        for name in (
            "attempt_id",
            "result_ref",
            "verification_id",
            "review_decision_id",
            "integration_revision",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _bounded_identity(value, name))
        if self.dependency_satisfied is not None and not isinstance(self.dependency_satisfied, bool):
            raise ProtocolError("dependency_satisfied must be a boolean when present")
        try:
            stage = self.stage if isinstance(self.stage, ExecutionLifecycleStage) else ExecutionLifecycleStage(self.stage)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("stage must be a supported execution lifecycle stage") from exc
        object.__setattr__(self, "stage", stage)

    @classmethod
    def for_child(
        cls,
        *,
        logical_execution_id: str,
        proposal_id: str,
        child_key: str,
        executor_kind: str,
    ) -> "CanonicalExecutionBinding":
        return cls(
            logical_execution_id=logical_execution_id,
            proposal_id=proposal_id,
            child_key=child_key,
            executor_kind=executor_kind,
        )

    def with_backend_task(self, backend_task_id: str) -> "CanonicalExecutionBinding":
        backend_task_id = _bounded_identity(backend_task_id, "backend_task_id")
        if self.backend_task_id is not None and self.backend_task_id != backend_task_id:
            raise ProtocolError("conflicting backend task identity")
        return replace(
            self,
            backend_task_id=backend_task_id,
            stage=ExecutionLifecycleStage.HANDED_OFF,
        )

    def with_stage(self, stage: ExecutionLifecycleStage | str) -> "CanonicalExecutionBinding":
        return replace(self, stage=stage)

    def with_observation(
        self,
        *,
        stage: ExecutionLifecycleStage | str | None = None,
        attempt_id: str | None | object = _UNSET,
        result_ref: str | None | object = _UNSET,
        verification_id: str | None | object = _UNSET,
        review_decision_id: str | None | object = _UNSET,
        integration_revision: str | None | object = _UNSET,
        dependency_satisfied: bool | None | object = _UNSET,
    ) -> "CanonicalExecutionBinding":
        """Return an immutable observation without mutating prior evidence.

        ``_UNSET`` means that an observation did not speak about a fact;
        explicit ``None`` is useful for a fresh attempt because it clears
        facts that belonged to the prior attempt.
        """

        values: dict[str, Any] = {}
        if stage is not None:
            values["stage"] = stage
        for name, value in (
            ("attempt_id", attempt_id),
            ("result_ref", result_ref),
            ("verification_id", verification_id),
            ("review_decision_id", review_decision_id),
            ("integration_revision", integration_revision),
            ("dependency_satisfied", dependency_satisfied),
        ):
            if value is not self._UNSET:
                values[name] = value
        return replace(self, **values)

    def for_retry(self) -> "CanonicalExecutionBinding":
        """Start a new mutable attempt without erasing immutable identity."""

        if self.stage in {ExecutionLifecycleStage.INTEGRATED, ExecutionLifecycleStage.COMPLETED}:
            raise ProtocolError("integrated execution cannot be retried")
        return replace(
            self,
            stage=ExecutionLifecycleStage.READY,
            attempt_id=None,
            result_ref=None,
            verification_id=None,
            review_decision_id=None,
            integration_revision=None,
            dependency_satisfied=None,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "logical_execution_id": self.logical_execution_id,
            "proposal_id": self.proposal_id,
            "child_key": self.child_key,
            "executor_kind": self.executor_kind,
            "stage": self.stage.value,
        }
        if self.backend_task_id is not None:
            result["backend_task_id"] = self.backend_task_id
        for name in (
            "attempt_id",
            "result_ref",
            "verification_id",
            "review_decision_id",
            "integration_revision",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        if self.dependency_satisfied is not None:
            result["dependency_satisfied"] = self.dependency_satisfied
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalExecutionBinding":
        if not isinstance(data, Mapping):
            raise ProtocolError("canonical execution binding must be an object")
        return cls(
            logical_execution_id=data.get("logical_execution_id"),
            proposal_id=data.get("proposal_id"),
            child_key=data.get("child_key"),
            executor_kind=data.get("executor_kind"),
            backend_task_id=data.get("backend_task_id"),
            stage=data.get("stage", ExecutionLifecycleStage.CREATED),
            attempt_id=data.get("attempt_id"),
            result_ref=data.get("result_ref"),
            verification_id=data.get("verification_id"),
            review_decision_id=data.get("review_decision_id"),
            integration_revision=data.get("integration_revision"),
            dependency_satisfied=data.get("dependency_satisfied"),
        )

    def merge_observation(self, observation: "CanonicalExecutionBinding") -> "CanonicalExecutionBinding":
        """Merge a replayed observation, rejecting identity/lifecycle conflicts."""

        if not isinstance(observation, CanonicalExecutionBinding):
            raise ProtocolError("canonical execution observation must use the binding type")
        for name in ("logical_execution_id", "proposal_id", "child_key", "executor_kind"):
            if getattr(self, name) != getattr(observation, name):
                raise ProtocolError(f"canonical execution {name} identity conflict")
        if self.backend_task_id is not None and observation.backend_task_id is not None:
            if self.backend_task_id != observation.backend_task_id:
                raise ProtocolError("conflicting backend task identity")
        if self.stage in {ExecutionLifecycleStage.INTEGRATED, ExecutionLifecycleStage.COMPLETED}:
            if observation.attempt_id != self.attempt_id or observation.stage is not self.stage:
                raise ProtocolError("terminal lifecycle cannot change attempt or stage")
        if (
            self.attempt_id is not None
            and observation.attempt_id is not None
            and observation.attempt_id != self.attempt_id
        ):
            if observation.stage not in {
                ExecutionLifecycleStage.READY,
                ExecutionLifecycleStage.HANDOFF_PENDING,
                ExecutionLifecycleStage.HANDED_OFF,
                ExecutionLifecycleStage.ATTEMPT_ACTIVE,
            }:
                raise ProtocolError("fresh attempt must begin before result or verification")
            return CanonicalExecutionBinding(
                logical_execution_id=self.logical_execution_id,
                proposal_id=self.proposal_id,
                child_key=self.child_key,
                executor_kind=self.executor_kind,
                backend_task_id=self.backend_task_id or observation.backend_task_id,
                stage=observation.stage,
                attempt_id=observation.attempt_id,
                result_ref=observation.result_ref,
                verification_id=observation.verification_id,
                review_decision_id=observation.review_decision_id,
                integration_revision=observation.integration_revision,
                dependency_satisfied=observation.dependency_satisfied,
            )
        current_rank = _LIFECYCLE_ORDER[self.stage]
        observed_rank = _LIFECYCLE_ORDER[observation.stage]
        if observed_rank < current_rank:
            raise ProtocolError("canonical execution lifecycle regression")
        if observed_rank == current_rank and observation.stage is not self.stage:
            raise ProtocolError("canonical execution lifecycle stage conflict")
        fact_values: dict[str, Any] = {}
        for name in (
            "attempt_id",
            "result_ref",
            "verification_id",
            "review_decision_id",
            "integration_revision",
        ):
            current = getattr(self, name)
            observed = getattr(observation, name)
            if current is not None and observed is not None and current != observed:
                label = {
                    "result_ref": "result reference",
                    "verification_id": "verification identity",
                    "review_decision_id": "review decision identity",
                    "integration_revision": "integration revision",
                }.get(name, name.replace("_", " "))
                raise ProtocolError(f"conflicting {label}")
            fact_values[name] = current or observed
        current_dependency = self.dependency_satisfied
        observed_dependency = observation.dependency_satisfied
        if current_dependency is True and observed_dependency is False:
            raise ProtocolError("dependency satisfaction cannot regress")
        if current_dependency is True or observed_dependency is True:
            fact_values["dependency_satisfied"] = True
        elif current_dependency is False or observed_dependency is False:
            fact_values["dependency_satisfied"] = False
        else:
            fact_values["dependency_satisfied"] = None
        return CanonicalExecutionBinding(
            logical_execution_id=self.logical_execution_id,
            proposal_id=self.proposal_id,
            child_key=self.child_key,
            executor_kind=self.executor_kind,
            backend_task_id=self.backend_task_id or observation.backend_task_id,
            stage=observation.stage if observed_rank > current_rank else self.stage,
            **fact_values,
        )


def _presence_from_metadata(metadata: Mapping[str, Any], key: str, transform=lambda value: value) -> FieldPresence[Any]:
    if key not in metadata:
        return FieldPresence.unspecified()
    value = metadata[key]
    if value is None:
        return FieldPresence.explicit_none()
    try:
        return FieldPresence.value(transform(value))
    except (TypeError, ValueError, ProtocolError) as exc:
        raise ProtocolError(f"invalid execution requirement field: {key}") from exc


def _enum_value(value: Any, enum_type: type[Enum], name: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be a supported value") from exc


def _tier_tuple(value: Any) -> tuple[IntelligenceTier, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ProtocolError("allowed_intelligence_tiers must be a collection")
    result = tuple(sorted({_enum_value(item, IntelligenceTier, "intelligence tier") for item in value}, key=lambda tier: tier.value))
    if not result:
        raise ProtocolError("allowed_intelligence_tiers must not be empty")
    return result


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ProtocolError(f"{name} must be a collection")
    normalized = tuple(sorted({item.strip() for item in value if isinstance(item, str) and item.strip()}))
    if len(normalized) != len(value):
        raise ProtocolError(f"{name} must contain only non-empty strings")
    return normalized


@dataclass(frozen=True)
class RouteConstraints:
    """Typed routing projection carried by an ``ExecutionRequirement``."""

    allowed_intelligence_tiers: FieldPresence[tuple[IntelligenceTier, ...]] = field(default_factory=FieldPresence.unspecified)
    max_cost_minor: FieldPresence[int] = field(default_factory=FieldPresence.unspecified)
    max_latency_ms: FieldPresence[int] = field(default_factory=FieldPresence.unspecified)
    allow_unknown_quota: FieldPresence[bool] = field(default_factory=FieldPresence.unspecified)
    task_fit: FieldPresence[str] = field(default_factory=FieldPresence.unspecified)
    minimum_task_fit_score: FieldPresence[float] = field(default_factory=FieldPresence.unspecified)
    allowed_providers: FieldPresence[tuple[str, ...]] = field(default_factory=FieldPresence.unspecified)
    allowed_provider_binding_ids: FieldPresence[tuple[str, ...]] = field(default_factory=FieldPresence.unspecified)
    excluded_provider_binding_ids: FieldPresence[tuple[str, ...]] = field(default_factory=FieldPresence.unspecified)
    max_observation_age_seconds: FieldPresence[float] = field(default_factory=FieldPresence.unspecified)
    max_quota_observation_age_seconds: FieldPresence[float] = field(default_factory=FieldPresence.unspecified)
    prefer_diversity: FieldPresence[bool] = field(default_factory=FieldPresence.unspecified)

    def to_dict(self) -> dict[str, Any]:
        return {name: value.to_dict() for name, value in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RouteConstraints":
        if not isinstance(data, Mapping):
            raise ProtocolError("route_constraints must be an object")
        values = {}
        for name in cls.__dataclass_fields__:
            raw = data.get(name)
            values[name] = FieldPresence.from_dict(raw) if raw is not None else FieldPresence.unspecified()
        if values["allowed_intelligence_tiers"].is_value:
            values["allowed_intelligence_tiers"] = FieldPresence.value(_tier_tuple(values["allowed_intelligence_tiers"].payload))
        for name in ("allowed_providers", "allowed_provider_binding_ids", "excluded_provider_binding_ids"):
            if values[name].is_value:
                values[name] = FieldPresence.value(_string_tuple(values[name].payload, name))
        return cls(**values)


_TIER_ORDER = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)


@dataclass(frozen=True)
class ExecutionRequirement:
    """Host-owned canonical meaning of one executable model turn."""

    role: str = "worker"
    task_type: TaskType = TaskType.WORKER
    required_capabilities: tuple[str, ...] = ()
    minimum_intelligence_tier: IntelligenceTier = IntelligenceTier.L0
    maximum_intelligence_tier: FieldPresence[IntelligenceTier] = field(default_factory=FieldPresence.unspecified)
    sensitivity: str = "normal"
    risk: RiskLevel = RiskLevel.NORMAL
    billing_policy: FieldPresence[str] = field(default_factory=FieldPresence.unspecified)
    quota_policy: FieldPresence[str] = field(default_factory=FieldPresence.unspecified)
    freshness_policy: FieldPresence[Any] = field(default_factory=FieldPresence.unspecified)
    liveness_policy: FieldPresence[str] = field(default_factory=FieldPresence.unspecified)
    acceptance_context: Mapping[str, Any] = field(default_factory=dict)
    feature_requirements: Mapping[str, FieldPresence[Any]] = field(default_factory=dict)
    route_constraints: RouteConstraints = field(default_factory=RouteConstraints)
    intelligence_routing: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip() or len(self.role.strip()) > 64:
            raise ProtocolError("role must be a bounded non-empty string")
        object.__setattr__(self, "role", self.role.strip())
        object.__setattr__(self, "task_type", _enum_value(self.task_type, TaskType, "task_type"))
        object.__setattr__(self, "risk", _enum_value(self.risk, RiskLevel, "risk"))
        object.__setattr__(self, "minimum_intelligence_tier", _enum_value(self.minimum_intelligence_tier, IntelligenceTier, "minimum_intelligence_tier"))
        if not isinstance(self.maximum_intelligence_tier, FieldPresence):
            raise ProtocolError("maximum_intelligence_tier must use FieldPresence")
        if self.maximum_intelligence_tier.is_value:
            maximum = _enum_value(self.maximum_intelligence_tier.payload, IntelligenceTier, "maximum_intelligence_tier")
            if _TIER_ORDER.index(self.minimum_intelligence_tier) > _TIER_ORDER.index(maximum):
                raise ProtocolError("minimum_intelligence_tier cannot exceed maximum_intelligence_tier")
            object.__setattr__(self, "maximum_intelligence_tier", FieldPresence.value(maximum))
        if not isinstance(self.sensitivity, str):
            raise ProtocolError("sensitivity must be public, normal, internal, or sensitive")
        object.__setattr__(self, "sensitivity", self.sensitivity.strip().lower())
        if self.sensitivity not in {"public", "normal", "internal", "sensitive"}:
            raise ProtocolError("sensitivity must be public, normal, internal, or sensitive")
        if self.required_capabilities is None:
            raise ProtocolError("required_capabilities must be a collection")
        if self.required_capabilities:
            if isinstance(self.required_capabilities, (str, bytes)):
                raise ProtocolError("required_capabilities must be a collection")
            raw_capabilities = list(self.required_capabilities)
            if any(not isinstance(item, str) or not item.strip() for item in raw_capabilities):
                raise ProtocolError("required_capabilities must contain only non-empty strings")
            normalized_capabilities = {item.strip() for item in raw_capabilities}
            capabilities = tuple(sorted(normalized_capabilities))
        else:
            capabilities = ()
        object.__setattr__(self, "required_capabilities", capabilities)
        if not isinstance(self.acceptance_context, Mapping):
            raise ProtocolError("acceptance_context must be an object")
        if len(self.acceptance_context) > 32:
            raise ProtocolError("acceptance_context is too large")
        try:
            json.dumps(_json_value(self.acceptance_context), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("acceptance_context must be JSON serializable") from exc
        object.__setattr__(self, "acceptance_context", dict(self.acceptance_context))
        if not isinstance(self.feature_requirements, Mapping) or len(self.feature_requirements) > 32:
            raise ProtocolError("feature_requirements must be a bounded object")
        features: dict[str, FieldPresence[Any]] = {}
        for name, presence in self.feature_requirements.items():
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
                raise ProtocolError("feature requirement names must be bounded non-empty strings")
            if not isinstance(presence, FieldPresence):
                raise ProtocolError("feature requirements must use FieldPresence")
            features[name.strip()] = presence
        object.__setattr__(self, "feature_requirements", dict(sorted(features.items())))
        if not isinstance(self.route_constraints, RouteConstraints):
            raise ProtocolError("route_constraints must be RouteConstraints")
        if not isinstance(self.intelligence_routing, bool):
            raise ProtocolError("intelligence_routing must be a boolean")

    @classmethod
    def from_model_request(cls, request: ModelRequest, *, role: str | None = None) -> "ExecutionRequirement":
        """Normalize the existing canonical request into a routing requirement."""

        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        metadata = request.metadata
        task_type = _enum_value(metadata.get("task_type", TaskType.WORKER), TaskType, "task_type")
        minimum = _enum_value(metadata.get("minimum_intelligence_tier", IntelligenceTier.L0), IntelligenceTier, "minimum_intelligence_tier")
        maximum = _presence_from_metadata(metadata, "maximum_intelligence_tier", lambda value: _enum_value(value, IntelligenceTier, "maximum_intelligence_tier"))
        feature_raw = metadata.get("execution_features", {})
        if feature_raw is None:
            feature_raw = {}
        if not isinstance(feature_raw, Mapping):
            raise ProtocolError("execution_features must be an object")
        features: dict[str, FieldPresence[Any]] = {}
        for name, value in feature_raw.items():
            if isinstance(value, Mapping) and "state" in value:
                features[str(name)] = FieldPresence.from_dict(value)
            elif value is None:
                features[str(name)] = FieldPresence.explicit_none()
            else:
                features[str(name)] = FieldPresence.value(value)
        intelligence_routing = metadata.get("intelligence_routing") == "bounded"
        if intelligence_routing and "allowed_intelligence_tiers" not in metadata:
            raise ProtocolError("bounded intelligence routing requires allowed_intelligence_tiers")
        route = RouteConstraints(
            allowed_intelligence_tiers=_presence_from_metadata(metadata, "allowed_intelligence_tiers", _tier_tuple),
            max_cost_minor=_presence_from_metadata(metadata, "max_cost_minor", lambda value: int(value)),
            max_latency_ms=_presence_from_metadata(metadata, "max_latency_ms", lambda value: int(value)),
            allow_unknown_quota=_presence_from_metadata(metadata, "allow_unknown_quota", lambda value: bool(value)),
            task_fit=_presence_from_metadata(metadata, "task_fit", lambda value: str(value)),
            minimum_task_fit_score=_presence_from_metadata(metadata, "minimum_task_fit_score", lambda value: float(value)),
            allowed_providers=_presence_from_metadata(metadata, "allowed_providers", lambda value: _string_tuple(value, "allowed_providers")),
            allowed_provider_binding_ids=_presence_from_metadata(metadata, "allowed_provider_binding_ids", lambda value: _string_tuple(value, "allowed_provider_binding_ids")),
            excluded_provider_binding_ids=_presence_from_metadata(metadata, "excluded_provider_binding_ids", lambda value: _string_tuple(value, "excluded_provider_binding_ids")),
            max_observation_age_seconds=_presence_from_metadata(metadata, "max_observation_age_seconds", lambda value: float(value)),
            max_quota_observation_age_seconds=_presence_from_metadata(metadata, "max_quota_observation_age_seconds", lambda value: float(value)),
            prefer_diversity=_presence_from_metadata(metadata, "prefer_diversity", lambda value: bool(value)),
        )
        return cls(
            role=str(role or metadata.get("execution_role") or metadata.get("role") or task_type.value),
            task_type=task_type,
            required_capabilities=tuple(request.requested_capabilities),
            minimum_intelligence_tier=minimum,
            maximum_intelligence_tier=maximum,
            sensitivity=request.sensitivity,
            risk=_enum_value(metadata.get("risk", RiskLevel.NORMAL), RiskLevel, "risk"),
            billing_policy=_presence_from_metadata(metadata, "billing_policy"),
            quota_policy=_presence_from_metadata(metadata, "quota_policy"),
            freshness_policy=_presence_from_metadata(metadata, "freshness_policy"),
            liveness_policy=_presence_from_metadata(metadata, "liveness_policy"),
            acceptance_context=metadata.get("acceptance_context", {}),
            feature_requirements=features,
            route_constraints=route,
            intelligence_routing=intelligence_routing,
        )

    def allowed_tier_values(self) -> tuple[str, ...] | None:
        """Return the exact tier set used by bounded routing, if any."""

        explicit = self.route_constraints.allowed_intelligence_tiers
        if explicit.is_value:
            if not self.intelligence_routing:
                return None
            return tuple(tier.value for tier in explicit.payload or ())
        if explicit.is_explicit_none or not self.intelligence_routing:
            return None
        maximum = self.maximum_intelligence_tier.payload if self.maximum_intelligence_tier.is_value else _TIER_ORDER[-1]
        start = _TIER_ORDER.index(self.minimum_intelligence_tier)
        end = _TIER_ORDER.index(maximum)
        return tuple(tier.value for tier in _TIER_ORDER[start : end + 1])

    def require_supported_capabilities(self, supported: Iterable[str]) -> None:
        """Fail closed when a route cannot honor a requested feature/value."""

        available = {item.strip() for item in supported if isinstance(item, str) and item.strip()}
        missing = set(self.required_capabilities) - available
        missing.update(name for name, presence in self.feature_requirements.items() if presence.is_value and name not in available)
        if missing:
            raise UnsupportedCapabilityError(missing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "task_type": self.task_type.value,
            "required_capabilities": list(self.required_capabilities),
            "minimum_intelligence_tier": self.minimum_intelligence_tier.value,
            "maximum_intelligence_tier": self.maximum_intelligence_tier.to_dict(),
            "sensitivity": self.sensitivity,
            "risk": self.risk.value,
            "billing_policy": self.billing_policy.to_dict(),
            "quota_policy": self.quota_policy.to_dict(),
            "freshness_policy": self.freshness_policy.to_dict(),
            "liveness_policy": self.liveness_policy.to_dict(),
            "acceptance_context": _json_value(self.acceptance_context),
            "feature_requirements": {name: value.to_dict() for name, value in self.feature_requirements.items()},
            "route_constraints": self.route_constraints.to_dict(),
            "intelligence_routing": self.intelligence_routing,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionRequirement":
        if not isinstance(data, Mapping):
            raise ProtocolError("execution requirement must be an object")
        raw_features = data.get("feature_requirements", {})
        if not isinstance(raw_features, Mapping):
            raise ProtocolError("feature_requirements must be an object")
        return cls(
            role=data.get("role", "worker"),
            task_type=data.get("task_type", TaskType.WORKER),
            required_capabilities=tuple(data.get("required_capabilities", ())),
            minimum_intelligence_tier=data.get("minimum_intelligence_tier", IntelligenceTier.L0),
            maximum_intelligence_tier=FieldPresence.from_dict(data.get("maximum_intelligence_tier", {"state": "UNSPECIFIED"})),
            sensitivity=data.get("sensitivity", "normal"),
            risk=data.get("risk", RiskLevel.NORMAL),
            billing_policy=FieldPresence.from_dict(data.get("billing_policy", {"state": "UNSPECIFIED"})),
            quota_policy=FieldPresence.from_dict(data.get("quota_policy", {"state": "UNSPECIFIED"})),
            freshness_policy=FieldPresence.from_dict(data.get("freshness_policy", {"state": "UNSPECIFIED"})),
            liveness_policy=FieldPresence.from_dict(data.get("liveness_policy", {"state": "UNSPECIFIED"})),
            acceptance_context=data.get("acceptance_context", {}),
            feature_requirements={name: FieldPresence.from_dict(value) for name, value in raw_features.items()},
            route_constraints=RouteConstraints.from_dict(data.get("route_constraints", {})),
            intelligence_routing=data.get("intelligence_routing", True),
        )

    def digest(self) -> str:
        canonical = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# These aliases deliberately point at the existing protocol classes.  They
# make the canonical execution boundary explicit without introducing a second
# request/result state model that could drift from ProviderDispatcher.
CanonicalExecutionRequest = ModelRequest
CanonicalExecutionResult = ModelResponse


__all__ = [
    "CanonicalExecutionBinding",
    "CanonicalExecutionRequest",
    "CanonicalExecutionResult",
    "ExecutionLifecycleStage",
    "ExecutionRequirement",
    "FieldPresence",
    "PresenceState",
    "RouteConstraints",
    "UnsupportedCapabilityError",
    "devfarm_lifecycle_stage",
    "operation_lifecycle_stage",
]
