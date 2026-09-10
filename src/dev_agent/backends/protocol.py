"""Thin external AgentBackend contract.

An AgentBackend is a higher-level external execution harness, not a
ModelProvider and not an owner of dev_agent task state, scheduling, budget,
approval, or recovery.  Concrete adapters are intentionally out of scope for
this module; they must translate their own session/event schemas at this
boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(values: Any, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence of strings")
    try:
        normalized = tuple(_text(value, f"{name}[]") for value in values)
    except TypeError as exc:
        raise ValueError(f"{name} must be a sequence of strings") from exc
    return normalized


class AgentBackendStatus(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"


@dataclass(frozen=True)
class AgentBackendIdentity:
    backend_id: str
    backend_version: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_id", _text(self.backend_id, "backend_id"))
        object.__setattr__(self, "backend_version", _text(self.backend_version, "backend_version"))
        object.__setattr__(self, "capabilities", _strings(self.capabilities, "capabilities"))


@dataclass(frozen=True)
class AgentBackendScope:
    """Opaque workspace/scope labels passed to an external backend.

    Path canonicalization, protected paths, and authority remain owned by the
    existing DevFarm/Control Plane boundaries; this value is only a typed
    handoff envelope.
    """

    workspace_id: str
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _text(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "allowed_paths", _strings(self.allowed_paths, "allowed_paths"))


@dataclass(frozen=True)
class AgentBackendRequest:
    task_id: str
    objective: str
    scope: AgentBackendScope
    input_artifacts: tuple[str, ...] = ()
    session_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        if not isinstance(self.scope, AgentBackendScope):
            raise TypeError("scope must be an AgentBackendScope")
        object.__setattr__(self, "input_artifacts", _strings(self.input_artifacts, "input_artifacts"))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class AgentBackendSession:
    session_id: str
    task_id: str
    backend_id: str
    status: AgentBackendStatus = AgentBackendStatus.PREPARED

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "backend_id", _text(self.backend_id, "backend_id"))
        if not isinstance(self.status, AgentBackendStatus):
            raise TypeError("status must be an AgentBackendStatus")


@dataclass(frozen=True)
class AgentBackendEvent:
    session_id: str
    sequence: int
    event_type: str
    status: AgentBackendStatus | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        object.__setattr__(self, "event_type", _text(self.event_type, "event_type"))
        if self.status is not None and not isinstance(self.status, AgentBackendStatus):
            raise TypeError("status must be an AgentBackendStatus or None")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class AgentBackendResult:
    session_id: str
    status: AgentBackendStatus
    output_artifacts: tuple[str, ...] = ()
    reconciliation_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if self.status not in {
            AgentBackendStatus.COMPLETED,
            AgentBackendStatus.FAILED,
            AgentBackendStatus.CANCELLED,
            AgentBackendStatus.UNKNOWN,
            AgentBackendStatus.RECONCILING,
            AgentBackendStatus.WAITING_APPROVAL,
        }:
            raise ValueError("result status must represent a completed or waiting backend outcome")
        object.__setattr__(self, "output_artifacts", _strings(self.output_artifacts, "output_artifacts"))
        if not isinstance(self.reconciliation_metadata, Mapping):
            raise TypeError("reconciliation_metadata must be a mapping")
        object.__setattr__(self, "reconciliation_metadata", dict(self.reconciliation_metadata))


@runtime_checkable
class AgentBackend(Protocol):
    """Adapter contract; authority and durable lifecycle stay outside it."""

    @property
    def identity(self) -> AgentBackendIdentity:
        ...

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        ...

    def events(self, session_id: str) -> Iterable[AgentBackendEvent]:
        ...

    def cancel(self, session_id: str) -> None:
        ...

    def result(self, session_id: str) -> AgentBackendResult:
        ...


__all__ = [
    "AgentBackend",
    "AgentBackendEvent",
    "AgentBackendIdentity",
    "AgentBackendRequest",
    "AgentBackendResult",
    "AgentBackendScope",
    "AgentBackendSession",
    "AgentBackendStatus",
]
