"""Bounded ReviewPacket and ReviewDecision contracts for DevFarm consumers.

Review payload normalization is independent from Supervisor heartbeat metadata.
This module is a neutral Host-owned contract boundary: it validates compact,
reference-first review data but does not own approval, integration, or task
authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


REVIEW_DECISION_VALUES = frozenset({"APPROVE_INTEGRATION", "REWORK", "REJECT", "ESCALATE"})
REVIEW_PACKET_LIMIT = 64
REVIEW_FINDING_LIMIT = 16
REVIEW_REFERENCE_LIMIT = 32
REVIEW_ISSUE_LIMIT = 16
FORBIDDEN_EVENT_KEYS = frozenset({"raw_output", "conversation", "payload", "stdout", "stderr", "patch"})


def _json(value: Any, name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable") from exc
    return value


def _text(value: Any, name: str, *, optional: bool = False, max_length: int = 4000) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


def bounded_strings(value: Any, name: str, *, limit: int, max_length: int = 1000) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if len(value) > limit:
        raise ValueError(f"{name} must contain at most {limit} items")
    result: list[str] = []
    for item in value:
        normalized = _text(item, name, max_length=max_length)
        assert normalized is not None
        result.append(normalized)
    return result


def reject_forbidden(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        if FORBIDDEN_EVENT_KEYS.intersection(value):
            raise ValueError(f"{name} must not contain raw output")
        for child in value.values():
            reject_forbidden(child, name)
    elif isinstance(value, list):
        for child in value:
            reject_forbidden(child, name)


def _references(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if len(value) > REVIEW_REFERENCE_LIMIT:
        raise ValueError(f"{name} must contain at most {REVIEW_REFERENCE_LIMIT} items")
    references: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{name} must contain objects")
        reject_forbidden(item, name)
        _json(item, name)
        references.append(dict(item))
    return references


def normalize_review_packet(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the compact, reference-first packet sent to a reviewer."""

    if not isinstance(value, Mapping):
        raise TypeError("review packet must be an object")
    reject_forbidden(value, "review packet")
    task_id = _text(value.get("task_id"), "review_packet.task_id", max_length=101)
    attempt_id = _text(value.get("attempt_id"), "review_packet.attempt_id", max_length=101)
    status = _text(value.get("status"), "review_packet.status", max_length=32)
    assert task_id is not None and attempt_id is not None and status is not None
    changed_files = bounded_strings(
        value.get("changed_files", []), "review_packet.changed_files", limit=64, max_length=400
    )
    patch_sha256 = value.get("patch_sha256")
    if patch_sha256 is not None:
        patch_sha256 = _text(patch_sha256, "review_packet.patch_sha256", max_length=64)
        assert patch_sha256 is not None
        if len(patch_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in patch_sha256):
            raise ValueError("review_packet.patch_sha256 must be a SHA-256 hex digest")
        patch_sha256 = patch_sha256.lower()
    verification_summary = value.get("verification_summary", {})
    if not isinstance(verification_summary, Mapping):
        raise TypeError("review_packet.verification_summary must be an object")
    reject_forbidden(verification_summary, "review_packet.verification_summary")
    _json(verification_summary, "review_packet.verification_summary")
    if len(json.dumps(verification_summary, ensure_ascii=False)) > 8000:
        raise ValueError("review_packet.verification_summary is too large")
    packet: dict[str, Any] = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "status": status,
        "changed_files": changed_files,
        "verification_summary": dict(verification_summary),
        "known_issues": bounded_strings(
            value.get("known_issues", []), "review_packet.known_issues", limit=REVIEW_ISSUE_LIMIT
        ),
        "acceptance": bounded_strings(value.get("acceptance", []), "review_packet.acceptance", limit=32),
        "artifact_refs": _references(value.get("artifact_refs", []), "review_packet.artifact_refs"),
    }
    if patch_sha256 is not None:
        packet["patch_sha256"] = patch_sha256
    for key in ("provider", "model", "result_ref", "verification_ref", "created_at"):
        if value.get(key) is not None:
            packet[key] = _text(value[key], f"review_packet.{key}", max_length=400)
    return packet


def normalize_review_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a durable reviewer decision without elevating it to authority."""

    if not isinstance(value, Mapping):
        raise TypeError("review decision must be an object")
    reject_forbidden(value, "review decision")
    decision_id = _text(value.get("decision_id"), "review_decision.decision_id", max_length=101)
    task_id = _text(value.get("task_id"), "review_decision.task_id", max_length=101)
    attempt_id = _text(value.get("attempt_id"), "review_decision.attempt_id", max_length=101)
    decision = _text(value.get("decision"), "review_decision.decision", max_length=32)
    assert decision_id is not None and task_id is not None and attempt_id is not None and decision is not None
    decision = decision.upper()
    if decision not in REVIEW_DECISION_VALUES:
        raise ValueError(f"unsupported review decision: {decision}")
    result: dict[str, Any] = {
        "decision_id": decision_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "decision": decision,
        "findings": bounded_strings(
            value.get("findings", []), "review_decision.findings", limit=REVIEW_FINDING_LIMIT
        ),
        "evidence_refs": _references(value.get("evidence_refs", []), "review_decision.evidence_refs"),
    }
    correction = value.get("required_correction")
    if correction is not None:
        result["required_correction"] = _text(correction, "review_decision.required_correction", max_length=4000)
    for key in ("decided_at", "reviewer_role"):
        if value.get(key) is not None:
            result[key] = _text(value[key], f"review_decision.{key}", max_length=256)
    return result


__all__ = [
    "FORBIDDEN_EVENT_KEYS",
    "REVIEW_DECISION_VALUES",
    "REVIEW_FINDING_LIMIT",
    "REVIEW_ISSUE_LIMIT",
    "REVIEW_PACKET_LIMIT",
    "REVIEW_REFERENCE_LIMIT",
    "bounded_strings",
    "normalize_review_decision",
    "normalize_review_packet",
    "reject_forbidden",
]
