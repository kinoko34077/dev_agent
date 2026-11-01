"""Pure, staged selection of isolated Worker proposal candidates.

This module is a policy boundary only.  It does not dispatch a Provider, run
Host Verification, create a Task, mutate a manifest, or grant integration
authority.  The existing Commander/Supervisor/Host boundaries remain the
owners of those actions.  A candidate is only an immutable observation of an
already-created, side-effect-free proposal artifact.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class CandidateSelectionError(ValueError):
    """A candidate observation or staged selection request is unsafe."""


STAGED_CANDIDATE_STAGES = (
    "parser_schema",
    "patch_applicability",
    "deterministic_tests",
    "host_verification",
    "review_integration",
)
_STAGE_SET = frozenset(STAGED_CANDIDATE_STAGES)
_STAGE_STATUSES = frozenset({"pass", "fail", "not_run"})
_MAX_ID_LENGTH = 256
_MAX_REASON_LENGTH = 160
_MAX_CANDIDATES = 32


def _text(value: Any, name: str, *, maximum: int = _MAX_ID_LENGTH) -> str:
    if not isinstance(value, str):
        raise CandidateSelectionError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise CandidateSelectionError(f"{name} must be non-empty text of at most {maximum} characters")
    return normalized


def _optional_text(value: Any, name: str, *, maximum: int = _MAX_REASON_LENGTH) -> str | None:
    if value is None:
        return None
    return _text(value, name, maximum=maximum)


def _nonnegative_integer(value: Any, name: str, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise CandidateSelectionError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class CandidateStage:
    """One Host-observed result in the fixed candidate gate order."""

    stage: str
    status: str
    evidence_ref: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        stage = _text(self.stage, "stage", maximum=64)
        status = _text(self.status, "status", maximum=32).lower()
        if stage not in _STAGE_SET:
            raise CandidateSelectionError(f"unsupported candidate stage: {stage}")
        if status not in _STAGE_STATUSES:
            raise CandidateSelectionError(f"unsupported candidate stage status: {status}")
        evidence_ref = _optional_text(self.evidence_ref, "evidence_ref")
        reason_code = _optional_text(self.reason_code, "reason_code")
        if status == "pass" and reason_code is not None:
            raise CandidateSelectionError("passing candidate stage cannot carry a failure reason")
        if status == "fail" and reason_code is None:
            raise CandidateSelectionError("failed candidate stage requires a bounded reason_code")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_ref", evidence_ref)
        object.__setattr__(self, "reason_code", reason_code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class WorkerProposalCandidate:
    """An isolated, side-effect-free proposal observation.

    ``scope_key`` may be shared by candidates for the same logical task, but
    only one candidate can reach integration.  The artifacts, attempts, and
    Host evidence remain distinct, allowing comparison without applying
    overlapping edits.  External effects are rejected at construction time;
    a proposal candidate is never an effect-intent replay vehicle.
    """

    logical_task_id: str
    candidate_id: str
    attempt_id: str
    artifact_ref: str
    source_revision: str
    scope_key: str
    provider_binding_id: str
    model_id: str
    prompt_variant: str
    context_variant: str
    stages: tuple[CandidateStage, ...]
    resource_units: int = 1
    external_effect_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "logical_task_id",
            "candidate_id",
            "attempt_id",
            "artifact_ref",
            "source_revision",
            "scope_key",
            "provider_binding_id",
            "model_id",
            "prompt_variant",
            "context_variant",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.stages, tuple) or not self.stages:
            raise CandidateSelectionError("candidate stages must be a non-empty tuple")
        if any(not isinstance(item, CandidateStage) for item in self.stages):
            raise CandidateSelectionError("candidate stages must contain CandidateStage values")
        stage_names = tuple(item.stage for item in self.stages)
        if len(set(stage_names)) != len(stage_names):
            raise CandidateSelectionError("candidate stages must be unique")
        positions = [STAGED_CANDIDATE_STAGES.index(item) for item in stage_names]
        if positions != sorted(positions):
            raise CandidateSelectionError("candidate stages must follow the fixed staged order")
        if self.external_effect_key is not None:
            raise CandidateSelectionError("candidate proposals cannot carry an external effect key")
        object.__setattr__(self, "resource_units", _nonnegative_integer(self.resource_units, "resource_units", minimum=1))

    @property
    def independence_key(self) -> tuple[str, str, str, str]:
        """Return the bounded model/prompt/context identity used for diagnostics."""

        return (
            self.provider_binding_id,
            self.model_id,
            self.prompt_variant,
            self.context_variant,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_task_id": self.logical_task_id,
            "candidate_id": self.candidate_id,
            "attempt_id": self.attempt_id,
            "artifact_ref": self.artifact_ref,
            "source_revision": self.source_revision,
            "scope_key": self.scope_key,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "prompt_variant": self.prompt_variant,
            "context_variant": self.context_variant,
            "stages": [stage.to_dict() for stage in self.stages],
            "resource_units": self.resource_units,
            "external_effect_key": self.external_effect_key,
        }


@dataclass(frozen=True)
class CandidateSelection:
    """Bounded read-only result of staged candidate screening."""

    logical_task_id: str
    accepted_candidate_id: str | None
    considered_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    failure_signatures: tuple[str, ...]
    correlated_failure_signatures: tuple[str, ...]
    stop_reason: str
    resource_units_used: int
    stage_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_task_id", _text(self.logical_task_id, "logical_task_id"))
        object.__setattr__(self, "accepted_candidate_id", _optional_text(self.accepted_candidate_id, "accepted_candidate_id"))
        object.__setattr__(self, "considered_candidate_ids", tuple(_text(item, "considered_candidate_id") for item in self.considered_candidate_ids))
        object.__setattr__(self, "rejected_candidate_ids", tuple(_text(item, "rejected_candidate_id") for item in self.rejected_candidate_ids))
        object.__setattr__(self, "failure_signatures", tuple(_text(item, "failure_signature", maximum=320) for item in self.failure_signatures))
        object.__setattr__(self, "correlated_failure_signatures", tuple(_text(item, "correlated_failure_signature", maximum=320) for item in self.correlated_failure_signatures))
        object.__setattr__(self, "stop_reason", _text(self.stop_reason, "stop_reason", maximum=64))
        object.__setattr__(self, "resource_units_used", _nonnegative_integer(self.resource_units_used, "resource_units_used"))
        normalized_counts: dict[str, int] = {}
        for key, value in self.stage_counts.items():
            if key not in _STAGE_SET:
                raise CandidateSelectionError("stage_counts contains an unsupported stage")
            normalized_counts[key] = _nonnegative_integer(value, f"stage_counts[{key}]")
        for stage in STAGED_CANDIDATE_STAGES:
            normalized_counts.setdefault(stage, 0)
        object.__setattr__(self, "stage_counts", normalized_counts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_task_id": self.logical_task_id,
            "accepted_candidate_id": self.accepted_candidate_id,
            "considered_candidate_ids": list(self.considered_candidate_ids),
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "failure_signatures": list(self.failure_signatures),
            "correlated_failure_signatures": list(self.correlated_failure_signatures),
            "stop_reason": self.stop_reason,
            "resource_units_used": self.resource_units_used,
            "stage_counts": dict(self.stage_counts),
        }


def _validate_candidate_set(
    candidates: Sequence[WorkerProposalCandidate],
    *,
    logical_task_id: str,
) -> tuple[WorkerProposalCandidate, ...]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise CandidateSelectionError("candidates must be a sequence")
    if len(candidates) > _MAX_CANDIDATES:
        raise CandidateSelectionError(f"candidate count exceeds {_MAX_CANDIDATES}")
    expected_task = _text(logical_task_id, "logical_task_id")
    normalized = tuple(candidates)
    if any(not isinstance(candidate, WorkerProposalCandidate) for candidate in normalized):
        raise CandidateSelectionError("candidates must contain WorkerProposalCandidate values")
    if any(candidate.logical_task_id != expected_task for candidate in normalized):
        raise CandidateSelectionError("all candidates must share logical_task_id")
    for field in ("candidate_id", "attempt_id", "artifact_ref"):
        values = [getattr(candidate, field) for candidate in normalized]
        if len(set(values)) != len(values):
            raise CandidateSelectionError(f"candidate {field} values must be unique")
    return tuple(sorted(normalized, key=lambda item: item.candidate_id))


def _stage_map(candidate: WorkerProposalCandidate) -> dict[str, CandidateStage]:
    return {stage.stage: stage for stage in candidate.stages}


def select_staged_candidates(
    candidates: Sequence[WorkerProposalCandidate],
    *,
    logical_task_id: str,
    max_candidates: int = 4,
    max_resource_units: int = 8,
) -> CandidateSelection:
    """Screen isolated candidates in cheap-to-expensive fixed stages.

    The function consumes only observations already available to the Host.
    Missing stages are treated as ``not_run`` and therefore cannot be selected.
    Once a candidate passes the final review/integration observation, the
    selector stops; it never asks another candidate to perform a duplicate
    integration or external effect.
    """

    candidate_limit = _nonnegative_integer(max_candidates, "max_candidates", minimum=1, maximum=_MAX_CANDIDATES)
    resource_limit = _nonnegative_integer(max_resource_units, "max_resource_units", minimum=1)
    normalized = _validate_candidate_set(candidates, logical_task_id=logical_task_id)
    normalized = normalized[:candidate_limit]
    considered: list[str] = []
    rejected: list[str] = []
    failure_signatures: list[str] = []
    stage_counts = {stage: 0 for stage in STAGED_CANDIDATE_STAGES}
    resource_used = 0
    accepted: str | None = None
    stop_reason = "candidates_exhausted"

    for candidate in normalized:
        if resource_used + candidate.resource_units > resource_limit:
            stop_reason = "resource_cap"
            break
        considered.append(candidate.candidate_id)
        resource_used += candidate.resource_units
        stages = _stage_map(candidate)
        candidate_passed = True
        for stage_name in STAGED_CANDIDATE_STAGES:
            observation = stages.get(stage_name)
            if observation is None or observation.status != "pass":
                candidate_passed = False
                if observation is None:
                    failure_signatures.append(f"{stage_name}:NOT_OBSERVED")
                elif observation.status == "fail":
                    failure_signatures.append(f"{stage_name}:{observation.reason_code}")
                else:
                    failure_signatures.append(f"{stage_name}:NOT_RUN")
                rejected.append(candidate.candidate_id)
                break
            stage_counts[stage_name] += 1
        if candidate_passed:
            accepted = candidate.candidate_id
            stop_reason = "accepted"
            break

    correlated = tuple(
        signature
        for signature, count in Counter(failure_signatures).items()
        if count > 1
    )
    return CandidateSelection(
        logical_task_id=logical_task_id,
        accepted_candidate_id=accepted,
        considered_candidate_ids=tuple(considered),
        rejected_candidate_ids=tuple(rejected),
        failure_signatures=tuple(failure_signatures),
        correlated_failure_signatures=correlated,
        stop_reason=stop_reason,
        resource_units_used=resource_used,
        stage_counts=stage_counts,
    )


__all__ = [
    "CandidateSelection",
    "CandidateSelectionError",
    "CandidateStage",
    "STAGED_CANDIDATE_STAGES",
    "WorkerProposalCandidate",
    "select_staged_candidates",
]
