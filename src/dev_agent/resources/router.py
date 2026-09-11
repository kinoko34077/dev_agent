"""Deterministic provider/resource selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import time
from typing import Protocol

from .qualification import QualificationResolver
from .snapshot import RoutingSnapshot


class NoRoute(RuntimeError):
    pass


class ResourceReadView(Protocol):
    """Read-only boundary required by deterministic resource selection."""

    def routing_snapshot(self) -> RoutingSnapshot: ...


@dataclass(frozen=True)
class RouteRequest:
    capabilities: set[str] = field(default_factory=set)
    sensitivity: str = "normal"
    allowed_providers: set[str] | None = None
    max_cost_minor: int | None = None
    max_latency_ms: int | None = None
    excluded_resource_ids: set[str] = field(default_factory=set)
    max_observation_age_seconds: float | None = 300.0
    max_quota_observation_age_seconds: float | None = 300.0
    allow_unknown_quota: bool = False
    allowed_intelligence_tiers: set[str] | frozenset[str] | tuple[str, ...] | None = None
    allowed_provider_binding_ids: set[str] | frozenset[str] | tuple[str, ...] | None = None
    excluded_provider_binding_ids: set[str] | frozenset[str] | tuple[str, ...] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not isinstance(self.allow_unknown_quota, bool):
            raise ValueError("allow_unknown_quota must be a boolean")
        for name, value in (("max_cost_minor", self.max_cost_minor), ("max_latency_ms", self.max_latency_ms)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.max_observation_age_seconds is not None:
            if isinstance(self.max_observation_age_seconds, bool) or not isinstance(self.max_observation_age_seconds, (int, float)) or not math.isfinite(self.max_observation_age_seconds) or self.max_observation_age_seconds < 0:
                raise ValueError("max_observation_age_seconds must be a non-negative number or None")
        if self.max_quota_observation_age_seconds is not None:
            if isinstance(self.max_quota_observation_age_seconds, bool) or not isinstance(self.max_quota_observation_age_seconds, (int, float)) or not math.isfinite(self.max_quota_observation_age_seconds) or self.max_quota_observation_age_seconds < 0:
                raise ValueError("max_quota_observation_age_seconds must be a non-negative number or None")
        if self.allowed_intelligence_tiers is not None:
            if not isinstance(self.allowed_intelligence_tiers, (set, frozenset, tuple, list)):
                raise ValueError("allowed_intelligence_tiers must be a collection of L0-L3 strings or None")
            normalized: set[str] = set()
            for tier in self.allowed_intelligence_tiers:
                if not isinstance(tier, str) or tier.strip() not in {"L0", "L1", "L2", "L3"}:
                    raise ValueError("allowed_intelligence_tiers must contain only L0, L1, L2, or L3")
                normalized.add(tier.strip())
            if not normalized:
                raise ValueError("allowed_intelligence_tiers must not be empty")
            object.__setattr__(self, "allowed_intelligence_tiers", frozenset(normalized))
        for name, value in (
            ("allowed_provider_binding_ids", self.allowed_provider_binding_ids),
            ("excluded_provider_binding_ids", self.excluded_provider_binding_ids),
        ):
            if name == "allowed_provider_binding_ids" and value is None:
                continue
            if not isinstance(value, (set, frozenset, tuple, list)):
                raise ValueError(f"{name} must be a collection of non-empty strings")
            normalized = set()
            for binding_id in value:
                if not isinstance(binding_id, str) or not binding_id.strip():
                    raise ValueError(f"{name} must be a collection of non-empty strings")
                normalized.add(binding_id.strip())
            if name == "allowed_provider_binding_ids" and not normalized:
                raise ValueError("allowed_provider_binding_ids must not be empty")
            object.__setattr__(self, name, frozenset(normalized))


@dataclass(frozen=True)
class RouteSelection:
    resource_id: str
    provider_id: str
    native_unit: str
    estimated_cost_minor: int | None
    price_currency: str | None
    provider_binding_id: str | None = None
    model_id: str | None = None
    quota_domain: str | None = None
    unknown_quota: bool = False


_SENSITIVITY = {"public": 0, "normal": 1, "internal": 2, "sensitive": 3}


class ResourceRouter:
    def __init__(self, read_view: ResourceReadView, *, qualification_resolver: QualificationResolver | None = None) -> None:
        self._read_view = read_view
        self._qualification_resolver = qualification_resolver or QualificationResolver()

    @property
    def ledger(self) -> ResourceReadView:
        """Compatibility view; routing itself only requires read access."""
        return self._read_view

    @property
    def qualification_resolver(self) -> QualificationResolver:
        """Return the runtime-scoped qualification resolver."""
        return self._qualification_resolver

    @staticmethod
    def _quota_ratio(observation: dict[str, object]) -> float | None:
        ratios: list[float] = []
        generic_limit = observation.get("limit")
        generic_remaining = observation.get("remaining")
        if (
            isinstance(generic_limit, (int, float))
            and not isinstance(generic_limit, bool)
            and math.isfinite(float(generic_limit))
            and generic_limit > 0
            and isinstance(generic_remaining, (int, float))
            and not isinstance(generic_remaining, bool)
            and math.isfinite(float(generic_remaining))
        ):
            ratios.append(float(generic_remaining) / float(generic_limit))
        elif (
            isinstance(generic_remaining, (int, float))
            and not isinstance(generic_remaining, bool)
            and math.isfinite(float(generic_remaining))
            and generic_remaining == 0
        ):
            ratios.append(0.0)
        for limit_name, remaining_name in (
            ("request_limit", "request_remaining"),
            ("token_limit", "token_remaining"),
        ):
            limit = observation.get(limit_name)
            remaining = observation.get(remaining_name)
            if isinstance(remaining, int) and remaining == 0:
                ratios.append(0.0)
            if isinstance(limit, int) and limit > 0 and isinstance(remaining, int):
                ratios.append(remaining / limit)
        daily_remaining = observation.get("daily_remaining")
        if isinstance(daily_remaining, int) and daily_remaining == 0:
            ratios.append(0.0)
        return min(ratios) if ratios else None

    def _fresh_domain_quota_ratio(self, quota_domain: str, max_age_seconds: float | None, observations_by_domain) -> float | None:
        ratios: list[float] = []
        now = time.time()
        for observation in observations_by_domain.get(quota_domain, ()):
            if self._quota_is_blocked(observation):
                continue
            try:
                observed_at = datetime.fromisoformat(str(observation["observed_at"])).timestamp()
            except (TypeError, ValueError):
                continue
            age = now - observed_at
            if age < 0 or (max_age_seconds is not None and age > max_age_seconds):
                continue
            ratio = self._quota_ratio(observation)
            if ratio is not None:
                ratios.append(ratio)
        # A quota domain may be shared by several credentials.  Taking the
        # minimum fresh headroom prevents the router from treating shared
        # quota as additive capacity.
        return min(ratios) if ratios else None

    @staticmethod
    def _quota_is_blocked(observation: dict[str, object]) -> bool:
        """Keep blocked or pre-reset observations out of routing.

        A past ``blocked_until`` is not enough to revive an old exhausted
        observation.  The provider must publish a newer observation after the
        block boundary; this leaves the bounded probe/requalification decision
        to the scheduler/operator rather than turning every queued task into a
        retry storm.
        """

        reason = observation.get("block_reason")
        if not isinstance(reason, str) or not reason.strip():
            return False
        if reason.strip().lower() in {"authorization", "permission", "blocked_external"}:
            return True
        # Keep the provider out of the route even after the wake timestamp.
        # A bounded probe or a normal provider response must write a newer
        # observation without ``block_reason`` before the resource is eligible
        # again.  This avoids reviving an exhausted/unauthorized provider just
        # because a local clock crossed its conservative cooldown.
        return True

    def snapshot(self) -> RoutingSnapshot:
        return self._read_view.routing_snapshot()

    def choose(self, request: RouteRequest, *, snapshot: RoutingSnapshot | None = None) -> RouteSelection:
        return self._choose_from_snapshot(request, self.snapshot() if snapshot is None else snapshot)

    def _choose_from_snapshot(self, request: RouteRequest, snapshot: RoutingSnapshot) -> RouteSelection:
        if request.sensitivity not in _SENSITIVITY:
            raise ValueError("invalid sensitivity")
        candidates = []
        unknown_quota_resource_ids: set[str] = set()
        for resource in snapshot.resources:
            if resource["resource_id"] in request.excluded_resource_ids:
                continue
            if request.allowed_providers is not None and resource["provider_id"] not in request.allowed_providers:
                continue
            metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
            provider_binding_id = resource.get("provider_binding_id") or metadata.get("provider_binding_id") or resource["provider_id"]
            if request.allowed_provider_binding_ids is not None and provider_binding_id not in request.allowed_provider_binding_ids:
                continue
            if provider_binding_id in request.excluded_provider_binding_ids:
                continue
            effective_capabilities = set(resource["capabilities"])
            effective_tier = metadata.get("intelligence_tier")
            # Production composition marks resources explicitly.  Do not infer
            # qualification ownership from billing metadata: isolated
            # qualification probes and legacy/test resources may use the
            # trusted billing catalog before a qualification record exists.
            # OperationService sets this flag for every normal provider
            # resource, so production routing remains fail-closed without
            # making the router guess at the caller's authority.
            qualification_required = metadata.get("qualification_required") is True
            if qualification_required:
                model_id = resource.get("model_id") or metadata.get("model_id")
                if not isinstance(provider_binding_id, str) or not isinstance(model_id, str) or not provider_binding_id.strip() or not model_id.strip():
                    continue
                qualification = self._qualification_resolver.resolve(
                    resource["provider_id"],
                    provider_binding_id.strip(),
                    model_id.strip(),
                )
                if qualification is None:
                    continue
                # Catalog capabilities remain operator-owned.  Qualification
                # can only narrow the effective routing view, never grant a
                # capability that the persisted resource did not allow.
                effective_capabilities &= set(qualification.routing_capabilities)
                effective_tier = qualification.intelligence_tier
            if not request.capabilities.issubset(effective_capabilities):
                continue
            if request.allowed_intelligence_tiers is not None:
                if effective_tier not in request.allowed_intelligence_tiers:
                    continue
            if _SENSITIVITY.get(resource["sensitivity"], -1) < _SENSITIVITY[request.sensitivity]:
                continue
            if resource["health"] not in {"healthy", "degraded"} or resource["available"] <= 0:
                continue
            if resource["circuit_open_until"] > time.time():
                continue
            inflight = resource.get("inflight")
            concurrency_limit = resource.get("concurrency_limit")
            if (
                isinstance(concurrency_limit, (int, float))
                and not isinstance(concurrency_limit, bool)
                and math.isfinite(float(concurrency_limit))
                and (concurrency_limit <= 0 or not isinstance(inflight, (int, float)) or isinstance(inflight, bool) or inflight >= concurrency_limit)
            ):
                continue
            try:
                observed_at = datetime.fromisoformat(resource["observed_at"]).timestamp()
            except (TypeError, ValueError):
                continue
            observation_age = time.time() - observed_at
            if observation_age < 0:
                continue
            if request.max_observation_age_seconds is not None:
                if observation_age > request.max_observation_age_seconds:
                    continue
            if request.max_latency_ms is not None:
                latency = resource["metadata"].get("latency_ms")
                if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0 or latency > request.max_latency_ms:
                    continue
            if request.max_cost_minor is not None and resource["cost_minor"] is not None and resource["cost_minor"] > request.max_cost_minor:
                continue
            if request.max_cost_minor is not None and resource["cost_minor"] is None:
                continue
            quota_ratio = None
            if resource.get("quota_domain"):
                quota_ratio = self._fresh_domain_quota_ratio(resource["quota_domain"], request.max_quota_observation_age_seconds, snapshot.quota_observations_by_domain)
                if quota_ratio is None:
                    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
                    domain_observations = snapshot.quota_observations_by_domain.get(resource["quota_domain"], ())
                    if any(self._quota_is_blocked(observation) for observation in domain_observations):
                        # A previous provider/quota block is not an UNKNOWN
                        # bootstrap case.  It remains quarantined until a
                        # bounded requalification or normal response writes a
                        # newer unblocked observation.  Otherwise a trusted
                        # free binding could immediately bypass its own 429
                        # block merely because no positive ratio was found.
                        continue
                    # A qualified, explicitly no-charge binding may make one
                    # bounded liveness request before its provider exposes
                    # quota telemetry.  This is an UNKNOWN observation, not
                    # fabricated headroom: paid or unqualified resources
                    # remain unroutable until real quota evidence exists.
                    unknown_bootstrap = (
                        request.allow_unknown_quota
                        and resource.get("cost_minor") == 0
                        and metadata.get("billing_authority") == "trusted_catalog"
                    )
                    if not unknown_bootstrap:
                        continue
                    unknown_quota_resource_ids.add(resource["resource_id"])
                elif quota_ratio <= 0:
                    continue
            elif resource.get("quota_remaining_ratio") is not None:
                quota_ratio = float(resource["quota_remaining_ratio"])
            latency = resource.get("latency_ewma_ms")
            if latency is None:
                latency = resource["metadata"].get("latency_ms")
            latency_rank = float(latency) if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(float(latency)) and latency >= 0 else 10**18
            failure_rank = float(resource["failure_ewma"]) if isinstance(resource.get("failure_ewma"), (int, float)) and not isinstance(resource["failure_ewma"], bool) else 1.0
            inflight_rank = float(resource["inflight"]) if isinstance(resource.get("inflight"), (int, float)) and not isinstance(resource["inflight"], bool) else 10**18
            # Minimize exposure first, then maximize known quota headroom,
            # then prefer free/low-cost, healthy, fast, lightly loaded resources.
            quota_rank = -quota_ratio if quota_ratio is not None else 0.0
            candidates.append((
                _SENSITIVITY[resource["sensitivity"]],
                quota_rank,
                resource["cost_minor"] if resource["cost_minor"] is not None else 10**18,
                failure_rank,
                latency_rank,
                inflight_rank,
                resource["resource_id"],
                resource,
            ))
        if not candidates:
            raise NoRoute("no eligible resource")
        *_, chosen = min(candidates)
        metadata = chosen.get("metadata") if isinstance(chosen.get("metadata"), dict) else {}
        provider_binding_id = chosen.get("provider_binding_id") or metadata.get("provider_binding_id") or chosen["provider_id"]
        model_id = chosen.get("model_id") or metadata.get("model_id")
        if not isinstance(provider_binding_id, str) or not provider_binding_id.strip():
            provider_binding_id = chosen["provider_id"]
        if not isinstance(model_id, str) or not model_id.strip():
            model_id = None
        return RouteSelection(
            chosen["resource_id"],
            chosen["provider_id"],
            chosen["native_unit"],
            chosen["cost_minor"],
            chosen["price_currency"],
            provider_binding_id.strip(),
            model_id.strip() if model_id else None,
            chosen.get("quota_domain"),
            chosen["resource_id"] in unknown_quota_resource_ids,
        )
