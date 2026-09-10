"""Small provider-neutral protocol for the v2 bootstrap.

The module intentionally uses only the Python standard library. Provider SDK
objects must be converted to these records inside an adapter before entering
the Kernel.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Any, Mapping, TypeVar
from uuid import UUID, uuid4


class ProtocolError(ValueError):
    """Raised when a protocol record is invalid or cannot be decoded."""


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_RECONCILIATION = "waiting_reconciliation"
    BLOCKED_QUOTA = "blocked_quota"
    BLOCKED_BUDGET = "blocked_budget"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskClass(str, Enum):
    NORMAL = "normal"
    RECOVERY = "recovery"


class TaskType(str, Enum):
    """Semantic work class used by the Phase 7 intelligence policy."""

    DETERMINISTIC = "deterministic"
    WORKER = "worker"
    REASONING = "reasoning"
    EXPERT = "expert"
    DELEGATED_AGENT = "delegated_agent"
    RECOVERY = "recovery"
    PROTECTED = "protected"


class RiskLevel(str, Enum):
    """Risk classification independent from the execution authority class."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class IntelligenceTier(str, Enum):
    """Bounded intelligence hierarchy; a model cannot set this field itself."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


_RECOVERY_TASK_AUTHORITY = object()


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(value: str | None, name: str) -> str:
    candidate = value or str(uuid4())
    try:
        UUID(candidate)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProtocolError(f"{name} must be a UUID string") from exc
    return candidate


def _text(value: str, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ProtocolError(f"{name} must be a non-empty string")
    return value


def _mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{name} must be an object")
    result = dict(value)
    try:
        json.dumps(result)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be JSON serializable") from exc
    return result


def _finite_nonnegative(value: int | float, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a finite non-negative number")
    if value < 0 or not math.isfinite(value):
        raise ProtocolError(f"{name} must be a finite non-negative number")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"{name} must be a positive integer")
    return value


def _enum(value: Any, enum_type: type[Enum], name: str) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ProtocolError(f"{name} must be one of: {allowed}") from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


T = TypeVar("T")


def _json_dict(record: Any) -> dict[str, Any]:
    result = _json_value(record)
    assert isinstance(result, dict)
    return result


@dataclass
class ExecutionLimits:
    """Finite limits; ``None`` is deliberately not a valid unlimited value."""

    max_steps: int = 20
    max_depth: int = 4
    max_child_tasks: int = 8
    max_model_calls: int = 12
    max_tool_calls: int = 20
    max_retries: int = 2
    max_wall_time_seconds: float = 300.0
    max_input_tokens: int = 16_000
    max_output_tokens: int = 2_048
    max_cost: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "max_steps",
            "max_depth",
            "max_child_tasks",
            "max_model_calls",
            "max_tool_calls",
            "max_retries",
            "max_input_tokens",
            "max_output_tokens",
        ):
            _positive_int(getattr(self, name), name)
        _finite_nonnegative(self.max_wall_time_seconds, "max_wall_time_seconds")
        _finite_nonnegative(self.max_cost, "max_cost")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionLimits":
        if not isinstance(data, Mapping):
            raise ProtocolError("limits must be an object")
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise ProtocolError(f"invalid limits: {exc}") from exc


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: str(uuid4()))
    objective: str = ""
    parent_task_id: str | None = None
    root_task_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.QUEUED
    depth: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    workflow_id: str | None = None
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    metadata: dict[str, Any] = field(default_factory=dict)
    task_class: TaskClass = TaskClass.NORMAL
    task_type: TaskType = TaskType.WORKER
    required_capabilities: list[str] = field(default_factory=list)
    risk: RiskLevel = RiskLevel.NORMAL
    # Privacy classification is part of the durable Task contract.  Resource
    # routing uses the same ordered vocabulary, so a caller cannot silently
    # fall back to normal cloud routing by omitting it from ModelRequest.
    sensitivity: str = "normal"
    _authority: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.task_id = _id(self.task_id, "task_id")
        self.objective = _text(self.objective, "objective")
        self.parent_task_id = _id(self.parent_task_id, "parent_task_id") if self.parent_task_id else None
        self.root_task_id = _id(self.root_task_id, "root_task_id") if self.root_task_id else self.task_id
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 0:
            raise ProtocolError("depth must be a non-negative integer")
        self.status = _enum(self.status, TaskStatus, "status")  # type: ignore[assignment]
        self.task_class = _enum(self.task_class, TaskClass, "task_class")  # type: ignore[assignment]
        self.task_type = _enum(self.task_type, TaskType, "task_type")  # type: ignore[assignment]
        self.risk = _enum(self.risk, RiskLevel, "risk")  # type: ignore[assignment]
        if not isinstance(self.sensitivity, str) or self.sensitivity.strip().lower() not in {"public", "normal", "internal", "sensitive"}:
            raise ProtocolError("sensitivity must be one of public, normal, internal, or sensitive")
        self.sensitivity = self.sensitivity.strip().lower()
        if self.task_class is TaskClass.RECOVERY and self._authority is not _RECOVERY_TASK_AUTHORITY:
            raise ProtocolError("recovery tasks must be created by RecoveryTaskAuthority")
        if self.task_class is TaskClass.RECOVERY and self.task_type is not TaskType.RECOVERY:
            raise ProtocolError("recovery tasks must use task_type=recovery")
        self.inputs = _mapping(self.inputs, "inputs")
        self.constraints = _mapping(self.constraints, "constraints")
        self.metadata = _mapping(self.metadata, "metadata")
        if not isinstance(self.required_capabilities, list):
            raise ProtocolError("required_capabilities must be a list of non-empty strings")
        capabilities: list[str] = []
        for capability in self.required_capabilities:
            if not isinstance(capability, str) or not capability.strip():
                raise ProtocolError("required_capabilities must be a list of non-empty strings")
            normalized = capability.strip()
            if normalized not in capabilities:
                capabilities.append(normalized)
        self.required_capabilities = capabilities
        if not isinstance(self.limits, ExecutionLimits):
            self.limits = ExecutionLimits.from_dict(self.limits)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        value = _json_dict(self)
        value.pop("_authority", None)
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        return cls._from_dict(data, authority=None)

    @classmethod
    def from_persisted_dict(cls, data: Mapping[str, Any]) -> "Task":
        """Rehydrate a task already accepted by a trusted durable StateStore."""
        persisted_class = _enum(data.get("task_class", TaskClass.NORMAL), TaskClass, "task_class")
        authority = _RECOVERY_TASK_AUTHORITY if persisted_class is TaskClass.RECOVERY else None
        return cls._from_dict(data, authority=authority)

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any], *, authority: object | None) -> "Task":
        values = dict(data)
        values["status"] = _enum(values.get("status", TaskStatus.QUEUED), TaskStatus, "status")
        values["limits"] = ExecutionLimits.from_dict(values.get("limits", {}))
        if authority is _RECOVERY_TASK_AUTHORITY and "task_type" not in values:
            values["task_type"] = TaskType.RECOVERY
        values["_authority"] = authority
        try:
            return cls(**values)
        except TypeError as exc:
            raise ProtocolError(f"invalid task: {exc}") from exc


class RecoveryTaskAuthority:
    """Create the explicitly classified Tasks allowed to use Recovery Reserve."""

    @staticmethod
    def create(**values: Any) -> Task:
        requested = values.pop("task_class", TaskClass.RECOVERY)
        if _enum(requested, TaskClass, "task_class") is not TaskClass.RECOVERY:
            raise ProtocolError("RecoveryTaskAuthority can create only recovery tasks")
        requested_type = values.pop("task_type", TaskType.RECOVERY)
        if _enum(requested_type, TaskType, "task_type") is not TaskType.RECOVERY:
            raise ProtocolError("RecoveryTaskAuthority can create only recovery task types")
        values["task_class"] = TaskClass.RECOVERY
        values["task_type"] = TaskType.RECOVERY
        values["_authority"] = _RECOVERY_TASK_AUTHORITY
        return Task(**values)


@dataclass
class Step:
    step_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    order: int = 0
    kind: str = ""
    status: StepStatus = StepStatus.PENDING
    input_ref: str | None = None
    output_ref: str | None = None
    attempt: int = 0
    model_ref: str | None = None
    provider_ref: str | None = None
    tool_ref: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    error_ref: str | None = None

    def __post_init__(self) -> None:
        self.step_id = _id(self.step_id, "step_id")
        self.task_id = _id(self.task_id, "task_id")
        if isinstance(self.order, bool) or not isinstance(self.order, int) or self.order < 0:
            raise ProtocolError("order must be a non-negative integer")
        self.kind = _text(self.kind, "kind")
        self.status = _enum(self.status, StepStatus, "status")  # type: ignore[assignment]
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ProtocolError("attempt must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Step":
        values = dict(data)
        values["status"] = _enum(values.get("status", StepStatus.PENDING), StepStatus, "status")
        try:
            return cls(**values)
        except TypeError as exc:
            raise ProtocolError(f"invalid step: {exc}") from exc


@dataclass
class ToolCall:
    call_id: str = field(default_factory=lambda: str(uuid4()))
    provider_call_id: str | None = None
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    originating_request_id: str | None = None
    originating_response_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        self.call_id = _id(self.call_id, "call_id")
        if self.provider_call_id is not None:
            self.provider_call_id = _text(self.provider_call_id, "provider_call_id")
        self.tool_name = _text(self.tool_name, "tool_name")
        self.arguments = _mapping(self.arguments, "arguments")
        if self.originating_request_id:
            self.originating_request_id = _id(self.originating_request_id, "originating_request_id")
        if self.originating_response_id:
            self.originating_response_id = _id(self.originating_response_id, "originating_response_id")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolCall":
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise ProtocolError(f"invalid tool call: {exc}") from exc


@dataclass
class ModelRequest:
    request_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    requested_capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list["ToolResult"] = field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    max_output_tokens: int = 2_048
    sensitivity: str = "normal"
    cost_ceiling: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.request_id = _id(self.request_id, "request_id")
        self.task_id = _id(self.task_id, "task_id")
        if not isinstance(self.messages, list) or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("role"), str)
            or not isinstance(item.get("content"), str)
            for item in self.messages
        ):
            raise ProtocolError("messages must be a list of {role, content} objects")
        self.messages = [dict(item) for item in self.messages]
        self.requested_capabilities = list(self.requested_capabilities)
        self.allowed_tools = list(self.allowed_tools)
        if not isinstance(self.tool_definitions, list) or any(not isinstance(item, Mapping) for item in self.tool_definitions):
            raise ProtocolError("tool_definitions must be a list of objects")
        self.tool_definitions = [dict(item) for item in self.tool_definitions]
        if any(not isinstance(item, str) or not item.strip() for item in self.requested_capabilities + self.allowed_tools):
            raise ProtocolError("capabilities and tools must be non-empty strings")
        self.tool_results = [item if isinstance(item, ToolResult) else ToolResult.from_dict(item) for item in self.tool_results]
        if self.response_schema is not None:
            self.response_schema = _mapping(self.response_schema, "response_schema")
        self.max_output_tokens = _positive_int(self.max_output_tokens, "max_output_tokens")
        self.sensitivity = _text(self.sensitivity, "sensitivity")
        self.cost_ceiling = _finite_nonnegative(self.cost_ceiling, "cost_ceiling")
        self.metadata = _mapping(self.metadata, "metadata")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelRequest":
        values = dict(data)
        values["tool_results"] = [ToolResult.from_dict(item) for item in values.get("tool_results", [])]
        try:
            return cls(**values)
        except TypeError as exc:
            raise ProtocolError(f"invalid model request: {exc}") from exc


@dataclass
class ModelResponse:
    response_id: str = field(default_factory=lambda: str(uuid4()))
    provider: str = ""
    model: str = ""
    parts: list[str] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    text_segments: list[str] = field(default_factory=list)
    structured_output: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    raw_response_ref: str | None = None

    def __post_init__(self) -> None:
        self.response_id = _id(self.response_id, "response_id")
        self.provider = _text(self.provider, "provider")
        self.model = _text(self.model, "model")
        self.parts = list(self.parts)
        self.text_segments = list(self.text_segments)
        self.warnings = list(self.warnings)
        if any(not isinstance(item, str) for item in self.parts + self.text_segments + self.warnings):
            raise ProtocolError("response text, parts, and warnings must be strings")
        self.usage = _mapping(self.usage, "usage")
        self.tool_calls = [item if isinstance(item, ToolCall) else ToolCall.from_dict(item) for item in self.tool_calls]
        if self.structured_output is not None:
            self.structured_output = _mapping(self.structured_output, "structured_output")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelResponse":
        values = dict(data)
        values["tool_calls"] = [ToolCall.from_dict(item) for item in values.get("tool_calls", [])]
        try:
            return cls(**values)
        except TypeError as exc:
            raise ProtocolError(f"invalid model response: {exc}") from exc


@dataclass
class ToolResult:
    call_id: str = ""
    tool_name: str | None = None
    provider_call_id: str | None = None
    status: ToolResultStatus = ToolResultStatus.SUCCEEDED
    structured_result: dict[str, Any] = field(default_factory=dict)
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    side_effects: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.call_id = _id(self.call_id, "call_id")
        if self.tool_name is not None:
            self.tool_name = _text(self.tool_name, "tool_name")
        if self.provider_call_id is not None:
            self.provider_call_id = _text(self.provider_call_id, "provider_call_id")
        self.status = _enum(self.status, ToolResultStatus, "status")  # type: ignore[assignment]
        self.structured_result = _mapping(self.structured_result, "structured_result")
        self.side_effects = _mapping(self.side_effects, "side_effects")
        if self.error is not None:
            self.error = _mapping(self.error, "error")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolResult":
        values = dict(data)
        values["status"] = _enum(values.get("status", ToolResultStatus.SUCCEEDED), ToolResultStatus, "status")
        try:
            return cls(**values)
        except TypeError as exc:
            raise ProtocolError(f"invalid tool result: {exc}") from exc


@dataclass
class Event:
    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_type: str = ""
    task_id: str = ""
    step_id: str | None = None
    request_id: str | None = None
    tool_call_id: str | None = None
    provider: str | None = None
    model: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.event_id = _id(self.event_id, "event_id")
        self.event_type = _text(self.event_type, "event_type")
        self.task_id = _id(self.task_id, "task_id")
        for name in ("step_id", "request_id", "tool_call_id"):
            value = getattr(self, name)
            if value:
                setattr(self, name, _id(value, name))
        self.payload = _mapping(self.payload, "payload")

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Event":
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise ProtocolError(f"invalid event: {exc}") from exc


def dumps(record: Any, *, indent: int | None = None) -> str:
    """Serialize a protocol record without exposing provider objects."""
    if not hasattr(record, "to_dict"):
        raise ProtocolError("record must provide to_dict()")
    return json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


__all__ = [
    "Event",
    "ExecutionLimits",
    "IntelligenceTier",
    "ModelRequest",
    "ModelResponse",
    "ProtocolError",
    "RiskLevel",
    "Step",
    "StepStatus",
    "Task",
    "TaskStatus",
    "TaskType",
    "ToolCall",
    "ToolResult",
    "ToolResultStatus",
    "dumps",
]
