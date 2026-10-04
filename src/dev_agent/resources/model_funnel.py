"""Bounded read-only model evidence and qualification funnel projection.

The funnel joins existing exact-identity evidence layers for diagnostics.  It
does not probe Providers, mutate routing, grant a tier, or persist a second
model state store.  In particular, ``RUNTIME_BOOTSTRAP_ADMITTED`` is reported
separately from formal ``RUNTIME_ELIGIBLE`` supply.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .billing_catalog import profile_for
from .model_evidence import ModelEvidenceCatalog
from .model_runtime import (
    RUNTIME_BOOTSTRAP_ADMITTED,
    RUNTIME_ELIGIBLE,
    RUNTIME_NOT_PROBED,
    RUNTIME_UNKNOWN,
    RUNTIME_UNAVAILABLE,
    RuntimeAdmissionSnapshot,
)
from .qualification import QualificationResolver


MAX_FUNNEL_ROWS = 5000
MAX_QUALIFICATION_CANDIDATES = 128
_RUNTIME_STATES = frozenset(
    {RUNTIME_ELIGIBLE, RUNTIME_BOOTSTRAP_ADMITTED, RUNTIME_NOT_PROBED, RUNTIME_UNKNOWN, RUNTIME_UNAVAILABLE}
)


def _current(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expiry_state(
    *,
    current: datetime,
    refresh_horizon: timedelta,
    discovery_current: bool,
    static_result: str,
    qualification_status: str,
    discovery_expires_at: str,
    qualification_expires_at: str | None,
) -> str:
    if not discovery_current:
        return "EXPIRED"
    if static_result != "ELIGIBLE":
        return "MISSING_EVIDENCE"
    if qualification_status == "EXPIRED":
        return "EXPIRED"
    if qualification_status != "CURRENT_HIGH_CONFIDENCE":
        return "MISSING_EVIDENCE"
    expiries = [_timestamp(discovery_expires_at)]
    if qualification_expires_at is not None:
        expiries.append(_timestamp(qualification_expires_at))
    if any(expiry <= current for expiry in expiries):
        return "EXPIRED"
    if any(expiry <= current + refresh_horizon for expiry in expiries):
        return "EXPIRING"
    return "CURRENT"


def _metrics() -> dict[str, int]:
    return {
        "discovered_count": 0,
        "current_discovered_count": 0,
        "expired_discovered_count": 0,
        "alias_coverage_count": 0,
        "canonical_coverage_count": 0,
        "benchmark_coverage_count": 0,
        "benchmark_tier_coverage_count": 0,
        "capability_coverage_count": 0,
        "static_eligible_count": 0,
        "qualification_current_count": 0,
        "qualification_high_confidence_count": 0,
        "runtime_eligible_count": 0,
        "runtime_bootstrap_admitted_count": 0,
        "runtime_not_probed_count": 0,
        "runtime_unknown_count": 0,
        "runtime_unavailable_count": 0,
        "expiring_count": 0,
        "expired_count": 0,
        "formal_supply_count": 0,
        "independent_route_count": 0,
    }


def _increment(metrics: dict[str, int], row: "FunnelCandidate") -> None:
    metrics["discovered_count"] += 1
    if row.discovery_status == "CURRENT":
        metrics["current_discovered_count"] += 1
    else:
        metrics["expired_discovered_count"] += 1
    if row.discovery_status == "CURRENT" and row.static_result == "ELIGIBLE":
        metrics["static_eligible_count"] += 1
    if row.discovery_status == "CURRENT" and row.alias_status == "PASS":
        metrics["alias_coverage_count"] += 1
    if row.discovery_status == "CURRENT" and row.canonical_model_id is not None:
        metrics["canonical_coverage_count"] += 1
    if row.discovery_status == "CURRENT" and row.benchmark_status == "PASS":
        metrics["benchmark_coverage_count"] += 1
    if row.discovery_status == "CURRENT" and row.benchmark_tier is not None:
        metrics["benchmark_tier_coverage_count"] += 1
    if row.discovery_status == "CURRENT" and row.capability_status == "PASS":
        metrics["capability_coverage_count"] += 1
    if row.discovery_status == "CURRENT" and row.qualification_status in {"CURRENT_HIGH_CONFIDENCE", "CURRENT_LOW_CONFIDENCE"}:
        metrics["qualification_current_count"] += 1
    if row.discovery_status == "CURRENT" and row.qualification_status == "CURRENT_HIGH_CONFIDENCE":
        metrics["qualification_high_confidence_count"] += 1
    if row.discovery_status == "CURRENT" and row.runtime_status == RUNTIME_ELIGIBLE:
        metrics["runtime_eligible_count"] += 1
    elif row.discovery_status == "CURRENT" and row.runtime_status == RUNTIME_BOOTSTRAP_ADMITTED:
        metrics["runtime_bootstrap_admitted_count"] += 1
    elif row.discovery_status == "CURRENT" and row.runtime_status == RUNTIME_NOT_PROBED:
        metrics["runtime_not_probed_count"] += 1
    elif row.discovery_status == "CURRENT" and row.runtime_status == RUNTIME_UNKNOWN:
        metrics["runtime_unknown_count"] += 1
    elif row.discovery_status == "CURRENT" and row.runtime_status == RUNTIME_UNAVAILABLE:
        metrics["runtime_unavailable_count"] += 1
    if row.refresh_state == "EXPIRING":
        metrics["expiring_count"] += 1
    elif row.refresh_state == "EXPIRED":
        metrics["expired_count"] += 1
    if row.formal_supply:
        metrics["formal_supply_count"] += 1


@dataclass(frozen=True)
class FunnelCandidate:
    """One exact discovered identity projected through the existing gates."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    discovery_status: str
    refresh_state: str
    static_result: str
    static_gap: str | None
    alias_status: str
    benchmark_status: str
    capability_status: str
    canonical_model_id: str | None
    benchmark_tier: str | None
    task_fit: Mapping[str, float]
    qualification_status: str
    qualification_confidence: str | None
    qualification_tier: str | None
    qualification_expires_at: str | None
    billing_status: str
    runtime_status: str
    formal_supply: bool
    candidate_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_fit", MappingProxyType(dict(self.task_fit)))
        if self.runtime_status not in _RUNTIME_STATES:
            raise ValueError(f"unsupported runtime status: {self.runtime_status}")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider_id, self.provider_binding_id, self.model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "discovery_status": self.discovery_status,
            "refresh_state": self.refresh_state,
            "static_result": self.static_result,
            "static_gap": self.static_gap,
            "alias_status": self.alias_status,
            "benchmark_status": self.benchmark_status,
            "capability_status": self.capability_status,
            "canonical_model_id": self.canonical_model_id,
            "benchmark_tier": self.benchmark_tier,
            "task_fit": dict(self.task_fit),
            "qualification_status": self.qualification_status,
            "qualification_confidence": self.qualification_confidence,
            "qualification_tier": self.qualification_tier,
            "qualification_expires_at": self.qualification_expires_at,
            "billing_status": self.billing_status,
            "runtime_status": self.runtime_status,
            "formal_supply": self.formal_supply,
            "candidate_reason": self.candidate_reason,
        }


@dataclass(frozen=True)
class FunnelReport:
    """Bounded, deterministic evidence coverage and candidate projection."""

    rows: tuple[FunnelCandidate, ...]
    qualification_candidates: tuple[FunnelCandidate, ...]
    coverage: Mapping[str, Any]
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "coverage": self.coverage,
            "qualification_candidates": [row.to_dict() for row in self.qualification_candidates],
        }

    def supply_for(
        self,
        *,
        provider_id: str | None = None,
        role: str | None = None,
        tier: str | None = None,
        min_independent_routes: int = 2,
    ) -> dict[str, Any]:
        """Evaluate already observed formal supply without probing or routing."""

        if isinstance(min_independent_routes, bool) or not isinstance(min_independent_routes, int) or min_independent_routes < 1:
            raise ValueError("min_independent_routes must be a positive integer")
        selected = [row for row in self.rows if row.formal_supply]
        if provider_id is not None:
            selected = [row for row in selected if row.provider_id == provider_id]
        if role is not None:
            selected = [row for row in selected if role in row.task_fit]
        if tier is not None:
            selected = [row for row in selected if row.qualification_tier == tier]
        routes = {(row.provider_id, row.provider_binding_id) for row in selected}
        sufficient = len(routes) >= min_independent_routes
        return {
            "status": "SUFFICIENT" if sufficient else "INSUFFICIENT",
            "formal_supply_count": len(selected),
            "independent_route_count": len(routes),
            "required_independent_routes": min_independent_routes,
            "independence_basis": "provider_id+provider_binding_id",
            "reason": None if sufficient else "insufficient_independent_routes",
        }


def _qualification_state(
    resolver: QualificationResolver,
    provider_id: str,
    provider_binding_id: str,
    model_id: str,
    *,
    now: datetime,
) -> tuple[str, str | None, str | None, str | None]:
    raw = resolver.catalog.lookup(provider_id, provider_binding_id, model_id)
    if raw is None:
        return "MISSING", None, None, None
    expires_at = str(raw["expires_at"])
    if _timestamp(expires_at) <= now:
        return "EXPIRED", str(raw.get("confidence", "")).lower() or None, raw.get("intelligence_tier"), expires_at
    observed = resolver.resolve_observed(provider_id, provider_binding_id, model_id, now=now)
    if observed is None:
        return "CURRENT_UNPROJECTABLE", str(raw.get("confidence", "")).lower() or None, raw.get("intelligence_tier"), expires_at
    if resolver.resolve(provider_id, provider_binding_id, model_id, now=now) is None:
        return "CURRENT_LOW_CONFIDENCE", observed.confidence, observed.intelligence_tier, observed.expires_at
    return "CURRENT_HIGH_CONFIDENCE", observed.confidence, observed.intelligence_tier, observed.expires_at


def _billing_status(provider_id: str, provider_binding_id: str, model_id: str, *, now: datetime) -> str:
    profile = profile_for(provider_id, provider_binding_id, model_id)
    if profile is None:
        return "MISSING"
    is_current = getattr(profile, "is_current", None)
    if callable(is_current) and not is_current(now=now):
        return "EXPIRED"
    return "CURRENT"


def _candidate_reason(row: FunnelCandidate) -> str | None:
    if row.formal_supply:
        return None
    if row.discovery_status != "CURRENT":
        return "discovery_expired"
    if row.static_result != "ELIGIBLE":
        return row.static_result.lower()
    if row.billing_status != "CURRENT":
        return "billing_missing"
    if row.qualification_status == "MISSING":
        return "qualification_missing"
    if row.qualification_status == "EXPIRED":
        return "qualification_expired"
    if row.qualification_status == "CURRENT_LOW_CONFIDENCE":
        return "qualification_low_confidence"
    if row.runtime_status == RUNTIME_BOOTSTRAP_ADMITTED:
        return "runtime_bootstrap_admitted"
    if row.runtime_status == RUNTIME_NOT_PROBED:
        return "runtime_not_probed"
    if row.runtime_status == RUNTIME_UNKNOWN:
        return "runtime_unknown"
    if row.runtime_status == RUNTIME_UNAVAILABLE:
        return "runtime_unavailable"
    return "not_formal_supply"


def build_funnel_report(
    evidence: ModelEvidenceCatalog,
    *,
    runtime_snapshot: RuntimeAdmissionSnapshot | None = None,
    qualification_resolver: QualificationResolver | None = None,
    now: datetime | None = None,
    refresh_horizon: timedelta = timedelta(hours=48),
    provider_id: str | None = None,
    provider_binding_id: str | None = None,
    model_id: str | None = None,
    limit: int = MAX_FUNNEL_ROWS,
    candidate_limit: int = MAX_QUALIFICATION_CANDIDATES,
) -> FunnelReport:
    """Build a bounded exact-identity funnel without network or mutation."""

    if not isinstance(evidence, ModelEvidenceCatalog):
        raise TypeError("evidence must be a ModelEvidenceCatalog")
    if not isinstance(refresh_horizon, timedelta) or refresh_horizon.total_seconds() < 0:
        raise ValueError("refresh_horizon must be a non-negative timedelta")
    for value, name, maximum in (
        (limit, "limit", MAX_FUNNEL_ROWS),
        (candidate_limit, "candidate_limit", MAX_QUALIFICATION_CANDIDATES),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
            raise ValueError(f"{name} must be from 1 to {maximum}")
    current = _current(now)
    runtime = runtime_snapshot or RuntimeAdmissionSnapshot.empty()
    qualification = qualification_resolver or QualificationResolver()
    rows: list[FunnelCandidate] = []
    for entry in evidence.catalog.all_entries():
        if provider_id is not None and entry.provider_id != provider_id:
            continue
        if provider_binding_id is not None and entry.provider_binding_id != provider_binding_id:
            continue
        if model_id is not None and entry.model_id != model_id:
            continue
        discovery_current = entry.is_current(now=current)
        diagnostic = evidence.resolver.diagnose(entry.provider_id, entry.provider_binding_id, entry.model_id, now=current)
        admission = evidence.resolver.resolve(entry.provider_id, entry.provider_binding_id, entry.model_id, now=current)
        alias = evidence.aliases.lookup(entry.provider_id, entry.model_id)
        benchmark = evidence.benchmarks.lookup(alias.canonical_model_id, now=current) if alias is not None else None
        capability = evidence.capabilities.lookup(entry.provider_id, entry.model_id, now=current)
        qualification_status, qualification_confidence, qualification_tier, qualification_expires_at = _qualification_state(
            qualification,
            entry.provider_id,
            entry.provider_binding_id,
            entry.model_id,
            now=current,
        )
        runtime_observation = runtime.lookup(entry.provider_id, entry.provider_binding_id, entry.model_id)
        runtime_status = runtime_observation.status if runtime_observation is not None else RUNTIME_NOT_PROBED
        billing_status = _billing_status(entry.provider_id, entry.provider_binding_id, entry.model_id, now=current)
        benchmark_tier = admission.intelligence_tier if admission is not None else (evidence.benchmarks.tier_for(benchmark) if benchmark is not None else None)
        task_fit = admission.task_fit if admission is not None else (benchmark.task_fit if benchmark is not None else {})
        formal_supply = (
            discovery_current
            and diagnostic.result == "ELIGIBLE"
            and qualification_status == "CURRENT_HIGH_CONFIDENCE"
            and billing_status == "CURRENT"
            and runtime_status == RUNTIME_ELIGIBLE
        )
        row = FunnelCandidate(
            provider_id=entry.provider_id,
            provider_binding_id=entry.provider_binding_id,
            model_id=entry.model_id,
            discovery_status="CURRENT" if discovery_current else "EXPIRED",
            refresh_state=_expiry_state(
                current=current,
                refresh_horizon=refresh_horizon,
                discovery_current=discovery_current,
                static_result=diagnostic.result,
                qualification_status=qualification_status,
                discovery_expires_at=entry.expires_at,
                qualification_expires_at=qualification_expires_at,
            ),
            static_result=diagnostic.result,
            static_gap=None if diagnostic.result == "ELIGIBLE" else diagnostic.result,
            alias_status="PASS" if alias is not None else "MISSING",
            benchmark_status="PASS" if benchmark is not None else "MISSING",
            capability_status="PASS" if capability is not None else "MISSING",
            canonical_model_id=alias.canonical_model_id if alias is not None else diagnostic.canonical_model_id,
            benchmark_tier=benchmark_tier,
            task_fit=task_fit,
            qualification_status=qualification_status,
            qualification_confidence=qualification_confidence,
            qualification_tier=qualification_tier,
            qualification_expires_at=qualification_expires_at,
            billing_status=billing_status,
            runtime_status=runtime_status,
            formal_supply=formal_supply,
            candidate_reason=None,
        )
        row = replace(row, candidate_reason=_candidate_reason(row))
        rows.append(row)
        if len(rows) >= limit:
            break

    providers: dict[str, dict[str, int]] = {}
    roles: dict[str, dict[str, int]] = {}
    tiers: dict[str, dict[str, int]] = {}
    for row in rows:
        provider_metrics = providers.setdefault(row.provider_id, _metrics())
        _increment(provider_metrics, row)
        for role in row.task_fit:
            role_metrics = roles.setdefault(role, _metrics())
            _increment(role_metrics, row)
        for tier in {row.benchmark_tier, row.qualification_tier} - {None}:
            tier_metrics = tiers.setdefault(str(tier), _metrics())
            _increment(tier_metrics, row)
    for metrics, group_rows in (
        (providers, rows),
        (roles, rows),
        (tiers, rows),
    ):
        for key, values in metrics.items():
            values["independent_route_count"] = len(
                {(row.provider_id, row.provider_binding_id) for row in group_rows if row.formal_supply and (key == row.provider_id or key in row.task_fit or key in {row.benchmark_tier, row.qualification_tier})}
            )
    qualification_candidates = tuple(
        row
        for row in rows
        if row.discovery_status == "CURRENT"
        and row.static_result == "ELIGIBLE"
        and not row.formal_supply
    )[:candidate_limit]
    overall = _metrics()
    for row in rows:
        _increment(overall, row)
    overall["independent_route_count"] = len({(row.provider_id, row.provider_binding_id) for row in rows if row.formal_supply})
    coverage = {
        "overall": overall,
        "providers": {key: providers[key] for key in sorted(providers)},
        "roles": {key: roles[key] for key in sorted(roles)},
        "tiers": {key: tiers[key] for key in sorted(tiers)},
        "qualification_candidate_count": len(qualification_candidates),
        "qualification_candidates_bounded": candidate_limit,
        "independent_route_basis": "provider_id+provider_binding_id; quota domains are not inferred",
        "formal_supply_basis": "current discovery + static evidence + high-confidence qualification + current billing profile + RUNTIME_ELIGIBLE",
    }
    return FunnelReport(
        rows=tuple(rows),
        qualification_candidates=qualification_candidates,
        coverage=coverage,
        observed_at=current.isoformat(),
    )


__all__ = [
    "FunnelCandidate",
    "FunnelReport",
    "MAX_FUNNEL_ROWS",
    "MAX_QUALIFICATION_CANDIDATES",
    "build_funnel_report",
]
