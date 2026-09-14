"""Bounded, evidence-backed F0-F2 self-improvement contracts.

The records in this module describe what the Host observed, how that evidence
was interpreted, and a possible improvement plan.  They are intentionally
pure data contracts: they do not call a model, dispatch a Task, alter policy,
write a Commander plan, or grant integration authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from typing import Any
from uuid import uuid4

from ..security.audit import AuditRecorder


MAX_ID_CHARS = 128
MAX_SUBJECT_CHARS = 256
MAX_SOURCE_CHARS = 128
MAX_TIMESTAMP_CHARS = 80
MAX_METRICS = 32
MAX_METRIC_KEY_CHARS = 64
MAX_METRIC_STRING_CHARS = 256
MAX_REFERENCES = 32
MAX_REFERENCE_CHARS = 4_000
MAX_LIST_ITEMS = 16
MAX_ITEM_CHARS = 1_000
MAX_OBJECTIVE_CHARS = 2_000

OBSERVATION_STATUSES = frozenset({"OBSERVED", "DEGRADED", "FAILED", "UNKNOWN"})
DIAGNOSIS_CATEGORIES = frozenset(
    {"quality", "reliability", "availability", "verification", "policy", "security", "cost", "unknown"}
)
SEVERITIES = frozenset({"low", "normal", "high", "critical"})
CONFIDENCES = frozenset({"low", "medium", "high"})
PLAN_RISKS = frozenset({"low", "normal", "high", "critical"})

_FORBIDDEN_KEYS = frozenset(
    {
        "raw_output",
        "conversation",
        "stdout",
        "stderr",
        "patch",
        "token",
        "api_key",
        "credentials",
        "private_key",
    }
)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        if any(str(key).lower() in _FORBIDDEN_KEYS for key in value):
            return True
        return any(_contains_forbidden(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    if any(pattern.search(normalized) for pattern in AuditRecorder.SECRET_PATTERNS):
        raise ValueError(f"{name} contains secret material")
    return normalized


def _bounded_choice(value: Any, name: str, choices: frozenset[str]) -> str:
    normalized = _text(value, name, maximum=64).lower()
    if normalized not in {choice.lower() for choice in choices}:
        raise ValueError(f"unsupported {name}: {normalized}")
    return normalized


def _bounded_list(value: Any, name: str, *, maximum: int = MAX_LIST_ITEMS) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    if len(value) > maximum:
        raise ValueError(f"{name} contains too many items")
    result: list[str] = []
    for item in value:
        normalized = _text(item, f"{name}[]", maximum=MAX_ITEM_CHARS)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if _contains_forbidden(value):
        raise ValueError(f"{name} contains raw output or secret material")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-safe") from exc
    if len(encoded) > MAX_REFERENCE_CHARS:
        raise ValueError(f"{name} is too large")
    return dict(value)


def _references(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    if not value or len(value) > MAX_REFERENCES:
        raise ValueError(f"{name} must contain between 1 and {MAX_REFERENCES} items")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        reference = _json_object(item, f"{name}[]")
        if not isinstance(reference.get("kind"), str) or not reference["kind"].strip():
            raise ValueError(f"{name}[] must have a kind")
        if not any(reference.get(key) for key in ("path", "uri", "id", "sha256")):
            raise ValueError(f"{name}[] must identify an evidence artifact")
        identity = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if identity not in seen:
            seen.add(identity)
            result.append(reference)
    return tuple(result)


def _reference_keys(value: Sequence[Mapping[str, Any]]) -> set[str]:
    return {json.dumps(dict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in value}


def _metrics(value: Any) -> dict[str, bool | float | int | str]:
    if not isinstance(value, Mapping):
        raise ValueError("metrics must be an object")
    if _contains_forbidden(value):
        raise ValueError("metrics contains raw output or secret material")
    if len(value) > MAX_METRICS:
        raise ValueError("metrics contains too many items")
    result: dict[str, bool | float | int | str] = {}
    for key, item in value.items():
        normalized_key = _text(key, "metrics key", maximum=MAX_METRIC_KEY_CHARS)
        if isinstance(item, bool):
            result[normalized_key] = item
        elif isinstance(item, int):
            result[normalized_key] = item
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("metrics numeric values must be finite")
            result[normalized_key] = item
        elif isinstance(item, str):
            result[normalized_key] = _text(item, f"metrics.{normalized_key}", maximum=MAX_METRIC_STRING_CHARS)
        else:
            raise ValueError("metrics values must be scalar")
    return result


def _timestamp(value: Any) -> str:
    normalized = _text(value, "observed_at", maximum=MAX_TIMESTAMP_CHARS)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return normalized


@dataclass(frozen=True)
class ObservationRecord:
    """A bounded F0 Host observation with artifact references only."""

    subject: str
    source: str
    observed_at: str
    status: str
    metrics: Mapping[str, bool | float | int | str]
    evidence_refs: tuple[Mapping[str, Any], ...]
    observation_id: str = field(default_factory=lambda: f"observation-{uuid4().hex}")

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _text(self.observation_id, "observation_id", maximum=MAX_ID_CHARS))
        object.__setattr__(self, "subject", _text(self.subject, "subject", maximum=MAX_SUBJECT_CHARS))
        object.__setattr__(self, "source", _text(self.source, "source", maximum=MAX_SOURCE_CHARS))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at))
        object.__setattr__(self, "status", _bounded_choice(self.status, "status", OBSERVATION_STATUSES).upper())
        object.__setattr__(self, "metrics", _metrics(self.metrics))
        object.__setattr__(self, "evidence_refs", _references(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "subject": self.subject,
            "source": self.source,
            "observed_at": self.observed_at,
            "status": self.status,
            "metrics": dict(self.metrics),
            "evidence_refs": [dict(item) for item in self.evidence_refs],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationRecord":
        if not isinstance(value, Mapping):
            raise ValueError("observation must be an object")
        allowed = {"observation_id", "subject", "source", "observed_at", "status", "metrics", "evidence_refs"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown observation field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid observation: {exc}") from exc


@dataclass(frozen=True)
class ImprovementDiagnosis:
    """An F1 interpretation explicitly grounded in one observation."""

    observation_id: str
    category: str
    severity: str
    confidence: str
    evidence_refs: tuple[Mapping[str, Any], ...]
    causes: tuple[str, ...] = ()
    recommended_focus: tuple[str, ...] = ()
    diagnosis_id: str = field(default_factory=lambda: f"diagnosis-{uuid4().hex}")
    status: str = "PROPOSAL_ONLY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnosis_id", _text(self.diagnosis_id, "diagnosis_id", maximum=MAX_ID_CHARS))
        object.__setattr__(self, "observation_id", _text(self.observation_id, "observation_id", maximum=MAX_ID_CHARS))
        object.__setattr__(self, "category", _bounded_choice(self.category, "category", DIAGNOSIS_CATEGORIES))
        object.__setattr__(self, "severity", _bounded_choice(self.severity, "severity", SEVERITIES))
        object.__setattr__(self, "confidence", _bounded_choice(self.confidence, "confidence", CONFIDENCES))
        if self.status != "PROPOSAL_ONLY":
            raise ValueError("diagnosis status must remain PROPOSAL_ONLY")
        object.__setattr__(self, "evidence_refs", _references(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "causes", _bounded_list(self.causes, "causes"))
        object.__setattr__(self, "recommended_focus", _bounded_list(self.recommended_focus, "recommended_focus"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "observation_id": self.observation_id,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence,
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "causes": list(self.causes),
            "recommended_focus": list(self.recommended_focus),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImprovementDiagnosis":
        if not isinstance(value, Mapping):
            raise ValueError("diagnosis must be an object")
        allowed = {
            "diagnosis_id",
            "observation_id",
            "category",
            "severity",
            "confidence",
            "evidence_refs",
            "causes",
            "recommended_focus",
            "status",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown diagnosis field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid diagnosis: {exc}") from exc


def diagnose_observation(
    observation: ObservationRecord,
    *,
    category: str,
    severity: str,
    confidence: str,
    causes: Sequence[str] = (),
    recommended_focus: Sequence[str] = (),
    evidence_refs: Sequence[Mapping[str, Any]] | None = None,
    diagnosis_id: str | None = None,
) -> ImprovementDiagnosis:
    """Create a diagnosis whose references are a subset of the observation."""

    if not isinstance(observation, ObservationRecord):
        raise TypeError("observation must be an ObservationRecord")
    refs = observation.evidence_refs if evidence_refs is None else _references(evidence_refs, "evidence_refs")
    if not _reference_keys(refs).issubset(_reference_keys(observation.evidence_refs)):
        raise ValueError("diagnosis evidence must be grounded in the observation")
    return ImprovementDiagnosis(
        observation_id=observation.observation_id,
        category=category,
        severity=severity,
        confidence=confidence,
        evidence_refs=refs,
        causes=tuple(causes),
        recommended_focus=tuple(recommended_focus),
        diagnosis_id=diagnosis_id or f"diagnosis-{uuid4().hex}",
    )


@dataclass(frozen=True)
class ImprovementPlanProposal:
    """An F2 plan proposal; dispatch and integration are intentionally absent."""

    observation_ids: tuple[str, ...]
    diagnosis_ids: tuple[str, ...]
    objective: str
    steps: tuple[str, ...]
    acceptance: tuple[str, ...]
    exclusions: tuple[str, ...]
    risk: str
    evidence_refs: tuple[Mapping[str, Any], ...]
    plan_id: str = field(default_factory=lambda: f"improvement-plan-{uuid4().hex}")
    status: str = "PROPOSAL_ONLY"
    requires_human_approval: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id", maximum=MAX_ID_CHARS))
        if self.status != "PROPOSAL_ONLY":
            raise ValueError("improvement plan status must remain PROPOSAL_ONLY")
        if self.requires_human_approval is not True:
            raise ValueError("improvement plans always require human approval")
        object.__setattr__(self, "observation_ids", _identifiers(self.observation_ids, "observation_ids"))
        object.__setattr__(self, "diagnosis_ids", _identifiers(self.diagnosis_ids, "diagnosis_ids"))
        object.__setattr__(self, "objective", _text(self.objective, "objective", maximum=MAX_OBJECTIVE_CHARS))
        object.__setattr__(self, "steps", _bounded_list(self.steps, "steps"))
        object.__setattr__(self, "acceptance", _bounded_list(self.acceptance, "acceptance"))
        object.__setattr__(self, "exclusions", _bounded_list(self.exclusions, "exclusions"))
        object.__setattr__(self, "risk", _bounded_choice(self.risk, "risk", PLAN_RISKS))
        object.__setattr__(self, "evidence_refs", _references(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "observation_ids": list(self.observation_ids),
            "diagnosis_ids": list(self.diagnosis_ids),
            "objective": self.objective,
            "steps": list(self.steps),
            "acceptance": list(self.acceptance),
            "exclusions": list(self.exclusions),
            "risk": self.risk,
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "status": self.status,
            "requires_human_approval": self.requires_human_approval,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImprovementPlanProposal":
        if not isinstance(value, Mapping):
            raise ValueError("improvement plan must be an object")
        allowed = {
            "plan_id",
            "observation_ids",
            "diagnosis_ids",
            "objective",
            "steps",
            "acceptance",
            "exclusions",
            "risk",
            "evidence_refs",
            "status",
            "requires_human_approval",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown improvement plan field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid improvement plan: {exc}") from exc


def _identifiers(value: Any, name: str) -> tuple[str, ...]:
    result = _bounded_list(value, name)
    return tuple(_text(item, f"{name}[]", maximum=MAX_ID_CHARS) for item in result)


def propose_improvement(
    *,
    observations: Sequence[ObservationRecord],
    diagnoses: Sequence[ImprovementDiagnosis],
    objective: str,
    steps: Sequence[str],
    acceptance: Sequence[str],
    exclusions: Sequence[str],
    risk: str,
    plan_id: str | None = None,
) -> ImprovementPlanProposal:
    """Build an F2 proposal from grounded F0/F1 records without dispatching it."""

    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence) or not observations:
        raise ValueError("observations must be a non-empty sequence")
    if isinstance(diagnoses, (str, bytes)) or not isinstance(diagnoses, Sequence) or not diagnoses:
        raise ValueError("diagnoses must be a non-empty sequence")
    if any(not isinstance(item, ObservationRecord) for item in observations):
        raise TypeError("observations must contain ObservationRecord values")
    if any(not isinstance(item, ImprovementDiagnosis) for item in diagnoses):
        raise TypeError("diagnoses must contain ImprovementDiagnosis values")
    observation_ids = tuple(item.observation_id for item in observations)
    diagnosis_ids = tuple(item.diagnosis_id for item in diagnoses)
    if len(set(observation_ids)) != len(observation_ids):
        raise ValueError("observation ids must be unique")
    if len(set(diagnosis_ids)) != len(diagnosis_ids):
        raise ValueError("diagnosis ids must be unique")
    if any(item.observation_id not in observation_ids for item in diagnoses):
        raise ValueError("diagnosis must refer to a supplied observation")
    observation_refs: list[Mapping[str, Any]] = []
    for observation in observations:
        for reference in observation.evidence_refs:
            if _reference_keys((reference,)).isdisjoint(_reference_keys(observation_refs)):
                observation_refs.append(reference)
    diagnosis_ref_keys = set().union(*(_reference_keys(item.evidence_refs) for item in diagnoses))
    if not diagnosis_ref_keys.issubset(_reference_keys(observation_refs)):
        raise ValueError("diagnosis evidence must be grounded in supplied observations")
    return ImprovementPlanProposal(
        observation_ids=observation_ids,
        diagnosis_ids=diagnosis_ids,
        objective=objective,
        steps=tuple(steps),
        acceptance=tuple(acceptance),
        exclusions=tuple(exclusions),
        risk=risk,
        evidence_refs=tuple(observation_refs),
        plan_id=plan_id or f"improvement-plan-{uuid4().hex}",
    )


__all__ = [
    "CONFIDENCES",
    "DIAGNOSIS_CATEGORIES",
    "ImprovementDiagnosis",
    "ImprovementPlanProposal",
    "OBSERVATION_STATUSES",
    "ObservationRecord",
    "diagnose_observation",
    "propose_improvement",
]
