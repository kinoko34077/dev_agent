"""Bounded parsing of the official OpenRouter benchmark response.

This module is an operator-invoked evidence adapter, not a router and not a
generic external-data proxy.  It keeps only numeric benchmark facts and a
source-qualified model slug; credentials and raw response bodies never leave
the caller's process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Mapping

from ..resources.model_catalog import ModelCatalogError


_MAX_TEXT = 256
_DATE_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<date>\d{8})$")
_DEFAULT_TTL = timedelta(days=7)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > _MAX_TEXT:
        raise ModelCatalogError(f"{name} must be a bounded non-empty string")
    return value.strip()


def _score(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
        raise ModelCatalogError(f"{name} must be a finite score from 0 to 100")
    return float(value)


def _observed(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ModelCatalogError("observed_at must include a timezone")
    return current.astimezone(timezone.utc)


@dataclass(frozen=True)
class DiscoveredBenchmark:
    model_permaslug: str
    display_name: str | None
    source: str
    benchmark_version: str
    observed_at: str
    intelligence_index: float | None
    coding_index: float | None
    agentic_index: float | None


def parse_benchmark_document(
    document: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> tuple[DiscoveredBenchmark, ...]:
    if not isinstance(document, Mapping):
        raise ModelCatalogError("benchmark response must be an object")
    raw_entries = document.get("data")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ModelCatalogError("benchmark response must contain a non-empty data array")
    current = _observed(observed_at)
    records: list[DiscoveredBenchmark] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ModelCatalogError("benchmark entries must be objects")
        slug = _text(raw.get("model_permaslug"), "model_permaslug")
        source = _text(raw.get("source"), "source")
        display_name = raw.get("display_name")
        if display_name is not None:
            display_name = _text(display_name, "display_name")
        meta = raw.get("meta")
        version = meta.get("version") if isinstance(meta, Mapping) else None
        benchmark_version = _text(version, "benchmark_version") if version else "openrouter-benchmarks-v1"
        records.append(
            DiscoveredBenchmark(
                model_permaslug=slug,
                display_name=display_name,
                source=source,
                benchmark_version=benchmark_version,
                observed_at=current.isoformat(),
                intelligence_index=_score(raw.get("intelligence_index"), "intelligence_index"),
                coding_index=_score(raw.get("coding_index"), "coding_index"),
                agentic_index=_score(raw.get("agentic_index"), "agentic_index"),
            )
        )
    return tuple(records)


def _canonical_for_slug(slug: str, canonical_model_ids: set[str]) -> str | None:
    if slug in canonical_model_ids:
        return slug
    match = _DATE_SUFFIX.fullmatch(slug)
    if match is None:
        return None
    base = match.group("base")
    candidates = {
        candidate
        for candidate in canonical_model_ids
        if candidate == base or (_DATE_SUFFIX.fullmatch(candidate) is not None and _DATE_SUFFIX.fullmatch(candidate).group("base") == base)
    }
    if len(candidates) > 1:
        raise ModelCatalogError(f"ambiguous benchmark model mapping: {slug!r}")
    return next(iter(candidates), None)


def benchmark_scores_from_document(
    document: Mapping[str, Any],
    *,
    canonical_model_ids: set[str] | frozenset[str] | tuple[str, ...],
    observed_at: datetime | None = None,
    ttl: timedelta = _DEFAULT_TTL,
    confidence: str = "medium",
) -> list[dict[str, Any]]:
    """Convert only exact/unique canonical matches into BenchmarkScore rows."""

    if not isinstance(canonical_model_ids, (set, frozenset, tuple, list)):
        raise ModelCatalogError("canonical_model_ids must be a string collection")
    canonical_ids = {_text(item, "canonical_model_id") for item in canonical_model_ids}
    if not canonical_ids:
        raise ModelCatalogError("canonical_model_ids must not be empty")
    if not isinstance(ttl, timedelta) or ttl <= timedelta(0) or ttl > timedelta(days=31):
        raise ModelCatalogError("ttl must be from one second to 31 days")
    confidence = _text(confidence, "confidence").lower()
    if confidence not in {"low", "medium", "high"}:
        raise ModelCatalogError("confidence is invalid")
    current = _observed(observed_at)
    expires_at = (current + ttl).isoformat()
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in parse_benchmark_document(document, observed_at=current):
        canonical = _canonical_for_slug(record.model_permaslug, canonical_ids)
        if canonical is None or record.intelligence_index is None:
            continue
        source_name = record.source.lower()
        benchmark_name = (
            "artificial_analysis_intelligence_index"
            if source_name == "artificial-analysis"
            else f"{source_name}_intelligence_index"
        )
        task_fit: dict[str, float] = {}
        # Artificial Analysis calls this an agentic index.  It is retained as
        # a bounded planning proxy, never as proof of planning correctness.
        if record.agentic_index is not None:
            task_fit["planning"] = record.agentic_index
        if record.coding_index is not None:
            task_fit["coding"] = record.coding_index
        identity = (canonical, benchmark_name, record.benchmark_version, record.source)
        if identity in seen:
            continue
        seen.add(identity)
        records.append(
            {
                "canonical_model_id": canonical,
                "benchmark": benchmark_name,
                "benchmark_version": record.benchmark_version,
                "model_version": record.model_permaslug,
                "source": f"openrouter.api.v1.benchmarks:{record.source}",
                "observed_at": record.observed_at,
                "expires_at": expires_at,
                "raw_score": record.intelligence_index,
                "normalized_score": record.intelligence_index,
                "confidence": confidence,
                "task_fit": task_fit or {"writing": record.intelligence_index},
            }
        )
    return records


__all__ = [
    "DiscoveredBenchmark",
    "benchmark_scores_from_document",
    "parse_benchmark_document",
]
