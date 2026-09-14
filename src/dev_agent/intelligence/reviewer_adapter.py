"""Proposal-only Free L2 review over the existing compact ReviewPacket.

The adapter deliberately stops at a typed review proposal.  It does not write
ReviewDecision state, approve integration, reassign a Worker, or execute Git
operations.  Those authorities remain with the existing Supervisor and Host
integration boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ModelProvider


class ReviewAdapterError(ValueError):
    """The review packet or provider response is not safe to use as a proposal."""


REVIEW_PROPOSAL_DECISIONS = frozenset(
    {"APPROVE_INTEGRATION", "REWORK", "REJECT", "ESCALATE"}
)
_FORBIDDEN_KEYS = frozenset({"raw_output", "conversation", "payload", "stdout", "stderr", "patch"})
_PACKET_KEYS = frozenset(
    {
        "task_id",
        "attempt_id",
        "status",
        "provider",
        "model",
        "changed_files",
        "patch_sha256",
        "result_ref",
        "verification_ref",
        "verification_summary",
        "known_issues",
        "acceptance",
        "artifact_refs",
        "created_at",
    }
)
_MAX_PACKET_CHARS = 24_000
_MAX_RESPONSE_CHARS = 16_000
_MAX_FINDINGS = 16
_MAX_FINDING_CHARS = 1_000
_MAX_REFERENCES = 32
_MAX_RATIONALE_CHARS = 2_000
_MAX_CORRECTION_CHARS = 4_000
_FENCED_JSON = re.compile(r"^```(?:json)?\s*\r?\n(?P<body>.*?)\r?\n```$", re.IGNORECASE | re.DOTALL)


REVIEW_PROPOSAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id", "attempt_id", "decision", "rationale"],
    "properties": {
        "task_id": {"type": "string"},
        "attempt_id": {"type": "string"},
        "decision": {"type": "string", "enum": sorted(REVIEW_PROPOSAL_DECISIONS)},
        "findings": {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_FINDINGS},
        "evidence_refs": {"type": "array", "items": {"type": "object"}, "maxItems": _MAX_REFERENCES},
        "required_correction": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
}


def _text(value: Any, name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewAdapterError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ReviewAdapterError(f"{name} is too long")
    return value


def _protocol_task_id(task_id: str) -> str:
    """Map a readable DevFarm task id to the protocol's UUID identity.

    Development manifests intentionally use readable ids, while the shared
    ModelRequest contract requires UUID task identities.  Keep the readable id
    in the proposal and prompt, and use a deterministic UUID only at the
    protocol boundary so retries and reviews retain one stable task identity.
    """

    try:
        UUID(task_id)
    except (TypeError, ValueError, AttributeError):
        return str(uuid5(NAMESPACE_URL, f"dev_agent.reviewer/{task_id}"))
    return task_id


def _reject_forbidden(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ReviewAdapterError(f"{name} must not contain raw output")
        for child in value.values():
            _reject_forbidden(child, name)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden(child, name)


def _json(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReviewAdapterError(f"{name} must be JSON-serializable") from exc


def _bounded_strings(value: Any, name: str, *, limit: int, max_length: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReviewAdapterError(f"{name} must be a sequence")
    if len(value) > limit:
        raise ReviewAdapterError(f"{name} contains too many items")
    return tuple(_text(item, f"{name}[]", max_length=max_length) for item in value)


def _references(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReviewAdapterError(f"{name} must be a sequence")
    if len(value) > _MAX_REFERENCES:
        raise ReviewAdapterError(f"{name} contains too many items")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ReviewAdapterError(f"{name} must contain objects")
        _reject_forbidden(item, name)
        _json(item, name)
        result.append(dict(item))
    return tuple(result)


@dataclass(frozen=True)
class ReviewProposal:
    """A Free L2 review suggestion with no integration authority."""

    task_id: str
    attempt_id: str
    decision: str
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[Mapping[str, Any], ...] = ()
    required_correction: str | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        task_id = _text(self.task_id, "task_id", max_length=101)
        attempt_id = _text(self.attempt_id, "attempt_id", max_length=101)
        decision = _text(self.decision, "decision", max_length=32).upper()
        if decision not in REVIEW_PROPOSAL_DECISIONS:
            raise ReviewAdapterError(f"unsupported review proposal decision: {decision}")
        findings = _bounded_strings(self.findings, "findings", limit=_MAX_FINDINGS, max_length=_MAX_FINDING_CHARS)
        evidence_refs = _references(self.evidence_refs, "evidence_refs")
        correction = self.required_correction
        if correction is not None:
            correction = _text(correction, "required_correction", max_length=_MAX_CORRECTION_CHARS)
        if decision == "REWORK" and correction is None:
            raise ReviewAdapterError("REWORK requires required_correction")
        rationale = _text(self.rationale, "rationale", max_length=_MAX_RATIONALE_CHARS)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "required_correction", correction)
        object.__setattr__(self, "rationale", rationale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "decision": self.decision,
            "findings": list(self.findings),
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "required_correction": self.required_correction,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewProposal":
        if not isinstance(value, Mapping):
            raise ReviewAdapterError("review proposal must be an object")
        allowed = {
            "task_id",
            "attempt_id",
            "decision",
            "findings",
            "evidence_refs",
            "required_correction",
            "rationale",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ReviewAdapterError(f"unknown review proposal field: {sorted(unknown)[0]}")
        try:
            return cls(
                task_id=value.get("task_id"),
                attempt_id=value.get("attempt_id"),
                decision=value.get("decision"),
                findings=value.get("findings", ()),
                evidence_refs=value.get("evidence_refs", ()),
                required_correction=value.get("required_correction"),
                rationale=value.get("rationale"),
            )
        except TypeError as exc:
            raise ReviewAdapterError(f"invalid review proposal: {exc}") from exc


@dataclass(frozen=True)
class ReviewShadowComparison:
    """Bounded comparison between a shadow proposal and Codex's final decision."""

    proposal_decision: str
    codex_decision: str
    agreement: bool
    false_approve: bool
    false_reject: bool
    missed_issue: bool
    unnecessary_rework: bool
    evidence_quality: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_decision": self.proposal_decision,
            "codex_decision": self.codex_decision,
            "agreement": self.agreement,
            "false_approve": self.false_approve,
            "false_reject": self.false_reject,
            "missed_issue": self.missed_issue,
            "unnecessary_rework": self.unnecessary_rework,
            "evidence_quality": self.evidence_quality,
        }


def compare_review_proposal(
    proposal: ReviewProposal,
    codex_decision: str,
    packet: Mapping[str, Any],
) -> ReviewShadowComparison:
    """Compare a proposal with the durable Codex outcome without changing it."""

    if not isinstance(proposal, ReviewProposal):
        raise TypeError("proposal must be a ReviewProposal")
    normalized_packet = ModelReviewAdapter._packet(packet)
    final_decision = _text(codex_decision, "codex_decision", max_length=32).upper()
    if final_decision not in REVIEW_PROPOSAL_DECISIONS:
        raise ReviewAdapterError(f"unsupported Codex decision: {final_decision}")
    artifact_refs = {
        json.dumps(dict(reference), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for reference in normalized_packet["artifact_refs"]
    }
    proposal_refs = {
        json.dumps(dict(reference), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for reference in proposal.evidence_refs
    }
    if not proposal_refs:
        evidence_quality = "unreferenced"
    elif proposal_refs.issubset(artifact_refs):
        evidence_quality = "grounded"
    else:
        evidence_quality = "unmatched_reference"
    return ReviewShadowComparison(
        proposal_decision=proposal.decision,
        codex_decision=final_decision,
        agreement=proposal.decision == final_decision,
        false_approve=proposal.decision == "APPROVE_INTEGRATION" and final_decision != "APPROVE_INTEGRATION",
        false_reject=proposal.decision in {"REJECT", "ESCALATE"} and final_decision == "APPROVE_INTEGRATION",
        missed_issue=proposal.decision == "APPROVE_INTEGRATION" and final_decision != "APPROVE_INTEGRATION",
        unnecessary_rework=proposal.decision == "REWORK" and final_decision == "APPROVE_INTEGRATION",
        evidence_quality=evidence_quality,
    )


class ModelReviewAdapter:
    """Request one bounded Free L2 review proposal from an injected provider."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_output_tokens: int = 1_024,
        allow_unknown_quota: bool = False,
    ) -> None:
        if not callable(getattr(provider, "request", None)):
            raise TypeError("provider must expose request(ModelRequest)")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 0 < max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        if not isinstance(allow_unknown_quota, bool):
            raise TypeError("allow_unknown_quota must be a boolean")
        self.provider = provider
        self.max_output_tokens = max_output_tokens
        self.allow_unknown_quota = allow_unknown_quota

    def propose(self, packet: Mapping[str, Any]) -> ReviewProposal:
        normalized = self._packet(packet)
        task_id = normalized["task_id"]
        protocol_task_id = _protocol_task_id(task_id)
        content = (
            "Generate exactly one raw JSON object matching the supplied review proposal schema. "
            "Do not emit Markdown fences, explanatory prose, comments, or trailing text. "
            "This is a proposal-only shadow review: do not approve integration, change authority, "
            "reassign a Worker, relax a Gate, or claim a human decision. Use only the compact packet; "
            "patch contents and raw Worker conversation are intentionally unavailable. "
            "Keep `task_id` and `attempt_id` exactly equal to the packet. "
            "Use one of `APPROVE_INTEGRATION`, `REWORK`, `REJECT`, or `ESCALATE` for `decision`. "
            "Always include `findings` and `evidence_refs` as arrays, `required_correction` as a string or null, "
            "and a concise `rationale` string. `REWORK` requires a non-null `required_correction`. "
            "Use this shape: {\"task_id\":\"...\",\"attempt_id\":\"...\",\"decision\":\"...\","
            "\"findings\":[],\"evidence_refs\":[],\"required_correction\":null,\"rationale\":\"...\"}.\n"
            "review_packet:\n"
            f"{json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )
        metadata = {
            "dev_agent_task_id": task_id,
            "review_mode": "shadow",
            "authority": "host_validation_required",
            "integration_authority": "codex_and_host",
            "proposal_only": True,
            "intelligence_routing": "bounded",
            "allowed_intelligence_tiers": ["L2"],
        }
        if self.allow_unknown_quota:
            metadata["allow_unknown_quota"] = True
        try:
            request = ModelRequest(
                task_id=protocol_task_id,
                messages=[{"role": "user", "content": content}],
                requested_capabilities=["text"],
                response_schema=REVIEW_PROPOSAL_RESPONSE_SCHEMA,
                max_output_tokens=self.max_output_tokens,
                sensitivity="normal",
                cost_ceiling=0.0,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            raise ReviewAdapterError(f"invalid review request: {exc}") from exc
        response = self.provider.request(request)
        payload = self._response_payload(response)
        proposal = ReviewProposal.from_dict(payload)
        if proposal.task_id != normalized["task_id"]:
            raise ReviewAdapterError("review proposal task_id does not match the packet")
        if proposal.attempt_id != normalized["attempt_id"]:
            raise ReviewAdapterError("review proposal attempt_id does not match the packet")
        return proposal

    @staticmethod
    def _packet(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ReviewAdapterError("review packet must be an object")
        _reject_forbidden(value, "review packet")
        unknown = set(value) - _PACKET_KEYS
        if unknown:
            raise ReviewAdapterError(f"unknown review packet field: {sorted(unknown)[0]}")
        task_id = _text(value.get("task_id"), "review_packet.task_id", max_length=101)
        attempt_id = _text(value.get("attempt_id"), "review_packet.attempt_id", max_length=101)
        status = _text(value.get("status"), "review_packet.status", max_length=32)
        changed_files = _bounded_strings(value.get("changed_files", []), "review_packet.changed_files", limit=64, max_length=400)
        known_issues = _bounded_strings(value.get("known_issues", []), "review_packet.known_issues", limit=16, max_length=1_000)
        acceptance = _bounded_strings(value.get("acceptance", []), "review_packet.acceptance", limit=32, max_length=1_000)
        artifact_refs = _references(value.get("artifact_refs", []), "review_packet.artifact_refs")
        verification_summary = value.get("verification_summary", {})
        if not isinstance(verification_summary, Mapping):
            raise ReviewAdapterError("review_packet.verification_summary must be an object")
        _reject_forbidden(verification_summary, "review_packet.verification_summary")
        _json(verification_summary, "review_packet.verification_summary")
        normalized: dict[str, Any] = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "status": status,
            "changed_files": list(changed_files),
            "verification_summary": dict(verification_summary),
            "known_issues": list(known_issues),
            "acceptance": list(acceptance),
            "artifact_refs": [dict(item) for item in artifact_refs],
        }
        for key in ("provider", "model", "result_ref", "verification_ref", "created_at"):
            if value.get(key) is not None:
                normalized[key] = _text(value[key], f"review_packet.{key}", max_length=400)
        if value.get("patch_sha256") is not None:
            digest = _text(value["patch_sha256"], "review_packet.patch_sha256", max_length=64).lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ReviewAdapterError("review_packet.patch_sha256 must be a SHA-256 hex digest")
            normalized["patch_sha256"] = digest
        serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > _MAX_PACKET_CHARS:
            raise ReviewAdapterError("review packet exceeds the input limit")
        return normalized

    @staticmethod
    def _response_payload(response: ModelResponse) -> Mapping[str, Any]:
        if not isinstance(response, ModelResponse):
            raise ReviewAdapterError("review provider returned an invalid response type")
        if response.structured_output is not None:
            return response.structured_output
        text = "".join(response.text_segments or response.parts)
        if not isinstance(text, str) or not text.strip():
            raise ReviewAdapterError("review response has no structured JSON output")
        if len(text) > _MAX_RESPONSE_CHARS:
            raise ReviewAdapterError("review response exceeds the response limit")
        candidate = text.strip()
        fenced = _FENCED_JSON.fullmatch(candidate)
        if fenced is not None:
            candidate = fenced.group("body").strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ReviewAdapterError("review response is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ReviewAdapterError("review response JSON must be an object")
        return payload


__all__ = [
    "ReviewShadowComparison",
    "ModelReviewAdapter",
    "REVIEW_PROPOSAL_DECISIONS",
    "REVIEW_PROPOSAL_RESPONSE_SCHEMA",
    "ReviewAdapterError",
    "ReviewProposal",
    "compare_review_proposal",
]
