"""Deterministic admission policy for the bounded D7 Codex-less cycle.

The policy is intentionally a candidate evaluator, not an integration engine.
It combines already-durable Free L2 Reviewer Shadow evidence with the compact
Host Verification packet and returns a bounded decision that a caller may
consider for a routine cycle.  Official-branch mutation, approval, and Git
integration remain outside this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
from typing import Any

from src.dev_agent.security.protected_paths import is_protected_path

from .reviewer_adapter import ReviewAdapterError, ReviewProposal


MIN_SHADOW_EVIDENCE = 2
MAX_SHADOW_EVIDENCE = 32
MAX_CODEX_LESS_CHANGED_FILES = 16
CODEX_LESS_RISKS = frozenset({"low", "normal"})
CODEX_LESS_SENSITIVITIES = frozenset({"public", "normal"})
KNOWN_CODEX_LESS_TASK_TYPES = frozenset(
    {
        "worker",
        "documentation",
        "docs",
        "test",
        "tests",
        "focused_regression",
        "unit",
        "parser",
        "serializer",
        "fixture",
        "fixtures",
        "mechanical_refactor",
        "bounded_bugfix",
        "small_helper",
        "cli_adapter",
        "data_model",
    }
)
_CODEX_LESS_TRUST_LEVELS = frozenset({"TRUSTED_HOST_EXEC", "OS_SANDBOXED"})
_FORBIDDEN_KEYS = frozenset(
    {"raw_output", "conversation", "payload", "stdout", "stderr", "patch"}
)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        if _FORBIDDEN_KEYS.intersection(value):
            return True
        return any(_contains_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return False


def _text(value: Any, name: str, *, maximum: int = 256) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        return None
    return value.strip()


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _reference_key(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ShadowEvidenceGateResult:
    """Bounded result of checking the minimum D6 shadow evidence gate."""

    eligible: bool
    sample_count: int
    distinct_task_count: int
    metrics: Mapping[str, int]
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "sample_count": self.sample_count,
            "distinct_task_count": self.distinct_task_count,
            "metrics": dict(self.metrics),
            "reasons": list(self.reasons),
            "evidence_refs": [dict(item) for item in self.evidence_refs],
        }


def evaluate_shadow_evidence(
    evidence: Sequence[Mapping[str, Any]],
    *,
    minimum_samples: int = MIN_SHADOW_EVIDENCE,
) -> ShadowEvidenceGateResult:
    """Check that D6 contains distinct, grounded, clean shadow comparisons.

    Evidence is treated as an observation, never as a new authority source.
    Malformed or unsafe records make the gate ineligible instead of being
    silently ignored.
    """

    reasons: list[str] = []
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        return ShadowEvidenceGateResult(False, 0, 0, {}, ("invalid_shadow_evidence",))
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples <= 0:
        raise ValueError("minimum_samples must be a positive integer")
    if len(evidence) > MAX_SHADOW_EVIDENCE:
        return ShadowEvidenceGateResult(False, len(evidence), 0, {}, ("shadow_evidence_limit",))

    identities: set[tuple[str, str]] = set()
    valid_count = 0
    agreement_count = 0
    grounded_count = 0
    clean_count = 0
    refs: list[Mapping[str, Any]] = []
    for item in evidence:
        if not isinstance(item, Mapping) or _contains_forbidden(item):
            reasons.append("invalid_shadow_evidence")
            continue
        source = _mapping(item.get("source"))
        authority = _mapping(item.get("authority"))
        comparison = _mapping(item.get("comparison"))
        task_id = _text(source.get("task_id") if source else None, "shadow.task_id", maximum=101)
        attempt_id = _text(source.get("attempt_id") if source else None, "shadow.attempt_id", maximum=101)
        if task_id is None or attempt_id is None:
            reasons.append("invalid_shadow_identity")
            continue
        identity = (task_id, attempt_id)
        if identity in identities:
            reasons.append("duplicate_shadow_identity")
            continue
        identities.add(identity)
        if (
            item.get("status") != "PROPOSAL_ONLY_VERIFIED"
            or authority is None
            or authority.get("reviewer_mode") != "shadow"
            or authority.get("proposal_only") is not True
            or item.get("raw_worker_conversation_recorded") is not False
            or comparison is None
        ):
            reasons.append("invalid_shadow_evidence")
            continue
        valid_count += 1
        agreement = comparison.get("agreement") is True
        grounded = comparison.get("evidence_quality") == "grounded"
        clean = all(comparison.get(key) is False for key in (
            "false_approve",
            "false_reject",
            "missed_issue",
            "unnecessary_rework",
        ))
        agreement_count += int(agreement)
        grounded_count += int(grounded)
        clean_count += int(clean)
        if not (agreement and grounded and clean):
            reasons.append("shadow_comparison_not_clean")
        refs.append({"task_id": task_id, "attempt_id": attempt_id})

    if len(identities) < minimum_samples:
        reasons.append("minimum_shadow_evidence")
    if valid_count < minimum_samples:
        reasons.append("valid_shadow_evidence_insufficient")
    metrics = {
        "sample_count": len(evidence),
        "valid_sample_count": valid_count,
        "distinct_task_count": len({task_id for task_id, _ in identities}),
        "agreement": agreement_count,
        "grounded": grounded_count,
        "clean": clean_count,
    }
    normalized_reasons = tuple(dict.fromkeys(reasons))
    return ShadowEvidenceGateResult(
        eligible=not normalized_reasons,
        sample_count=len(evidence),
        distinct_task_count=metrics["distinct_task_count"],
        metrics=metrics,
        reasons=normalized_reasons,
        evidence_refs=tuple(refs),
    )


@dataclass(frozen=True)
class CodexLessEvaluation:
    """A routine-cycle candidate; never an integration approval."""

    eligible: bool
    status: str
    codex_review_required: bool
    integration_authority: str
    official_branch_auto_merge: bool
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "codex_review_required": self.codex_review_required,
            "integration_authority": self.integration_authority,
            "official_branch_auto_merge": self.official_branch_auto_merge,
            "reasons": list(self.reasons),
            "evidence_refs": [dict(item) for item in self.evidence_refs],
        }


def _rejected(reasons: Sequence[str]) -> CodexLessEvaluation:
    return CodexLessEvaluation(
        eligible=False,
        status="REJECTED",
        codex_review_required=True,
        integration_authority="codex_and_host",
        official_branch_auto_merge=False,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _classification(
    task: Mapping[str, Any],
    manifest: Mapping[str, Any],
    name: str,
    *,
    default: str | None = None,
) -> str | None:
    value = task.get(name, manifest.get(name, default))
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _safe_changed_paths(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if not value or len(value) > MAX_CODEX_LESS_CHANGED_FILES:
        return None
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        path = item.strip().replace("\\", "/")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            return None
        normalized_path = str(parsed)
        if normalized_path in normalized:
            continue
        normalized.append(normalized_path)
    return tuple(normalized) if normalized else None


class CodexLessPolicy:
    """Evaluate whether one verified routine task may omit Codex review."""

    def evaluate(
        self,
        *,
        task: Mapping[str, Any],
        manifest: Mapping[str, Any],
        packet: Mapping[str, Any],
        proposal: ReviewProposal | Mapping[str, Any],
        shadow_evidence: Sequence[Mapping[str, Any]],
    ) -> CodexLessEvaluation:
        if not all(isinstance(value, Mapping) for value in (task, manifest, packet)):
            return _rejected(("invalid_candidate_input",))
        if _contains_forbidden(task) or _contains_forbidden(manifest) or _contains_forbidden(packet):
            return _rejected(("raw_output_not_allowed",))

        shadow_gate = evaluate_shadow_evidence(shadow_evidence)
        reasons = list(shadow_gate.reasons)
        if not shadow_gate.eligible:
            return _rejected(reasons)

        task_id = _text(task.get("task_id"), "task.task_id", maximum=101)
        packet_task_id = _text(packet.get("task_id"), "packet.task_id", maximum=101)
        attempt_id = _text(packet.get("attempt_id"), "packet.attempt_id", maximum=101)
        if task_id is None or packet_task_id != task_id or attempt_id is None:
            reasons.append("task_attempt_mismatch")

        if task.get("owner") != "worker" or task.get("worker_candidate") is not True:
            reasons.append("worker_ownership_required")
        if task.get("status") != "HOST_VERIFIED" or packet.get("status") != "HOST_VERIFIED":
            reasons.append("host_verified_status_required")

        risk = _classification(task, manifest, "risk", default="normal")
        if risk not in CODEX_LESS_RISKS:
            reasons.append("risk_not_allowed")
        sensitivity = _classification(task, manifest, "sensitivity", default="normal")
        if sensitivity not in CODEX_LESS_SENSITIVITIES:
            reasons.append("sensitivity_not_allowed")
        task_type = _classification(task, manifest, "task_type")
        if task_type not in KNOWN_CODEX_LESS_TASK_TYPES:
            reasons.append("task_type_not_known")

        ownership = task.get("ownership")
        allowed_files = manifest.get("allowed_files")
        forbidden_files = manifest.get("forbidden_files", [])
        changed_files = _safe_changed_paths(packet.get("changed_files"))
        if not isinstance(ownership, Sequence) or isinstance(ownership, (str, bytes)):
            reasons.append("ownership_missing")
            ownership = ()
        if not isinstance(allowed_files, Sequence) or isinstance(allowed_files, (str, bytes)):
            reasons.append("manifest_allowed_files_missing")
            allowed_files = ()
        if not isinstance(forbidden_files, Sequence) or isinstance(forbidden_files, (str, bytes)):
            reasons.append("manifest_forbidden_files_invalid")
            forbidden_files = ()
        if changed_files is None:
            reasons.append("changed_files_invalid")
            changed_files = ()
        ownership_set = {str(item).replace("\\", "/") for item in ownership}
        allowed_set = {str(item).replace("\\", "/") for item in allowed_files}
        forbidden_set = {str(item).replace("\\", "/") for item in forbidden_files}
        for path in changed_files:
            if path not in ownership_set:
                reasons.append("changed_file_outside_ownership")
            if path not in allowed_set:
                reasons.append("changed_file_outside_manifest")
            if path in forbidden_set or is_protected_path(path):
                reasons.append("protected_path")

        summary = _mapping(packet.get("verification_summary"))
        if summary is None or not all(
            summary.get(key) is True
            for key in ("host_verified", "host_tests_passed", "independent_verification", "result_accepted")
        ):
            reasons.append("host_verification_incomplete")
        trust_level = summary.get("verification_trust_level") if summary else None
        if trust_level not in _CODEX_LESS_TRUST_LEVELS:
            reasons.append("verification_trust_not_allowed")
        if trust_level == "TRUSTED_HOST_EXEC" and summary.get("operator_approved") is not True:
            reasons.append("operator_approval_required")

        normalized_proposal: ReviewProposal | None
        if isinstance(proposal, ReviewProposal):
            normalized_proposal = proposal
        else:
            try:
                normalized_proposal = ReviewProposal.from_dict(proposal)
            except (ReviewAdapterError, TypeError, ValueError):
                normalized_proposal = None
                reasons.append("review_proposal_invalid")
        if normalized_proposal is not None:
            if normalized_proposal.task_id != task_id or normalized_proposal.attempt_id != attempt_id:
                reasons.append("review_proposal_mismatch")
            if normalized_proposal.decision != "APPROVE_INTEGRATION":
                reasons.append("review_proposal_not_approved")
            if normalized_proposal.findings or normalized_proposal.required_correction is not None:
                reasons.append("review_proposal_has_findings")
            packet_refs = {
                _reference_key(item)
                for item in packet.get("artifact_refs", [])
                if isinstance(item, Mapping)
            }
            proposal_refs = {
                _reference_key(item)
                for item in normalized_proposal.evidence_refs
            }
            if not proposal_refs or not proposal_refs.issubset(packet_refs):
                reasons.append("review_evidence_not_grounded")

        normalized_reasons = tuple(dict.fromkeys(reasons))
        if normalized_reasons:
            return _rejected(normalized_reasons)
        return CodexLessEvaluation(
            eligible=True,
            status="CANDIDATE",
            codex_review_required=False,
            integration_authority="host_policy",
            official_branch_auto_merge=False,
            reasons=("d6_shadow_gate_satisfied", "host_verification_passed", "review_proposal_approved"),
            evidence_refs=shadow_gate.evidence_refs,
        )


__all__ = [
    "CODEX_LESS_RISKS",
    "CODEX_LESS_SENSITIVITIES",
    "CodexLessEvaluation",
    "CodexLessPolicy",
    "KNOWN_CODEX_LESS_TASK_TYPES",
    "MAX_CODEX_LESS_CHANGED_FILES",
    "MIN_SHADOW_EVIDENCE",
    "ShadowEvidenceGateResult",
    "evaluate_shadow_evidence",
]
