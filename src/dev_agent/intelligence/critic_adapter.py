"""Proposal-only L1 refinement criticism over bounded Host evidence.

The adapter asks an injected provider for concise correction findings.  It does
not select a provider, rewrite source, approve integration, or mutate a Task;
the caller keeps those responsibilities at the existing Host boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ..coordination.protocol_helpers import (
    ensure_json_safe,
    ensure_secret_free,
    validate_identifier,
    validate_relative_path,
    validate_text,
)
from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ModelProvider
from .refinement import CriticFinding, RefinementProposal
from .structured_response import StructuredResponseError, decode_json_object


class CriticAdapterError(ValueError):
    """The bounded critic packet or response is not safe to use."""


CRITIC_PROPOSAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id", "attempt_id", "findings", "evidence_refs"],
    "properties": {
        "task_id": {"type": "string"},
        "attempt_id": {"type": "string"},
        "findings": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["location", "problem", "required_correction"],
                "properties": {
                    "location": {"type": "string"},
                    "problem": {"type": "string"},
                    "required_correction": {"type": "string"},
                },
            },
        },
        "evidence_refs": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
    },
}

_PACKET_KEYS = frozenset(
    {
        "task_id",
        "attempt_id",
        "failure_class",
        "failure_summary",
        "changed_files",
        "patch_sha256",
        "evidence_refs",
        "acceptance",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "raw_output",
        "conversation",
        "payload",
        "patch",
        "stdout",
        "stderr",
        "source",
        "credential",
        "secret",
        "token",
    }
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_PACKET_CHARS = 16_000
_MAX_RESPONSE_CHARS = 16_000
_MAX_CHANGED_FILES = 64
_MAX_EVIDENCE_REFS = 64
_MAX_ACCEPTANCE = 32
_MAX_FAILURE_SUMMARY_CHARS = 4_000


def _reject_forbidden(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.lower().replace("-", "_") in _FORBIDDEN_KEYS:
                raise CriticAdapterError(f"{name} must not contain raw output")
            _reject_forbidden(child, name)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_forbidden(child, name)


def _bounded_sequence(value: Any, name: str, *, limit: int, max_chars: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CriticAdapterError(f"{name} must be a sequence")
    if len(value) > limit:
        raise CriticAdapterError(f"{name} exceeds its input limit")
    result = tuple(validate_text(item, f"{name}[]", max_chars=max_chars) for item in value)
    if len(set(result)) != len(result):
        raise CriticAdapterError(f"{name} must not contain duplicates")
    return result


def _bounded_failure_summary(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CriticAdapterError("failure_summary must be non-empty text")
    if len(value) > _MAX_FAILURE_SUMMARY_CHARS:
        raise CriticAdapterError("failure_summary exceeds the input limit")
    try:
        return validate_text(value, "failure_summary", max_chars=_MAX_FAILURE_SUMMARY_CHARS)
    except ValueError as exc:
        raise CriticAdapterError(str(exc)) from exc


def _evidence_refs(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CriticAdapterError("evidence_refs must be a sequence")
    if len(value) > _MAX_EVIDENCE_REFS:
        raise CriticAdapterError("evidence_refs exceeds its input limit")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CriticAdapterError("evidence_refs must contain objects")
        ensure_json_safe(item, "evidence_refs")
        ensure_secret_free(item, "evidence_refs")
        normalized = dict(item)
        result.append(normalized)
    return tuple(result)


def _protocol_task_id(task_id: str) -> str:
    try:
        UUID(task_id)
    except (TypeError, ValueError, AttributeError):
        return str(uuid5(NAMESPACE_URL, f"dev_agent.refinement-critic/{task_id}"))
    return task_id


class ModelCriticAdapter:
    """Request one concise L1 Critic proposal from a Host-selected provider."""

    def __init__(self, provider: ModelProvider, *, max_output_tokens: int = 512) -> None:
        if not callable(getattr(provider, "request", None)):
            raise TypeError("provider must expose request(ModelRequest)")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 0 < max_output_tokens <= 2_048
        ):
            raise ValueError("max_output_tokens must be between 1 and 2048")
        self.provider = provider
        self.max_output_tokens = max_output_tokens

    def propose(self, packet: Mapping[str, Any]) -> RefinementProposal:
        normalized = self._packet(packet)
        task_id = normalized["task_id"]
        attempt_id = normalized["attempt_id"]
        content = (
            "Generate exactly one raw JSON object matching the supplied refinement proposal schema. "
            "Do not emit Markdown, explanatory prose, comments, or trailing text. "
            "This is a proposal-only correction critique: do not make an integration decision, "
            "change authority, approve a result, or request a policy relaxation. "
            "Keep task_id and attempt_id exactly equal to the packet. "
            "Return concise findings with location, problem, and required_correction, plus only "
            "bounded evidence references. Do not invent file contents or unreferenced evidence. "
            "Use this shape: {\"task_id\":\"...\",\"attempt_id\":\"...\",\"findings\":[{"
            "\"location\":\"...\",\"problem\":\"...\",\"required_correction\":\"...\"}],"
            "\"evidence_refs\":[]}\n"
            "bounded_failure_packet:\n"
            f"{json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )
        metadata = {
            "dev_agent_task_id": task_id,
            "dev_agent_attempt_id": attempt_id,
            "refinement_mode": "critic",
            "task_fit": "correction",
            "authority": "host_validation_required",
            "proposal_only": True,
            "integration_authority": "host_and_codex",
            "model_selection": "host_selected",
            "intelligence_routing": "bounded",
            "allowed_intelligence_tiers": ["L1"],
        }
        try:
            request = ModelRequest(
                task_id=_protocol_task_id(task_id),
                messages=[{"role": "user", "content": content}],
                requested_capabilities=["text"],
                response_schema=CRITIC_PROPOSAL_RESPONSE_SCHEMA,
                max_output_tokens=self.max_output_tokens,
                sensitivity="normal",
                cost_ceiling=0.0,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            raise CriticAdapterError(f"invalid critic request: {exc}") from exc
        try:
            payload = decode_json_object(response := self.provider.request(request), role="critic", max_chars=_MAX_RESPONSE_CHARS)
            proposal = RefinementProposal.from_dict(payload)
        except StructuredResponseError as exc:
            raise CriticAdapterError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise CriticAdapterError(str(exc)) from exc
        if proposal.task_id != task_id:
            raise CriticAdapterError("critic proposal task_id does not match the packet")
        if proposal.attempt_id != attempt_id:
            raise CriticAdapterError("critic proposal attempt_id does not match the packet")
        return proposal

    @staticmethod
    def _packet(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise CriticAdapterError("critic packet must be an object")
        _reject_forbidden(value, "critic packet")
        unknown = set(value) - _PACKET_KEYS
        if unknown:
            raise CriticAdapterError(f"unknown critic packet field: {sorted(unknown)[0]}")
        try:
            task_id = validate_identifier(value.get("task_id"), "task_id")
            attempt_id = validate_identifier(value.get("attempt_id"), "attempt_id")
            failure_class = validate_identifier(value.get("failure_class"), "failure_class").lower()
        except ValueError as exc:
            raise CriticAdapterError(str(exc)) from exc
        failure_summary = _bounded_failure_summary(value.get("failure_summary"))
        changed_files_raw = value.get("changed_files", ())
        changed_files = _bounded_sequence(
            changed_files_raw,
            "changed_files",
            limit=_MAX_CHANGED_FILES,
            max_chars=512,
        )
        normalized_files: list[str] = []
        for item in changed_files:
            try:
                normalized_files.append(validate_relative_path(item, "changed_files[]"))
            except ValueError as exc:
                raise CriticAdapterError(str(exc)) from exc
        patch_sha256 = value.get("patch_sha256")
        if patch_sha256 is not None:
            if not isinstance(patch_sha256, str) or _SHA256.fullmatch(patch_sha256.strip()) is None:
                raise CriticAdapterError("patch_sha256 must be a SHA-256 hex digest")
            patch_sha256 = patch_sha256.strip().lower()
        evidence_refs = _evidence_refs(value.get("evidence_refs", ()))
        acceptance = _bounded_sequence(
            value.get("acceptance", ()),
            "acceptance",
            limit=_MAX_ACCEPTANCE,
            max_chars=1_024,
        )
        normalized: dict[str, Any] = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "failure_class": failure_class,
            "failure_summary": failure_summary,
            "changed_files": normalized_files,
            "evidence_refs": list(evidence_refs),
            "acceptance": list(acceptance),
        }
        if patch_sha256 is not None:
            normalized["patch_sha256"] = patch_sha256
        try:
            ensure_json_safe(normalized, "critic packet")
            ensure_secret_free(normalized, "critic packet")
        except ValueError as exc:
            raise CriticAdapterError(str(exc)) from exc
        serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > _MAX_PACKET_CHARS:
            raise CriticAdapterError("critic packet exceeds the input limit")
        return normalized


__all__ = [
    "CRITIC_PROPOSAL_RESPONSE_SCHEMA",
    "CriticAdapterError",
    "ModelCriticAdapter",
]
