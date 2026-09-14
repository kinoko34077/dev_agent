"""Durable process-coordination value objects.

The coordination plane carries process presence, immutable handoff artifacts,
and mailbox delivery state.  It deliberately does not own task scheduling,
provider routing, approval, or process creation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from .protocol_helpers import (
    CoordinationValidationError,
    ensure_json_safe,
    ensure_secret_free,
    validate_identifier,
    validate_relative_path,
    validate_string_sequence,
    validate_text,
    validate_timestamp,
)


class CoordinationConflict(RuntimeError):
    """Raised when a stale generation or conflicting idempotency is used."""


class PeerStatus(str, Enum):
    STARTING = "STARTING"
    READY = "READY"
    DRAINING = "DRAINING"
    RESTARTING = "RESTARTING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    DEGRADED = "DEGRADED"


class MessageKind(str, Enum):
    NOTE = "NOTE"
    HANDOFF = "HANDOFF"
    ARTIFACT_READY = "ARTIFACT_READY"
    WORK_REQUEST = "WORK_REQUEST"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    CONTROL_REQUEST = "CONTROL_REQUEST"
    CHECKPOINT = "CHECKPOINT"


class ControlAction(str, Enum):
    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"
    DRAIN = "DRAIN"
    UPDATE = "UPDATE"
    ROLLBACK = "ROLLBACK"


class MailboxStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    ACKED = "ACKED"
    EXPIRED = "EXPIRED"


class GuardianActionStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_PROTOCOL_BYTES = 256 * 1024
_MAX_ARTIFACT_REFS = 64


def _enum(value: Any, enum_type: type[Enum], name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CoordinationValidationError(f"{name} has an unsupported value") from exc


def _optional_text(value: Any, name: str, *, max_chars: int = 4_096) -> str | None:
    if value is None:
        return None
    return validate_text(value, name, max_chars=max_chars)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoordinationValidationError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoordinationValidationError(f"{name} must be a non-negative integer")
    return value


def _artifact_refs(value: Any, name: str = "artifact_refs") -> tuple["ArtifactReference", ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CoordinationValidationError(f"{name} must be a sequence")
    if len(value) > _MAX_ARTIFACT_REFS:
        raise CoordinationValidationError(f"{name} contains too many references")
    result: list[ArtifactReference] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        reference = item if isinstance(item, ArtifactReference) else ArtifactReference.from_dict(item)
        identity = reference.sha256 + ":" + reference.path
        if identity not in seen:
            seen.add(identity)
            result.append(reference)
    return tuple(result)


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str
    size_bytes: int
    kind: str
    revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", validate_relative_path(self.path, "path"))
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise CoordinationValidationError("sha256 must be a 64-character hexadecimal digest")
        object.__setattr__(self, "sha256", self.sha256.lower())
        object.__setattr__(self, "size_bytes", _nonnegative_int(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "kind", validate_identifier(self.kind, "kind"))
        object.__setattr__(self, "revision", _optional_text(self.revision, "revision", max_chars=512))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactReference":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("artifact reference must be an object")
        return cls(
            path=value.get("path"),
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
            kind=value.get("kind"),
            revision=value.get("revision"),
        )


@dataclass(frozen=True)
class PeerRecord:
    role: str
    instance_id: str
    generation: int
    pid: int | None
    revision: str
    started_at: str
    heartbeat_at: str
    lease_until: str
    status: PeerStatus = PeerStatus.STARTING
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", validate_identifier(self.role, "role"))
        object.__setattr__(self, "instance_id", validate_identifier(self.instance_id, "instance_id"))
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        if self.pid is not None:
            object.__setattr__(self, "pid", _nonnegative_int(self.pid, "pid"))
        object.__setattr__(self, "revision", validate_text(self.revision, "revision", max_chars=512))
        object.__setattr__(self, "started_at", validate_timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "heartbeat_at", validate_timestamp(self.heartbeat_at, "heartbeat_at"))
        object.__setattr__(self, "lease_until", validate_timestamp(self.lease_until, "lease_until"))
        object.__setattr__(self, "status", _enum(self.status, PeerStatus, "status"))
        object.__setattr__(
            self,
            "capabilities",
            validate_string_sequence(self.capabilities, "capabilities", item_max_chars=512),
        )
        ensure_secret_free(self.to_dict(), "peer")
        ensure_json_safe(self.to_dict(), "peer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "pid": self.pid,
            "revision": self.revision,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "lease_until": self.lease_until,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PeerRecord":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("peer must be an object")
        return cls(
            role=value.get("role"),
            instance_id=value.get("instance_id"),
            generation=value.get("generation"),
            pid=value.get("pid"),
            revision=value.get("revision"),
            started_at=value.get("started_at"),
            heartbeat_at=value.get("heartbeat_at"),
            lease_until=value.get("lease_until"),
            status=value.get("status"),
            capabilities=value.get("capabilities", ()),
        )


@dataclass(frozen=True)
class MailboxMessage:
    message_id: str
    sender_role: str
    sender_instance_id: str
    sender_generation: int
    recipient_role: str
    kind: MessageKind
    subject: str
    artifact_refs: tuple[ArtifactReference, ...] = ()
    correlation_id: str | None = None
    requires_ack: bool = True
    idempotency_key: str = ""
    created_at: str = ""
    expires_at: str | None = None
    status: MailboxStatus = MailboxStatus.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None
    claim_lease_until: str | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", validate_identifier(self.message_id, "message_id"))
        object.__setattr__(self, "sender_role", validate_identifier(self.sender_role, "sender_role"))
        object.__setattr__(self, "sender_instance_id", validate_identifier(self.sender_instance_id, "sender_instance_id"))
        object.__setattr__(self, "sender_generation", _positive_int(self.sender_generation, "sender_generation"))
        object.__setattr__(self, "recipient_role", validate_identifier(self.recipient_role, "recipient_role"))
        object.__setattr__(self, "kind", _enum(self.kind, MessageKind, "kind"))
        object.__setattr__(self, "subject", validate_text(self.subject, "subject"))
        object.__setattr__(self, "artifact_refs", _artifact_refs(self.artifact_refs))
        object.__setattr__(self, "correlation_id", _optional_text(self.correlation_id, "correlation_id", max_chars=512))
        if not isinstance(self.requires_ack, bool):
            raise CoordinationValidationError("requires_ack must be a boolean")
        object.__setattr__(self, "idempotency_key", validate_identifier(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", _optional_text(self.expires_at, "expires_at", max_chars=80))
        if self.expires_at is not None:
            validate_timestamp(self.expires_at, "expires_at")
        object.__setattr__(self, "status", _enum(self.status, MailboxStatus, "status"))
        object.__setattr__(self, "claimed_by", _optional_text(self.claimed_by, "claimed_by", max_chars=256))
        object.__setattr__(self, "claimed_at", _optional_text(self.claimed_at, "claimed_at", max_chars=80))
        if self.claimed_at is not None:
            validate_timestamp(self.claimed_at, "claimed_at")
        object.__setattr__(self, "claim_lease_until", _optional_text(self.claim_lease_until, "claim_lease_until", max_chars=80))
        if self.claim_lease_until is not None:
            validate_timestamp(self.claim_lease_until, "claim_lease_until")
        object.__setattr__(self, "attempt_count", _nonnegative_int(self.attempt_count, "attempt_count"))
        ensure_secret_free(self.to_dict(), "mailbox message")
        encoded = ensure_json_safe(self.to_dict(), "mailbox message")
        if len(str(encoded).encode("utf-8")) > _MAX_PROTOCOL_BYTES:
            raise CoordinationValidationError("mailbox message exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_role": self.sender_role,
            "sender_instance_id": self.sender_instance_id,
            "sender_generation": self.sender_generation,
            "recipient_role": self.recipient_role,
            "kind": self.kind.value,
            "subject": self.subject,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "correlation_id": self.correlation_id,
            "requires_ack": self.requires_ack,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "claim_lease_until": self.claim_lease_until,
            "attempt_count": self.attempt_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MailboxMessage":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("mailbox message must be an object")
        return cls(
            message_id=value.get("message_id"),
            sender_role=value.get("sender_role"),
            sender_instance_id=value.get("sender_instance_id"),
            sender_generation=value.get("sender_generation"),
            recipient_role=value.get("recipient_role"),
            kind=value.get("kind"),
            subject=value.get("subject"),
            artifact_refs=value.get("artifact_refs", ()),
            correlation_id=value.get("correlation_id"),
            requires_ack=value.get("requires_ack", True),
            idempotency_key=value.get("idempotency_key"),
            created_at=value.get("created_at"),
            expires_at=value.get("expires_at"),
            status=value.get("status", MailboxStatus.PENDING),
            claimed_by=value.get("claimed_by"),
            claimed_at=value.get("claimed_at"),
            claim_lease_until=value.get("claim_lease_until"),
            attempt_count=value.get("attempt_count", 0),
        )


@dataclass(frozen=True)
class ControlRequest:
    """A generation-fenced request for a future deterministic Guardian.

    This value is only an intent record.  The coordination plane can persist
    and deliver it, but it never starts, stops, or restarts a process.
    """

    request_id: str
    sender_role: str
    sender_instance_id: str
    sender_generation: int
    target_role: str
    target_generation: int
    action: ControlAction
    reason: str
    created_at: str
    idempotency_key: str
    desired_revision: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_identifier(self.request_id, "request_id"))
        object.__setattr__(self, "sender_role", validate_identifier(self.sender_role, "sender_role"))
        object.__setattr__(self, "sender_instance_id", validate_identifier(self.sender_instance_id, "sender_instance_id"))
        object.__setattr__(self, "sender_generation", _positive_int(self.sender_generation, "sender_generation"))
        object.__setattr__(self, "target_role", validate_identifier(self.target_role, "target_role"))
        object.__setattr__(self, "target_generation", _positive_int(self.target_generation, "target_generation"))
        object.__setattr__(self, "action", _enum(self.action, ControlAction, "action"))
        object.__setattr__(self, "reason", validate_text(self.reason, "reason", max_chars=4_096))
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "idempotency_key", validate_identifier(self.idempotency_key, "idempotency_key"))
        desired_revision = _optional_text(self.desired_revision, "desired_revision", max_chars=512)
        expires_at = _optional_text(self.expires_at, "expires_at", max_chars=80)
        if expires_at is not None:
            validate_timestamp(expires_at, "expires_at")
        object.__setattr__(self, "desired_revision", desired_revision)
        object.__setattr__(self, "expires_at", expires_at)
        ensure_secret_free(self.to_dict(), "control request")
        encoded = ensure_json_safe(self.to_dict(), "control request")
        if len(str(encoded).encode("utf-8")) > _MAX_PROTOCOL_BYTES:
            raise CoordinationValidationError("control request exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "sender_role": self.sender_role,
            "sender_instance_id": self.sender_instance_id,
            "sender_generation": self.sender_generation,
            "target_role": self.target_role,
            "target_generation": self.target_generation,
            "action": self.action.value,
            "reason": self.reason,
            "created_at": self.created_at,
            "idempotency_key": self.idempotency_key,
            "desired_revision": self.desired_revision,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlRequest":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("control request must be an object")
        return cls(
            request_id=value.get("request_id"),
            sender_role=value.get("sender_role"),
            sender_instance_id=value.get("sender_instance_id"),
            sender_generation=value.get("sender_generation"),
            target_role=value.get("target_role"),
            target_generation=value.get("target_generation"),
            action=value.get("action"),
            reason=value.get("reason"),
            created_at=value.get("created_at"),
            idempotency_key=value.get("idempotency_key"),
            desired_revision=value.get("desired_revision"),
            expires_at=value.get("expires_at"),
        )


@dataclass(frozen=True)
class GuardianActionRecord:
    """Durable journal entry for one generation-fenced control intent."""

    request_id: str
    idempotency_key: str
    request_digest: str
    sender_role: str
    sender_instance_id: str
    sender_generation: int
    target_role: str
    target_generation: int
    action: ControlAction
    status: GuardianActionStatus
    decision: str
    decision_reason: str
    created_at: str
    updated_at: str
    desired_revision: str | None = None
    result_code: str | None = None
    reconciliation_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_identifier(self.request_id, "request_id"))
        object.__setattr__(self, "idempotency_key", validate_identifier(self.idempotency_key, "idempotency_key"))
        if not isinstance(self.request_digest, str) or _SHA256.fullmatch(self.request_digest) is None:
            raise CoordinationValidationError("request_digest must be a 64-character hexadecimal digest")
        object.__setattr__(self, "request_digest", self.request_digest.lower())
        object.__setattr__(self, "sender_role", validate_identifier(self.sender_role, "sender_role"))
        object.__setattr__(self, "sender_instance_id", validate_identifier(self.sender_instance_id, "sender_instance_id"))
        object.__setattr__(self, "sender_generation", _positive_int(self.sender_generation, "sender_generation"))
        object.__setattr__(self, "target_role", validate_identifier(self.target_role, "target_role"))
        object.__setattr__(self, "target_generation", _positive_int(self.target_generation, "target_generation"))
        object.__setattr__(self, "action", _enum(self.action, ControlAction, "action"))
        object.__setattr__(self, "status", _enum(self.status, GuardianActionStatus, "status"))
        object.__setattr__(self, "decision", validate_identifier(self.decision, "decision"))
        object.__setattr__(self, "decision_reason", validate_text(self.decision_reason, "decision_reason", max_chars=4_096))
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", validate_timestamp(self.updated_at, "updated_at"))
        object.__setattr__(self, "desired_revision", _optional_text(self.desired_revision, "desired_revision", max_chars=512))
        result_code = _optional_text(self.result_code, "result_code", max_chars=256)
        if result_code is not None:
            result_code = validate_identifier(result_code, "result_code")
        object.__setattr__(self, "result_code", result_code)
        if not isinstance(self.reconciliation_required, bool):
            raise CoordinationValidationError("reconciliation_required must be a boolean")
        ensure_secret_free(self.to_dict(), "guardian action")
        encoded = ensure_json_safe(self.to_dict(), "guardian action")
        if len(str(encoded).encode("utf-8")) > _MAX_PROTOCOL_BYTES:
            raise CoordinationValidationError("guardian action exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
            "sender_role": self.sender_role,
            "sender_instance_id": self.sender_instance_id,
            "sender_generation": self.sender_generation,
            "target_role": self.target_role,
            "target_generation": self.target_generation,
            "action": self.action.value,
            "status": self.status.value,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "desired_revision": self.desired_revision,
            "result_code": self.result_code,
            "reconciliation_required": self.reconciliation_required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GuardianActionRecord":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("guardian action must be an object")
        return cls(
            request_id=value.get("request_id"),
            idempotency_key=value.get("idempotency_key"),
            request_digest=value.get("request_digest"),
            sender_role=value.get("sender_role"),
            sender_instance_id=value.get("sender_instance_id"),
            sender_generation=value.get("sender_generation"),
            target_role=value.get("target_role"),
            target_generation=value.get("target_generation"),
            action=value.get("action"),
            status=value.get("status"),
            decision=value.get("decision"),
            decision_reason=value.get("decision_reason"),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            desired_revision=value.get("desired_revision"),
            result_code=value.get("result_code"),
            reconciliation_required=value.get("reconciliation_required", False),
        )


@dataclass(frozen=True)
class HandoffNote:
    from_role: str
    to_role: str
    revision: str
    run_id: str
    task_id: str
    correlation_id: str
    completed: tuple[str, ...]
    current_state: str
    pending: tuple[str, ...]
    blockers: tuple[str, ...]
    read_these: tuple[ArtifactReference, ...]
    next_action: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_role", validate_identifier(self.from_role, "from_role"))
        object.__setattr__(self, "to_role", validate_identifier(self.to_role, "to_role"))
        object.__setattr__(self, "revision", validate_text(self.revision, "revision", max_chars=512))
        object.__setattr__(self, "run_id", validate_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "task_id", validate_identifier(self.task_id, "task_id"))
        object.__setattr__(self, "correlation_id", validate_identifier(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "completed", validate_string_sequence(self.completed, "completed"))
        object.__setattr__(self, "current_state", validate_text(self.current_state, "current_state"))
        object.__setattr__(self, "pending", validate_string_sequence(self.pending, "pending"))
        object.__setattr__(self, "blockers", validate_string_sequence(self.blockers, "blockers"))
        object.__setattr__(self, "read_these", _artifact_refs(self.read_these, "read_these"))
        object.__setattr__(self, "next_action", validate_text(self.next_action, "next_action"))
        ensure_secret_free(self.to_dict(), "handoff note")
        encoded = ensure_json_safe(self.to_dict(), "handoff note")
        if len(str(encoded).encode("utf-8")) > _MAX_PROTOCOL_BYTES:
            raise CoordinationValidationError("handoff note exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_role": self.from_role,
            "to_role": self.to_role,
            "revision": self.revision,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "completed": list(self.completed),
            "current_state": self.current_state,
            "pending": list(self.pending),
            "blockers": list(self.blockers),
            "read_these": [item.to_dict() for item in self.read_these],
            "next_action": self.next_action,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandoffNote":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("handoff note must be an object")
        return cls(
            from_role=value.get("from_role"),
            to_role=value.get("to_role"),
            revision=value.get("revision"),
            run_id=value.get("run_id"),
            task_id=value.get("task_id"),
            correlation_id=value.get("correlation_id"),
            completed=value.get("completed", ()),
            current_state=value.get("current_state"),
            pending=value.get("pending", ()),
            blockers=value.get("blockers", ()),
            read_these=value.get("read_these", ()),
            next_action=value.get("next_action"),
        )


__all__ = [
    "ArtifactReference",
    "CoordinationConflict",
    "CoordinationValidationError",
    "ControlAction",
    "ControlRequest",
    "GuardianActionRecord",
    "GuardianActionStatus",
    "HandoffNote",
    "MailboxMessage",
    "MailboxStatus",
    "MessageKind",
    "PeerRecord",
    "PeerStatus",
]
