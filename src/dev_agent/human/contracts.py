"""Sanitized, bounded Human request/response records.

These records are deliberately independent of Codex, Discord, or any other
transport.  They describe an exact Human authority request and its one-shot
response correlation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Mapping
from uuid import uuid4

from ..security.audit import AuditRecorder


_MAX_ID = 256
_MAX_TEXT = 2048
_MAX_DECISION = 256
_MAX_ANSWERS = 32


def _text(value: Any, name: str, *, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return normalized


def _identifier(value: Any, name: str) -> str:
    return _text(value, name, maximum=_MAX_ID)


def _timestamp(value: Any, name: str) -> str:
    normalized = _text(value, name, maximum=64)
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    return normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    safe = AuditRecorder.sanitize_payload(dict(value))
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > AuditRecorder.MAX_PAYLOAD_BYTES:
        raise ValueError(f"{name} exceeds the bounded audit payload")
    return safe


@dataclass(frozen=True)
class HumanRequest:
    request_id: str = field(default_factory=lambda: str(uuid4()))
    root_id: str = ""
    task_id: str = ""
    attempt_id: str = ""
    reason: str = ""
    question: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    required_authority: str = "HUMAN_REQUIRED"
    allowed_answers: tuple[str, ...] = ()
    response_shape: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "root_id", _identifier(self.root_id, "root_id"))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "attempt_id", _identifier(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(self, "question", _text(self.question, "question"))
        authority = _text(self.required_authority, "required_authority", maximum=_MAX_ID)
        if authority != "HUMAN_REQUIRED":
            raise ValueError("HumanRequest required_authority must be HUMAN_REQUIRED")
        object.__setattr__(self, "required_authority", authority)
        if not isinstance(self.allowed_answers, (tuple, list)):
            raise ValueError("allowed_answers must be a sequence")
        if len(self.allowed_answers) > _MAX_ANSWERS:
            raise ValueError("allowed_answers has too many entries")
        answers: list[str] = []
        for answer in self.allowed_answers:
            normalized = _text(answer, "allowed_answer", maximum=_MAX_ID)
            if normalized not in answers:
                answers.append(normalized)
        object.__setattr__(self, "allowed_answers", tuple(answers))
        object.__setattr__(self, "context", _safe_mapping(self.context, "context"))
        object.__setattr__(self, "response_shape", _safe_mapping(self.response_shape, "response_shape"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "root_id": self.root_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "reason": self.reason,
            "question": self.question,
            "context": dict(self.context),
            "required_authority": self.required_authority,
            "allowed_answers": list(self.allowed_answers),
            "response_shape": dict(self.response_shape),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanRequest":
        if not isinstance(payload, Mapping):
            raise ValueError("human request must be an object")
        return cls(**dict(payload))


@dataclass(frozen=True)
class HumanResponse:
    request_id: str = ""
    responder: str = ""
    response: Any = field(default_factory=dict)
    decision: str = ""
    received_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "responder", _text(self.responder, "responder", maximum=_MAX_ID))
        object.__setattr__(self, "decision", _text(self.decision, "decision", maximum=_MAX_DECISION))
        if not isinstance(self.response, (Mapping, list, tuple, str, int, float, bool)) and self.response is not None:
            raise ValueError("response must be a bounded JSON value")
        safe = AuditRecorder.sanitize_payload({"response": self.response})["response"]
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > AuditRecorder.MAX_PAYLOAD_BYTES:
            raise ValueError("response exceeds the bounded audit payload")
        object.__setattr__(self, "response", safe)
        object.__setattr__(self, "received_at", _timestamp(self.received_at, "received_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "responder": self.responder,
            "response": self.response,
            "decision": self.decision,
            "received_at": self.received_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanResponse":
        if not isinstance(payload, Mapping):
            raise ValueError("human response must be an object")
        return cls(**dict(payload))


__all__ = ["HumanRequest", "HumanResponse"]
