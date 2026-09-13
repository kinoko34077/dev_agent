"""Small schema-only contracts for a future MCP adapter.

This module deliberately does not implement MCP transport or execution.  It
only records the bounded operation names and the input/result envelopes that
a later adapter must translate to existing Commander/Supervisor APIs.  The
existing authorities remain responsible for approval, budget, privacy,
recovery, reconciliation, and integration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_REQUEST_ID = 128
_MAX_ERROR_CODE = 128
_MAX_REQUEST_BYTES = 16 * 1024
_MAX_RESULT_BYTES = 64 * 1024


def _token(value: Any, name: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty structural token")
    normalized = value.strip()
    if len(normalized) > maximum or _TOKEN.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a bounded structural token")
    return normalized


def _reference(value: Any, name: str, *, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty reference")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{name} contains unsafe whitespace or control characters")
    return normalized


def _json_bytes(value: Any, name: str, *, maximum: int) -> int:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable") from exc
    if len(encoded) > maximum:
        raise ValueError(f"{name} exceeds the {maximum}-byte limit")
    return len(encoded)


class McpToolName(str, Enum):
    STATUS = "status"
    PLAN_PROPOSE = "plan_propose"
    PLAN_VALIDATE = "plan_validate"
    PLAN_APPLY = "plan_apply"
    RUN = "run"
    REVIEW = "review"
    REWORK = "rework"
    INTEGRATE = "integrate"
    RESUME = "resume"
    ARTIFACT_SUMMARY = "artifact_summary"


class McpApprovalRequirement(str, Enum):
    """Existing authority boundary required before an adapter may act."""

    NONE = "none"
    EXISTING_AUTHORITY = "existing_authority"
    EXPLICIT_OPERATOR = "explicit_operator"
    HUMAN_DECISION = "human_decision"


class UnknownOutcomePolicy(str, Enum):
    """What a transport/adapter failure must mean to the caller."""

    NO_EXTERNAL_EFFECT = "no_external_effect"
    RECONCILE = "reconcile"
    RETURN_UNKNOWN = "return_unknown"


class McpResultStatus(str, Enum):
    OK = "ok"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"
    HUMAN_DECISION_REQUIRED = "human_decision_required"


@dataclass(frozen=True)
class McpToolSpec:
    """Static limits and authority declarations for one future tool."""

    name: McpToolName
    timeout_seconds: int
    max_request_bytes: int = _MAX_REQUEST_BYTES
    max_result_bytes: int = _MAX_RESULT_BYTES
    approval: McpApprovalRequirement = McpApprovalRequirement.NONE
    unknown_outcome: UnknownOutcomePolicy = UnknownOutcomePolicy.NO_EXTERNAL_EFFECT
    requires_durable_intent: bool = False
    result_reference_first: bool = True
    redaction_policy: str = "central_audit"
    adapter_connected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, McpToolName):
            raise TypeError("name must be a McpToolName")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 0 < self.timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")
        for value, field_name in (
            (self.max_request_bytes, "max_request_bytes"),
            (self.max_result_bytes, "max_result_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if not isinstance(self.approval, McpApprovalRequirement):
            raise TypeError("approval must be an McpApprovalRequirement")
        if not isinstance(self.unknown_outcome, UnknownOutcomePolicy):
            raise TypeError("unknown_outcome must be an UnknownOutcomePolicy")
        if not isinstance(self.requires_durable_intent, bool):
            raise TypeError("requires_durable_intent must be a boolean")
        if self.unknown_outcome is UnknownOutcomePolicy.RECONCILE and not self.requires_durable_intent:
            raise ValueError("reconcile tools must require durable intent")
        if not isinstance(self.result_reference_first, bool):
            raise TypeError("result_reference_first must be a boolean")
        if self.redaction_policy != "central_audit":
            raise ValueError("MCP results must use the central audit redaction policy")
        if self.adapter_connected is not False:
            raise ValueError("the schema-only MCP contract cannot mark an adapter connected")


def _spec(
    name: McpToolName,
    *,
    timeout_seconds: int,
    approval: McpApprovalRequirement,
    unknown_outcome: UnknownOutcomePolicy = UnknownOutcomePolicy.NO_EXTERNAL_EFFECT,
    requires_durable_intent: bool = False,
) -> McpToolSpec:
    return McpToolSpec(
        name=name,
        timeout_seconds=timeout_seconds,
        approval=approval,
        unknown_outcome=unknown_outcome,
        requires_durable_intent=requires_durable_intent,
    )


MCP_TOOL_SPECS: Mapping[McpToolName, McpToolSpec] = MappingProxyType(
    {
        McpToolName.STATUS: _spec(
            McpToolName.STATUS,
            timeout_seconds=30,
            approval=McpApprovalRequirement.NONE,
        ),
        McpToolName.PLAN_PROPOSE: _spec(
            McpToolName.PLAN_PROPOSE,
            timeout_seconds=60,
            approval=McpApprovalRequirement.NONE,
        ),
        McpToolName.PLAN_VALIDATE: _spec(
            McpToolName.PLAN_VALIDATE,
            timeout_seconds=60,
            approval=McpApprovalRequirement.NONE,
        ),
        McpToolName.PLAN_APPLY: _spec(
            McpToolName.PLAN_APPLY,
            timeout_seconds=120,
            approval=McpApprovalRequirement.EXISTING_AUTHORITY,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.RUN: _spec(
            McpToolName.RUN,
            timeout_seconds=900,
            approval=McpApprovalRequirement.EXPLICIT_OPERATOR,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.REVIEW: _spec(
            McpToolName.REVIEW,
            timeout_seconds=60,
            approval=McpApprovalRequirement.EXISTING_AUTHORITY,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.REWORK: _spec(
            McpToolName.REWORK,
            timeout_seconds=120,
            approval=McpApprovalRequirement.EXISTING_AUTHORITY,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.INTEGRATE: _spec(
            McpToolName.INTEGRATE,
            timeout_seconds=300,
            approval=McpApprovalRequirement.EXISTING_AUTHORITY,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.RESUME: _spec(
            McpToolName.RESUME,
            timeout_seconds=300,
            approval=McpApprovalRequirement.EXISTING_AUTHORITY,
            unknown_outcome=UnknownOutcomePolicy.RECONCILE,
            requires_durable_intent=True,
        ),
        McpToolName.ARTIFACT_SUMMARY: _spec(
            McpToolName.ARTIFACT_SUMMARY,
            timeout_seconds=30,
            approval=McpApprovalRequirement.NONE,
        ),
    }
)


@dataclass(frozen=True)
class McpToolRequest:
    """Bounded JSON request; it carries no transport or authority semantics."""

    request_id: str
    tool: McpToolName
    arguments: Mapping[str, Any]
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        request_id = _token(self.request_id, "request_id", maximum=_MAX_REQUEST_ID)
        try:
            tool = self.tool if isinstance(self.tool, McpToolName) else McpToolName(self.tool)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool must be a known MCP tool") from exc
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a JSON object")
        arguments = dict(self.arguments)
        _json_bytes(arguments, "arguments", maximum=MCP_TOOL_SPECS[tool].max_request_bytes)
        timeout = self.timeout_seconds
        if timeout is None:
            timeout = float(MCP_TOOL_SPECS[tool].timeout_seconds)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if float(timeout) > MCP_TOOL_SPECS[tool].timeout_seconds:
            raise ValueError("timeout_seconds exceeds tool timeout")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "timeout_seconds", float(timeout))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool": self.tool.value,
            "arguments": dict(self.arguments),
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "McpToolRequest":
        if not isinstance(value, Mapping):
            raise ValueError("MCP request must be an object")
        allowed = {"request_id", "tool", "arguments", "timeout_seconds"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown request field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid MCP request: {exc}") from exc


@dataclass(frozen=True)
class McpToolResult:
    """Bounded reference-first result preserving UNKNOWN as a real outcome."""

    request_id: str
    tool: McpToolName
    status: McpResultStatus
    data: Mapping[str, Any] | None = None
    error_code: str | None = None
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        request_id = _token(self.request_id, "request_id", maximum=_MAX_REQUEST_ID)
        try:
            tool = self.tool if isinstance(self.tool, McpToolName) else McpToolName(self.tool)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool must be a known MCP tool") from exc
        try:
            status = self.status if isinstance(self.status, McpResultStatus) else McpResultStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("status must be a known MCP result status") from exc
        data = {} if self.data is None else self.data
        if not isinstance(data, Mapping):
            raise TypeError("data must be a JSON object")
        data = dict(data)
        _json_bytes(data, "data", maximum=MCP_TOOL_SPECS[tool].max_result_bytes)
        error_code = self.error_code
        if error_code is not None:
            error_code = _token(error_code, "error_code", maximum=_MAX_ERROR_CODE)
        if isinstance(self.artifact_refs, (str, bytes)):
            raise TypeError("artifact_refs must be a sequence of references")
        try:
            artifact_refs = tuple(_reference(item, "artifact_refs[]") for item in self.artifact_refs)
        except TypeError as exc:
            raise TypeError("artifact_refs must be a sequence of references") from exc
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "artifact_refs", artifact_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool": self.tool.value,
            "status": self.status.value,
            "data": dict(self.data),
            "error_code": self.error_code,
            "artifact_refs": list(self.artifact_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "McpToolResult":
        if not isinstance(value, Mapping):
            raise ValueError("MCP result must be an object")
        allowed = {"request_id", "tool", "status", "data", "error_code", "artifact_refs"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown result field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid MCP result: {exc}") from exc


__all__ = [
    "MCP_TOOL_SPECS",
    "McpApprovalRequirement",
    "McpResultStatus",
    "McpToolName",
    "McpToolRequest",
    "McpToolResult",
    "McpToolSpec",
    "UnknownOutcomePolicy",
]
