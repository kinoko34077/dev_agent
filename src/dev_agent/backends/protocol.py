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
import re
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


def _optional_text(value: Any, name: str, *, max_length: int = 512) -> str | None:
    if value is None:
        return None
    normalized = _text(value, name)
    if len(normalized) > max_length:
        raise ValueError(f"{name} is too long")
    return normalized


def _bounded_reference_text(value: Any, name: str, *, max_length: int) -> str:
    normalized = _text(value, name)
    if len(normalized) > max_length:
        raise ValueError(f"{name} is too long")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{name} contains unsafe whitespace or control characters")
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


def _status(value: Any, name: str, *, allow_none: bool = False) -> AgentBackendStatus | None:
    if value is None and allow_none:
        return None
    try:
        return value if isinstance(value, AgentBackendStatus) else AgentBackendStatus(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be an AgentBackendStatus or valid status value") from exc


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
    client_session_key: str | None = None
    sensitivity: str = "normal"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        if not isinstance(self.scope, AgentBackendScope):
            raise TypeError("scope must be an AgentBackendScope")
        object.__setattr__(self, "input_artifacts", _strings(self.input_artifacts, "input_artifacts"))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if self.client_session_key is not None:
            object.__setattr__(self, "client_session_key", _text(self.client_session_key, "client_session_key"))
        sensitivity = _text(self.sensitivity, "sensitivity").lower()
        if sensitivity not in {"public", "normal", "internal", "sensitive"}:
            raise ValueError("sensitivity must be one of public, normal, internal, or sensitive")
        object.__setattr__(self, "sensitivity", sensitivity)
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class AgentBackendSession:
    session_id: str
    task_id: str
    backend_id: str
    status: AgentBackendStatus = AgentBackendStatus.PREPARED
    client_session_key: str | None = None
    external_session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "backend_id", _text(self.backend_id, "backend_id"))
        object.__setattr__(self, "status", _status(self.status, "status"))
        object.__setattr__(self, "client_session_key", _optional_text(self.client_session_key, "client_session_key"))
        object.__setattr__(self, "external_session_id", _optional_text(self.external_session_id, "external_session_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "backend_id": self.backend_id,
            "status": self.status.value,
            "client_session_key": self.client_session_key,
            "external_session_id": self.external_session_id,
        }


@dataclass(frozen=True)
class AgentBackendArtifactReference:
    """Bounded identity/reference for an output produced by a backend.

    The reference is metadata only. It does not grant read access, prove
    verification, or make an external URI authoritative; those decisions stay
    with the existing DevFarm, Host Verification, and artifact authorities.
    ``output_artifacts`` remains on :class:`AgentBackendResult` for backwards
    compatibility with older adapters.
    """

    artifact_id: str
    uri: str
    kind: str = "output"
    sha256: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        artifact_id = _bounded_reference_text(self.artifact_id, "artifact_id", max_length=256)
        kind = _bounded_reference_text(self.kind, "kind", max_length=128)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", kind) is None:
            raise ValueError("kind must be a structural token")
        uri = _bounded_reference_text(self.uri, "uri", max_length=2048)
        sha256 = self.sha256
        if sha256 is not None:
            sha256 = _bounded_reference_text(sha256, "sha256", max_length=64).lower()
            if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
                raise ValueError("sha256 must be a 64-character hexadecimal digest")
        size_bytes = self.size_bytes
        if size_bytes is not None and (isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0):
            raise ValueError("size_bytes must be a non-negative integer")
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "size_bytes", size_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "uri": self.uri,
            "kind": self.kind,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentBackendArtifactReference":
        if not isinstance(value, Mapping):
            raise TypeError("artifact reference must be an object")
        return cls(
            artifact_id=value.get("artifact_id"),
            uri=value.get("uri"),
            kind=value.get("kind", "output"),
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
        )


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
        object.__setattr__(self, "status", _status(self.status, "status", allow_none=True))
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class AgentBackendResult:
    session_id: str
    status: AgentBackendStatus
    output_artifacts: tuple[str, ...] = ()
    reconciliation_metadata: Mapping[str, Any] = field(default_factory=dict)
    external_session_id: str | None = None
    artifact_references: tuple[AgentBackendArtifactReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        normalized_status = _status(self.status, "status")
        object.__setattr__(self, "status", normalized_status)
        if normalized_status not in {
            AgentBackendStatus.COMPLETED,
            AgentBackendStatus.FAILED,
            AgentBackendStatus.CANCELLED,
            AgentBackendStatus.UNKNOWN,
            AgentBackendStatus.RECONCILING,
            AgentBackendStatus.WAITING_APPROVAL,
            # RUNNING and CANCELLING are known, non-terminal states: the
            # adapter has confirmed evidence the backend is still active (or
            # winding down after a cancel request), which is a materially
            # different claim from UNKNOWN ("no confirmed evidence of the
            # outcome at all"). AgentBackendDispatcher.result() keeps the
            # durable effect intent in "dispatching" for either of these
            # (see the trailing `else` branch there) so a later poll can
            # still resolve it normally -- unlike UNKNOWN, which durably
            # commits to "needs explicit reconciliation" and cannot self-heal
            # on a plain retry.
            AgentBackendStatus.RUNNING,
            AgentBackendStatus.CANCELLING,
        }:
            raise ValueError("result status must represent a completed, waiting, or known non-terminal backend outcome")
        object.__setattr__(self, "output_artifacts", _strings(self.output_artifacts, "output_artifacts"))
        if not isinstance(self.reconciliation_metadata, Mapping):
            raise TypeError("reconciliation_metadata must be a mapping")
        object.__setattr__(self, "reconciliation_metadata", dict(self.reconciliation_metadata))
        object.__setattr__(self, "external_session_id", _optional_text(self.external_session_id, "external_session_id"))
        references = self.artifact_references
        if references is None:
            references = ()
        if isinstance(references, (str, bytes)):
            raise TypeError("artifact_references must be a sequence of objects")
        try:
            normalized_references = tuple(
                reference
                if isinstance(reference, AgentBackendArtifactReference)
                else AgentBackendArtifactReference.from_dict(reference)
                for reference in references
            )
        except TypeError as exc:
            raise TypeError("artifact_references must be a sequence of objects") from exc
        object.__setattr__(self, "artifact_references", normalized_references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "output_artifacts": list(self.output_artifacts),
            "reconciliation_metadata": dict(self.reconciliation_metadata),
            "external_session_id": self.external_session_id,
            "artifact_references": [reference.to_dict() for reference in self.artifact_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentBackendResult":
        if not isinstance(value, Mapping):
            raise TypeError("backend result must be an object")
        return cls(**dict(value))


@runtime_checkable
class AgentBackendDiscovery(Protocol):
    """Optional recovery capability for an already-started backend session.

    Discovery is deliberately separate from :class:`AgentBackend`: adapters
    must not claim restart recovery unless they can locate the exact external
    session by the durable client key.  The dispatcher does not call this
    method implicitly; a caller-owned discovery authority may wrap it and
    return an identity-bound receipt.  Without that authority, reconciliation
    remains an explicit UNKNOWN boundary.
    """

    def discover(self, client_session_key: str) -> AgentBackendSession | None:
        ...


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
    "AgentBackendArtifactReference",
    "AgentBackendDiscovery",
    "AgentBackendEvent",
    "AgentBackendIdentity",
    "AgentBackendRequest",
    "AgentBackendResult",
    "AgentBackendScope",
    "AgentBackendSession",
    "AgentBackendStatus",
]
