"""Typed control semantics for model-neutral handoffs.

The directive is deliberately a small value object.  It records what a role
must do with a handoff without becoming an execution policy, scheduler, or
authority implementation.  The envelope keeps it outside the payload so a
future compression transport cannot receive it by accident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any


class PayloadSemantics(str, Enum):
    AUDIT_RESULT = "audit_result"
    ANALYSIS_RESULT = "analysis_result"
    IMPLEMENTATION_INSTRUCTION = "implementation_instruction"
    SOURCE_MATERIAL = "source_material"
    CURRENT_STATE_SNAPSHOT = "current_state_snapshot"
    ROADMAP = "roadmap"
    USER_DECISION = "user_decision"
    EXECUTION_EVIDENCE = "execution_evidence"


class SourceRequirement(str, Enum):
    CURRENT_REPOSITORY = "current_repository"
    CURRENT_PROJECT_SOURCE = "current_project_source"
    ATTACHED_MATERIAL = "attached_material"
    WEB_CURRENT = "web_current"
    PREVIOUS_FINDINGS = "previous_findings"
    ROADMAP = "roadmap"
    EXECUTION_EVIDENCE = "execution_evidence"
    PROVIDED_PAYLOAD = "provided_payload"


class AuthoritySource(str, Enum):
    CURRENT_USER_INSTRUCTION = "current_user_instruction"
    LATEST_USER_CORRECTION = "latest_user_correction"
    SPECIFIC_REQUIREMENT = "specific_requirement"
    CURRENT_REPOSITORY = "current_repository"
    CURRENT_PROJECT_SOURCE = "current_project_source"
    PROVIDED_PAYLOAD = "provided_payload"
    PREVIOUS_FINDINGS = "previous_findings"
    REVIEW_DECISION = "review_decision"
    DOMAIN_SOURCE = "domain_source"
    CORE_SOURCE = "core_source"
    PREVIOUS_CONTEXT = "previous_context"
    GENERAL_DEFAULT = "general_default"


class ContinuationMode(str, Enum):
    FRESH = "fresh"
    CONTINUE = "continue"
    RECHECK = "recheck"
    REAUDIT_AFTER_CHANGE = "reaudit_after_change"
    REANALYZE_AFTER_CORRECTION = "reanalyze_after_correction"
    INTEGRATE_PREVIOUS = "integrate_previous"


DEFAULT_AUTHORITY_PRECEDENCE = (
    AuthoritySource.CURRENT_USER_INSTRUCTION.value,
    AuthoritySource.LATEST_USER_CORRECTION.value,
    AuthoritySource.SPECIFIC_REQUIREMENT.value,
    AuthoritySource.DOMAIN_SOURCE.value,
    AuthoritySource.CORE_SOURCE.value,
    AuthoritySource.PREVIOUS_CONTEXT.value,
    AuthoritySource.GENERAL_DEFAULT.value,
)

_AUTHORITY_ORDER = (
    AuthoritySource.CURRENT_USER_INSTRUCTION.value,
    AuthoritySource.LATEST_USER_CORRECTION.value,
    AuthoritySource.SPECIFIC_REQUIREMENT.value,
    AuthoritySource.CURRENT_PROJECT_SOURCE.value,
    AuthoritySource.CURRENT_REPOSITORY.value,
    AuthoritySource.DOMAIN_SOURCE.value,
    AuthoritySource.CORE_SOURCE.value,
    AuthoritySource.PREVIOUS_CONTEXT.value,
    AuthoritySource.PREVIOUS_FINDINGS.value,
    AuthoritySource.REVIEW_DECISION.value,
    AuthoritySource.PROVIDED_PAYLOAD.value,
    AuthoritySource.GENERAL_DEFAULT.value,
)

_MAX_TEXT = 20_000
_MAX_ITEMS = 32
_MAX_OUTPUT_CONTRACT_BYTES = 20_000


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > _MAX_TEXT:
        raise ValueError(f"{name} is too long")
    return normalized


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{name} has too many items")
    return tuple(_text(item, f"{name}[]") for item in value)


def _enum(value: Any, enum_type: type[Enum], name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, Enum):
        value = value.value
    normalized = _text(value, name)
    allowed = {item.value for item in enum_type}
    if normalized not in allowed:
        raise ValueError(f"unsupported {name}: {normalized}")
    return normalized


def _enum_strings(value: Any, enum_type: type[Enum], name: str) -> tuple[str, ...]:
    raw = _strings(value, name)
    allowed = {item.value for item in enum_type}
    invalid = [item for item in raw if item not in allowed]
    if invalid:
        raise ValueError(f"unsupported {name}: {invalid[0]}")
    return raw


def _output_contract(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("output_contract must be an object")
    normalized = dict(value)
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("output_contract must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > _MAX_OUTPUT_CONTRACT_BYTES:
        raise ValueError("output_contract is too large")
    return normalized


@dataclass(frozen=True)
class HandoffDirective:
    """Optional, typed control metadata for a :class:`HandoffEnvelope`."""

    exclusions: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()
    payload_semantics: str | None = None
    comparison_targets: tuple[str, ...] = ()
    comparison_axes: tuple[str, ...] = ()
    output_contract: Mapping[str, Any] = field(default_factory=dict)
    source_requirements: tuple[str, ...] = ()
    authority_source: str | None = None
    authority_precedence: tuple[str, ...] = ()
    continuation_mode: str = ContinuationMode.FRESH.value
    latest_correction: str | None = None
    prior_interpretation_invalidated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclusions", _strings(self.exclusions, "exclusions"))
        object.__setattr__(self, "focus", _strings(self.focus, "focus"))
        object.__setattr__(
            self,
            "payload_semantics",
            _enum(self.payload_semantics, PayloadSemantics, "payload_semantics", optional=True),
        )
        object.__setattr__(self, "comparison_targets", _strings(self.comparison_targets, "comparison_targets"))
        object.__setattr__(self, "comparison_axes", _strings(self.comparison_axes, "comparison_axes"))
        object.__setattr__(self, "output_contract", _output_contract(self.output_contract))
        object.__setattr__(
            self,
            "source_requirements",
            _enum_strings(self.source_requirements, SourceRequirement, "source_requirements"),
        )
        object.__setattr__(
            self,
            "authority_source",
            _enum(self.authority_source, AuthoritySource, "authority_source", optional=True),
        )
        object.__setattr__(
            self,
            "authority_precedence",
            _enum_strings(self.authority_precedence, AuthoritySource, "authority_precedence"),
        )
        positions = [_AUTHORITY_ORDER.index(item) for item in self.authority_precedence]
        if len(set(positions)) != len(positions) or positions != sorted(positions):
            raise ValueError("authority_precedence must preserve the canonical source order")
        object.__setattr__(
            self,
            "continuation_mode",
            _enum(self.continuation_mode, ContinuationMode, "continuation_mode"),
        )
        if not isinstance(self.prior_interpretation_invalidated, bool):
            raise TypeError("prior_interpretation_invalidated must be a bool")
        correction = self.latest_correction
        if correction is not None:
            correction = _text(correction, "latest_correction")
        object.__setattr__(self, "latest_correction", correction)
        if self.continuation_mode == ContinuationMode.REANALYZE_AFTER_CORRECTION.value:
            if not self.latest_correction:
                raise ValueError("reanalyze_after_correction requires latest_correction")
            if self.authority_source != AuthoritySource.LATEST_USER_CORRECTION.value:
                raise ValueError("reanalyze_after_correction requires latest_user_correction authority")
            if self.prior_interpretation_invalidated is not True:
                raise ValueError("reanalyze_after_correction invalidates the prior interpretation")
        elif self.latest_correction is not None or self.prior_interpretation_invalidated:
            raise ValueError("correction state requires reanalyze_after_correction")

    @property
    def is_empty(self) -> bool:
        return self.to_dict() == HandoffDirective().to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exclusions": list(self.exclusions),
            "focus": list(self.focus),
            "payload_semantics": self.payload_semantics,
            "comparison_targets": list(self.comparison_targets),
            "comparison_axes": list(self.comparison_axes),
            "output_contract": dict(self.output_contract),
            "source_requirements": list(self.source_requirements),
            "authority_source": self.authority_source,
            "authority_precedence": list(self.authority_precedence),
            "continuation_mode": self.continuation_mode,
            "latest_correction": self.latest_correction,
            "prior_interpretation_invalidated": self.prior_interpretation_invalidated,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandoffDirective":
        if not isinstance(value, Mapping):
            raise TypeError("directive must be an object")
        allowed = {
            "exclusions",
            "focus",
            "payload_semantics",
            "comparison_targets",
            "comparison_axes",
            "output_contract",
            "source_requirements",
            "authority_source",
            "authority_precedence",
            "continuation_mode",
            "latest_correction",
            "prior_interpretation_invalidated",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"directive has unsupported fields: {sorted(unknown)[0]}")
        return cls(
            exclusions=value.get("exclusions", ()),
            focus=value.get("focus", ()),
            payload_semantics=value.get("payload_semantics"),
            comparison_targets=value.get("comparison_targets", ()),
            comparison_axes=value.get("comparison_axes", ()),
            output_contract=value.get("output_contract", {}),
            source_requirements=value.get("source_requirements", ()),
            authority_source=value.get("authority_source"),
            authority_precedence=value.get("authority_precedence", ()),
            continuation_mode=value.get("continuation_mode", ContinuationMode.FRESH.value),
            latest_correction=value.get("latest_correction"),
            prior_interpretation_invalidated=value.get("prior_interpretation_invalidated", False),
        )


__all__ = [
    "AuthoritySource",
    "ContinuationMode",
    "DEFAULT_AUTHORITY_PRECEDENCE",
    "HandoffDirective",
    "PayloadSemantics",
    "SourceRequirement",
]
