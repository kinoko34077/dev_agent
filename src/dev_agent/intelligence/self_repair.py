"""Proposal-only D9 Controlled Self-Repair candidate policy.

This module evaluates Host evidence for one bounded repair candidate.  It does
not execute a patch, create a Commander task, consume approval, mutate Git, or
perform rollback.  Existing DevFarm/Host Verification and approval authorities
remain the only owners of those effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
import re
from typing import Any
from uuid import uuid4

from ..policy.approvals import canonical_arguments_hash
from src.dev_agent.security.audit import AuditRecorder
from src.dev_agent.security.protected_paths import is_protected_path

from .self_improvement import ImprovementPlanProposal


MAX_CHANGED_FILES = 16
MAX_TEXT_CHARS = 4_000
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRUST_LEVELS = frozenset({"TRUSTED_HOST_EXEC", "OS_SANDBOXED"})
_VERIFICATION_STATUSES = frozenset({"passed", "failed", "unknown"})
_FORBIDDEN_KEYS = frozenset(
    {
        "patch",
        "raw_output",
        "conversation",
        "stdout",
        "stderr",
        "token",
        "api_key",
        "credentials",
        "private_key",
    }
)


def _text(value: Any, name: str, *, maximum: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    if any(pattern.search(normalized) for pattern in AuditRecorder.SECRET_PATTERNS):
        raise ValueError(f"{name} contains secret material")
    return normalized


def _safe_artifact_ref(value: Any, name: str) -> str:
    normalized = _text(value, name)
    path = normalized.replace("\\", "/")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"{name} must be a safe relative artifact path")
    if not path.startswith(".devfarm/"):
        raise ValueError(f"{name} must reference a .devfarm artifact")
    return str(parsed)


def _changed_files(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError("changed_files must be a non-empty sequence")
    if len(value) > MAX_CHANGED_FILES:
        raise ValueError("changed_files contains too many items")
    result: list[str] = []
    for item in value:
        normalized = _text(item, "changed_files[]", maximum=512).replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("changed_files contains an unsafe path")
        value_path = str(parsed)
        if value_path not in result:
            result.append(value_path)
    return tuple(result)


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        if any(str(key).lower() in _FORBIDDEN_KEYS for key in value):
            return True
        return any(_contains_forbidden(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


@dataclass(frozen=True)
class RepairEvidence:
    """Host evidence required before a repair candidate can be proposed."""

    plan_id: str
    base_revision: str
    attempt_id: str
    patch_ref: str
    patch_sha256: str
    manifest_ref: str
    verification_ref: str
    changed_files: tuple[str, ...]
    verification_status: str
    verification_trust_level: str
    operator_approved: bool
    independent_verification: bool
    external_outcome_known: bool
    rollback_ref: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id", maximum=128))
        object.__setattr__(self, "base_revision", _text(self.base_revision, "base_revision", maximum=128))
        object.__setattr__(self, "attempt_id", _text(self.attempt_id, "attempt_id", maximum=128))
        object.__setattr__(self, "patch_ref", _safe_artifact_ref(self.patch_ref, "patch_ref"))
        digest = _text(self.patch_sha256, "patch_sha256", maximum=64).lower()
        if not _HEX_SHA256.fullmatch(digest):
            raise ValueError("patch_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "patch_sha256", digest)
        object.__setattr__(self, "manifest_ref", _safe_artifact_ref(self.manifest_ref, "manifest_ref"))
        object.__setattr__(self, "verification_ref", _safe_artifact_ref(self.verification_ref, "verification_ref"))
        object.__setattr__(self, "changed_files", _changed_files(self.changed_files))
        status = _text(self.verification_status, "verification_status", maximum=32).lower()
        if status not in _VERIFICATION_STATUSES:
            raise ValueError(f"unsupported verification_status: {status}")
        object.__setattr__(self, "verification_status", status)
        trust = _text(self.verification_trust_level, "verification_trust_level", maximum=64).upper()
        object.__setattr__(self, "verification_trust_level", trust)
        object.__setattr__(self, "operator_approved", _bool(self.operator_approved, "operator_approved"))
        object.__setattr__(
            self,
            "independent_verification",
            _bool(self.independent_verification, "independent_verification"),
        )
        object.__setattr__(
            self,
            "external_outcome_known",
            _bool(self.external_outcome_known, "external_outcome_known"),
        )
        if self.rollback_ref is not None:
            object.__setattr__(self, "rollback_ref", _text(self.rollback_ref, "rollback_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "base_revision": self.base_revision,
            "attempt_id": self.attempt_id,
            "patch_ref": self.patch_ref,
            "patch_sha256": self.patch_sha256,
            "manifest_ref": self.manifest_ref,
            "verification_ref": self.verification_ref,
            "changed_files": list(self.changed_files),
            "verification_status": self.verification_status,
            "verification_trust_level": self.verification_trust_level,
            "operator_approved": self.operator_approved,
            "independent_verification": self.independent_verification,
            "external_outcome_known": self.external_outcome_known,
            "rollback_ref": self.rollback_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairEvidence":
        if not isinstance(value, Mapping):
            raise ValueError("repair evidence must be an object")
        if _contains_forbidden(value):
            raise ValueError("repair evidence contains patch or secret material")
        allowed = {
            "plan_id",
            "base_revision",
            "attempt_id",
            "patch_ref",
            "patch_sha256",
            "manifest_ref",
            "verification_ref",
            "changed_files",
            "verification_status",
            "verification_trust_level",
            "operator_approved",
            "independent_verification",
            "external_outcome_known",
            "rollback_ref",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown repair evidence field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid repair evidence: {exc}") from exc


@dataclass(frozen=True)
class RepairCandidate:
    """A repair proposal with no execution or integration authority."""

    plan_id: str
    evidence: RepairEvidence
    candidate_id: str = field(default_factory=lambda: f"repair-candidate-{uuid4().hex}")
    status: str = "PROPOSAL_ONLY"
    requires_human_approval: bool = True
    integration_performed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id", maximum=128))
        if not isinstance(self.evidence, RepairEvidence):
            raise TypeError("evidence must be RepairEvidence")
        if self.evidence.plan_id != self.plan_id:
            raise ValueError("candidate plan_id must match evidence")
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id", maximum=128))
        if self.status != "PROPOSAL_ONLY":
            raise ValueError("repair candidate status must remain PROPOSAL_ONLY")
        if self.requires_human_approval is not True:
            raise ValueError("repair candidates always require Human approval")
        if self.integration_performed is not False:
            raise ValueError("repair candidate cannot perform integration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "plan_id": self.plan_id,
            "evidence": self.evidence.to_dict(),
            "status": self.status,
            "requires_human_approval": self.requires_human_approval,
            "integration_performed": self.integration_performed,
        }


@dataclass(frozen=True)
class RepairEvaluation:
    eligible: bool
    status: str
    integration_authority: str
    reasons: tuple[str, ...] = ()
    candidate: RepairCandidate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "integration_authority": self.integration_authority,
            "reasons": list(self.reasons),
            "candidate": self.candidate.to_dict() if self.candidate is not None else None,
        }


@dataclass(frozen=True)
class RepairExecutionRequest:
    """Reference-only request for the existing Host integration boundary.

    This request deliberately contains no patch bytes and grants no mutation
    authority.  It binds the candidate, verified attempt, durable review
    decision, target, and approval arguments so a later Host adapter cannot
    silently apply a different repair than the one reviewed.
    """

    candidate_id: str
    run_id: str
    task_id: str
    attempt_id: str
    review_decision_id: str
    target_checkout_ref: str
    target_ref: str
    commit_message: str
    approval_id: str
    call_id: str

    def __post_init__(self) -> None:
        for name, maximum in (
            ("candidate_id", 128),
            ("run_id", 128),
            ("task_id", 128),
            ("attempt_id", 128),
            ("review_decision_id", 128),
            ("target_checkout_ref", 512),
            ("target_ref", 256),
            ("approval_id", 128),
            ("call_id", 128),
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name, maximum=maximum))
        object.__setattr__(
            self,
            "commit_message",
            _text(self.commit_message, "commit_message", maximum=200),
        )

    def authorization_arguments(self) -> dict[str, str]:
        """Return only effect-defining arguments for approval binding."""

        return {
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "review_decision_id": self.review_decision_id,
            "target_checkout_ref": self.target_checkout_ref,
            "target_ref": self.target_ref,
            "commit_message": self.commit_message,
        }

    def authorization_hash(self) -> str:
        return canonical_arguments_hash(self.authorization_arguments())

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "review_decision_id": self.review_decision_id,
            "target_checkout_ref": self.target_checkout_ref,
            "target_ref": self.target_ref,
            "commit_message": self.commit_message,
            "approval_id": self.approval_id,
            "call_id": self.call_id,
            "authorization_arguments_hash": self.authorization_hash(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairExecutionRequest":
        if not isinstance(value, Mapping):
            raise ValueError("repair execution request must be an object")
        allowed = {
            "candidate_id",
            "run_id",
            "task_id",
            "attempt_id",
            "review_decision_id",
            "target_checkout_ref",
            "target_ref",
            "commit_message",
            "approval_id",
            "call_id",
            "authorization_arguments_hash",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown repair execution field: {sorted(unknown)[0]}")
        payload = dict(value)
        supplied_hash = payload.pop("authorization_arguments_hash", None)
        try:
            request = cls(**payload)
        except TypeError as exc:
            raise ValueError(f"invalid repair execution request: {exc}") from exc
        if supplied_hash is not None and supplied_hash != request.authorization_hash():
            raise ValueError("repair execution authorization hash mismatch")
        return request


@dataclass(frozen=True)
class RepairExecutionEvaluation:
    """Non-mutating result of checking an approval-bound repair request."""

    eligible: bool
    status: str
    integration_authority: str
    reasons: tuple[str, ...] = ()
    request: RepairExecutionRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "integration_authority": self.integration_authority,
            "reasons": list(self.reasons),
            "request": self.request.to_dict() if self.request is not None else None,
        }


class RepairExecutionPolicy:
    """Preflight D9 execution without consuming approval or mutating state.

    The actual side effect remains the existing Supervisor Host helper.  This
    policy only checks that a durable external-write approval exists for the
    exact candidate and target arguments.  A later adapter must still
    re-read the candidate, review decision, verification digests, and clean
    checkout before consuming approval and invoking that helper.
    """

    def evaluate(
        self,
        candidate: RepairCandidate,
        request: RepairExecutionRequest,
        approval_store: Any,
    ) -> RepairExecutionEvaluation:
        if not isinstance(candidate, RepairCandidate):
            raise TypeError("candidate must be a RepairCandidate")
        if not isinstance(request, RepairExecutionRequest):
            raise TypeError("request must be a RepairExecutionRequest")

        reasons: list[str] = []
        if request.candidate_id != candidate.candidate_id:
            reasons.append("candidate_identity_mismatch")
        if request.attempt_id != candidate.evidence.attempt_id:
            reasons.append("attempt_identity_mismatch")
        if not request.review_decision_id:
            reasons.append("review_decision_required")
        if reasons:
            return RepairExecutionEvaluation(
                eligible=False,
                status="REJECTED",
                integration_authority="existing_supervisor_host_helper",
                reasons=tuple(dict.fromkeys(reasons)),
            )
        if approval_store is None or not callable(getattr(approval_store, "has_approval", None)):
            reasons.append("approval_store_required")
        else:
            try:
                approved = approval_store.has_approval(
                    request.approval_id,
                    task_id=request.task_id,
                    side_effect_level="external_write",
                    call_id=request.call_id,
                    arguments_hash=request.authorization_hash(),
                )
            except (AttributeError, TypeError, ValueError):
                approved = False
                reasons.append("approval_lookup_failed")
            if approved is not True:
                reasons.append("approval_missing_or_mismatched")

        if reasons:
            return RepairExecutionEvaluation(
                eligible=False,
                status="REJECTED",
                integration_authority="existing_supervisor_host_helper",
                reasons=tuple(dict.fromkeys(reasons)),
            )
        return RepairExecutionEvaluation(
            eligible=True,
            status="READY_FOR_HOST_EXECUTION",
            integration_authority="existing_supervisor_host_helper",
            reasons=("durable_approval_preflight_passed",),
            request=request,
        )


class RepairPolicy:
    """Deterministically evaluate a bounded D9 repair candidate."""

    def evaluate(self, plan: ImprovementPlanProposal, evidence: RepairEvidence) -> RepairEvaluation:
        if not isinstance(plan, ImprovementPlanProposal):
            raise TypeError("plan must be an ImprovementPlanProposal")
        if not isinstance(evidence, RepairEvidence):
            raise TypeError("evidence must be RepairEvidence")

        reasons: list[str] = []
        if plan.status != "PROPOSAL_ONLY" or plan.requires_human_approval is not True:
            reasons.append("improvement_plan_not_proposal_only")
        if plan.plan_id != evidence.plan_id:
            reasons.append("plan_identity_mismatch")
        if plan.risk not in {"low", "normal"}:
            reasons.append("repair_risk_not_allowed")
        if evidence.verification_status != "passed":
            reasons.append("host_verification_not_passed")
        if evidence.verification_trust_level not in _TRUST_LEVELS:
            reasons.append("verification_trust_not_allowed")
        if evidence.verification_trust_level == "TRUSTED_HOST_EXEC" and not evidence.operator_approved:
            reasons.append("operator_approval_required")
        if not evidence.independent_verification:
            reasons.append("independent_verification_required")
        if not evidence.external_outcome_known:
            reasons.append("external_outcome_unknown")
        if evidence.rollback_ref is None:
            reasons.append("rollback_path_missing")
        if any(is_protected_path(path) for path in evidence.changed_files):
            reasons.append("protected_path")

        if reasons:
            return RepairEvaluation(
                eligible=False,
                status="REJECTED",
                integration_authority="codex_and_host",
                reasons=tuple(dict.fromkeys(reasons)),
            )
        candidate = RepairCandidate(plan_id=plan.plan_id, evidence=evidence)
        return RepairEvaluation(
            eligible=True,
            status="CANDIDATE",
            integration_authority="host_policy",
            reasons=("bounded_repair_evidence_passed",),
            candidate=candidate,
        )


__all__ = [
    "RepairCandidate",
    "RepairEvidence",
    "RepairEvaluation",
    "RepairExecutionEvaluation",
    "RepairExecutionPolicy",
    "RepairExecutionRequest",
    "RepairPolicy",
]
