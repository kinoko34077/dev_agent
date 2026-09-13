"""External benchmark snapshots and data-defined intelligence tiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from types import MappingProxyType
from typing import Any, Mapping

from .model_catalog import ModelCatalogError


CANONICAL_TASK_FITS = frozenset({"planning", "coding", "review", "writing"})
_CONFIDENCE = frozenset({"low", "medium", "high"})
_TIERS = ("L1", "L2", "L3")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError(f"{name} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ModelCatalogError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelCatalogError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _score(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
        raise ModelCatalogError(f"{name} must be a finite score from 0 to 100")
    return float(value)


@dataclass(frozen=True)
class BenchmarkScore:
    canonical_model_id: str
    benchmark: str
    benchmark_version: str
    model_version: str
    source: str
    observed_at: str
    expires_at: str
    raw_score: float
    normalized_score: float
    confidence: str
    task_fit: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in (
            "canonical_model_id",
            "benchmark",
            "benchmark_version",
            "model_version",
            "source",
            "observed_at",
            "expires_at",
            "confidence",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        observed = _timestamp(self.observed_at, "observed_at")
        expires = _timestamp(self.expires_at, "expires_at")
        if expires <= observed:
            raise ModelCatalogError("expires_at must be after observed_at")
        object.__setattr__(self, "raw_score", _score(self.raw_score, "raw_score"))
        object.__setattr__(self, "normalized_score", _score(self.normalized_score, "normalized_score"))
        confidence = self.confidence.lower()
        if confidence not in _CONFIDENCE:
            raise ModelCatalogError("benchmark confidence is invalid")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.task_fit, Mapping):
            raise ModelCatalogError("task_fit must be an object")
        normalized: dict[str, float] = {}
        for task, score in self.task_fit.items():
            task_name = _text(task, "task_fit key")
            if task_name not in CANONICAL_TASK_FITS:
                raise ModelCatalogError(f"unknown task_fit: {task_name}")
            normalized[task_name] = _score(score, f"task_fit.{task_name}")
        if not normalized:
            raise ModelCatalogError("task_fit must not be empty")
        object.__setattr__(self, "task_fit", MappingProxyType(normalized))

    def is_current(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ModelCatalogError("now must include a timezone")
        current = current.astimezone(timezone.utc)
        return _timestamp(self.observed_at, "observed_at") <= current < _timestamp(self.expires_at, "expires_at")

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """A source-qualified observation identity, not a model identity."""
        return (self.canonical_model_id, self.benchmark, self.benchmark_version, self.source)


@dataclass(frozen=True)
class IntelligenceScore:
    """Current aggregate of independently normalized benchmark observations."""

    canonical_model_id: str
    normalized_score: float
    task_fit: Mapping[str, float]
    confidence: str
    source_count: int
    sources: tuple[BenchmarkScore, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_model_id", _text(self.canonical_model_id, "canonical_model_id"))
        object.__setattr__(self, "normalized_score", _score(self.normalized_score, "normalized_score"))
        if self.confidence not in _CONFIDENCE:
            raise ModelCatalogError("intelligence score confidence is invalid")
        if isinstance(self.source_count, bool) or not isinstance(self.source_count, int) or self.source_count < 1:
            raise ModelCatalogError("intelligence score source_count must be positive")
        if self.source_count != len(self.sources):
            raise ModelCatalogError("intelligence score source_count must match sources")
        normalized_task_fit: dict[str, float] = {}
        for task, value in self.task_fit.items():
            task_name = _text(task, "task_fit key")
            if task_name not in CANONICAL_TASK_FITS:
                raise ModelCatalogError(f"unknown task_fit: {task_name}")
            normalized_task_fit[task_name] = _score(value, f"task_fit.{task_name}")
        if not normalized_task_fit:
            raise ModelCatalogError("intelligence score task_fit must not be empty")
        object.__setattr__(self, "task_fit", MappingProxyType(normalized_task_fit))


@dataclass(frozen=True)
class BenchmarkCatalog:
    _scores_by_model: Mapping[str, tuple[BenchmarkScore, ...]]
    tier_thresholds: Mapping[str, float]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "BenchmarkCatalog":
        if not isinstance(document, Mapping):
            raise ModelCatalogError("benchmark catalog must be an object")
        if document.get("schema_version") != 1:
            raise ModelCatalogError("benchmark catalog schema_version must be 1")
        raw_thresholds = document.get("tier_thresholds")
        if not isinstance(raw_thresholds, Mapping) or set(raw_thresholds) != set(_TIERS):
            raise ModelCatalogError("tier_thresholds must define L1, L2, and L3")
        thresholds = {tier: _score(raw_thresholds[tier], f"tier_thresholds.{tier}") for tier in _TIERS}
        if not thresholds["L1"] <= thresholds["L2"] <= thresholds["L3"]:
            raise ModelCatalogError("tier thresholds must be ascending")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise ModelCatalogError("benchmark catalog entries must be an array")
        indexed: dict[tuple[str, str, str, str], BenchmarkScore] = {}
        by_model: dict[str, list[BenchmarkScore]] = {}
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                raise ModelCatalogError("benchmark entries must be objects")
            try:
                score = BenchmarkScore(**dict(raw))
            except TypeError as exc:
                raise ModelCatalogError(f"invalid benchmark entry: {exc}") from exc
            if score.identity in indexed:
                raise ModelCatalogError(f"duplicate benchmark source identity: {score.identity!r}")
            indexed[score.identity] = score
            by_model.setdefault(score.canonical_model_id, []).append(score)
        return cls(
            MappingProxyType({model_id: tuple(scores) for model_id, scores in by_model.items()}),
            MappingProxyType(thresholds),
        )

    def lookup(self, canonical_model_id: str, *, now: datetime | None = None) -> IntelligenceScore | None:
        current_scores = tuple(
            score
            for score in self._scores_by_model.get(_text(canonical_model_id, "canonical_model_id"), ())
            if score.is_current(now=now)
        )
        if not current_scores:
            return None
        task_values: dict[str, list[float]] = {}
        for score in current_scores:
            for task, value in score.task_fit.items():
                task_values.setdefault(task, []).append(value)
        confidence_rank = {"low": 0, "medium": 1, "high": 2}
        confidence = min(current_scores, key=lambda score: confidence_rank[score.confidence]).confidence
        return IntelligenceScore(
            canonical_model_id=_text(canonical_model_id, "canonical_model_id"),
            normalized_score=sum(score.normalized_score for score in current_scores) / len(current_scores),
            task_fit={task: sum(values) / len(values) for task, values in task_values.items()},
            confidence=confidence,
            source_count=len(current_scores),
            sources=current_scores,
        )

    def tier_for(self, score: IntelligenceScore) -> str:
        if not isinstance(score, IntelligenceScore):
            raise TypeError("score must be an IntelligenceScore")
        tier = "L1"
        for candidate in _TIERS:
            if score.normalized_score >= self.tier_thresholds[candidate]:
                tier = candidate
        return tier

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "tier_thresholds": dict(self.tier_thresholds),
            "entries": [
                {
                    "canonical_model_id": score.canonical_model_id,
                    "benchmark": score.benchmark,
                    "benchmark_version": score.benchmark_version,
                    "model_version": score.model_version,
                    "source": score.source,
                    "observed_at": score.observed_at,
                    "expires_at": score.expires_at,
                    "raw_score": score.raw_score,
                    "normalized_score": score.normalized_score,
                    "confidence": score.confidence,
                    "task_fit": dict(score.task_fit),
                }
                for scores in sorted(self._scores_by_model.values(), key=lambda entries: entries[0].canonical_model_id)
                for score in scores
            ],
        }


__all__ = ["BenchmarkCatalog", "BenchmarkScore", "CANONICAL_TASK_FITS", "IntelligenceScore"]
