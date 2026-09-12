"""Typed, model-neutral handoff envelopes.

Handoff is an information-transfer contract, not a scheduler, authority
layer, or model-specific execution API.  Control fields stay separate from
the payload so a later compression client can receive payload only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any
from uuid import uuid4

from .directive import HandoffDirective
from .references import ExternalTextReference


class HandoffKind(str, Enum):
    ANALYSIS_RESULT = "analysis_result"
    IMPLEMENTATION_INSTRUCTION = "implementation_instruction"
    REVIEW_REQUEST = "review_request"
    CURRENT_STATE_REQUEST = "current_state_request"
    AUDIT_RESULT = "audit_result"
    EXECUTION_RESULT = "execution_result"
    ROADMAP_COMPARISON = "roadmap_comparison"
    REPAIR_REQUEST = "repair_request"
    HUMAN_DECISION_REQUIRED = "human_decision_required"


class HandoffRole(str, Enum):
    HUMAN = "human"
    PLANNER = "planner"
    REVIEWER = "reviewer"
    EXECUTOR = "executor"
    COMPRESSION = "compression"


class PayloadMode(str, Enum):
    ORIGINAL = "original"
    COMPRESSED = "compressed"
    REFERENCE = "reference"


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_MAX_TEXT = 100_000


def _text(value: Any, name: str, *, max_length: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError(f"{name} is too long")
    return normalized


def _identifier(value: Any, name: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    normalized = _text(value, name, max_length=256)
    if _IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a structural identifier")
    return normalized


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    normalized: list[str] = []
    for item in value:
        normalized.append(_text(item, f"{name}[]", max_length=20_000))
    return tuple(normalized)


def _json_value(value: Any, name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable") from exc
    return value


def _mapping(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    normalized = dict(value)
    _json_value(normalized, name)
    return normalized


@dataclass(frozen=True)
class HandoffEnvelope:
    """A model-neutral handoff with explicit control/payload separation."""

    kind: str
    subject: str
    instruction: str
    source_role: str
    target_role: str
    handoff_id: str = field(default_factory=lambda: str(uuid4()))
    conditions: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    directive: HandoffDirective | Mapping[str, Any] = field(default_factory=HandoffDirective)
    payload: Any = None
    payload_mode: str = PayloadMode.ORIGINAL.value
    payload_reference: Mapping[str, Any] | None = None
    original_reference: Mapping[str, Any] | None = None
    original_sha256: str | None = None
    compression_profile: str | None = None
    compression_prompt_version: str | None = None
    compression_model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "handoff_id", _identifier(self.handoff_id, "handoff_id"))
        object.__setattr__(self, "kind", _identifier(self.kind, "kind"))
        object.__setattr__(self, "subject", _text(self.subject, "subject"))
        object.__setattr__(self, "instruction", _text(self.instruction, "instruction"))
        object.__setattr__(self, "source_role", _identifier(self.source_role, "source_role"))
        object.__setattr__(self, "target_role", _identifier(self.target_role, "target_role"))
        object.__setattr__(self, "conditions", _strings(self.conditions, "conditions"))
        object.__setattr__(self, "cautions", _strings(self.cautions, "cautions"))
        object.__setattr__(self, "requirements", _strings(self.requirements, "requirements"))
        directive = self.directive
        if isinstance(directive, Mapping):
            directive = HandoffDirective.from_dict(directive)
        if not isinstance(directive, HandoffDirective):
            raise TypeError("directive must be a HandoffDirective or object")
        object.__setattr__(self, "directive", directive)

        mode = _identifier(self.payload_mode, "payload_mode")
        if mode not in {item.value for item in PayloadMode}:
            raise ValueError(f"unsupported payload_mode: {mode}")
        object.__setattr__(self, "payload_mode", mode)
        _json_value(self.payload, "payload")
        payload_reference = _mapping(self.payload_reference, "payload_reference")
        if mode == PayloadMode.REFERENCE.value and not payload_reference:
            raise ValueError("reference payload_mode requires payload_reference")
        if payload_reference is not None and payload_reference.get("type") == "external_text":
            payload_reference = ExternalTextReference.from_dict(payload_reference).to_dict()
        object.__setattr__(self, "payload_reference", payload_reference)

        object.__setattr__(self, "original_reference", _mapping(self.original_reference, "original_reference"))
        for name in (
            "original_sha256",
            "compression_profile",
            "compression_prompt_version",
            "compression_model",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name, max_length=512))
        metadata = _mapping(self.metadata, "metadata")
        object.__setattr__(self, "metadata", metadata or {})

    def control_payload(self) -> dict[str, Any]:
        """Return only control data; safe compression callers must not pass it."""

        value = {
            "subject": self.subject,
            "instruction": self.instruction,
            "conditions": list(self.conditions),
            "cautions": list(self.cautions),
            "requirements": list(self.requirements),
            "source_role": self.source_role,
            "target_role": self.target_role,
        }
        if not self.directive.is_empty:
            value["directive"] = self.directive.to_dict()
        return value

    def payload_for_compression(self) -> Any:
        """Return payload only, never instructions or authority constraints."""

        return self.payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "kind": self.kind,
            "subject": self.subject,
            "instruction": self.instruction,
            "conditions": list(self.conditions),
            "cautions": list(self.cautions),
            "requirements": list(self.requirements),
            "directive": self.directive.to_dict(),
            "payload": self.payload,
            "payload_mode": self.payload_mode,
            "payload_reference": dict(self.payload_reference) if self.payload_reference is not None else None,
            "original_reference": dict(self.original_reference) if self.original_reference is not None else None,
            "original_sha256": self.original_sha256,
            "compression_profile": self.compression_profile,
            "compression_prompt_version": self.compression_prompt_version,
            "compression_model": self.compression_model,
            "source_role": self.source_role,
            "target_role": self.target_role,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandoffEnvelope":
        if not isinstance(value, Mapping):
            raise TypeError("handoff must be an object")
        required = ("kind", "subject", "instruction", "source_role", "target_role")
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"handoff missing required fields: {', '.join(missing)}")
        return cls(
            handoff_id=value.get("handoff_id", str(uuid4())),
            kind=value["kind"],
            subject=value["subject"],
            instruction=value["instruction"],
            source_role=value["source_role"],
            target_role=value["target_role"],
            conditions=value.get("conditions", ()),
            cautions=value.get("cautions", ()),
            requirements=value.get("requirements", ()),
            directive=value.get("directive", {}),
            payload=value.get("payload"),
            payload_mode=value.get("payload_mode", PayloadMode.ORIGINAL.value),
            payload_reference=value.get("payload_reference"),
            original_reference=value.get("original_reference"),
            original_sha256=value.get("original_sha256"),
            compression_profile=value.get("compression_profile"),
            compression_prompt_version=value.get("compression_prompt_version"),
            compression_model=value.get("compression_model"),
            metadata=value.get("metadata", {}),
        )


__all__ = ["ExternalTextReference", "HandoffDirective", "HandoffEnvelope", "HandoffKind", "HandoffRole", "PayloadMode"]
