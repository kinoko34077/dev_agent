"""Read-only exact-route admission through the existing resource Router.

This is an evidence adapter, not a second scheduler or Provider router.  It
narrows one existing ``RoutingSnapshot`` to an exact
``(provider, binding, model)`` identity and delegates the decision to
``ResourceRouter.choose``.  It never calls a Provider and therefore cannot
turn a read-only admission check into a generation effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .model_runtime import (
    RUNTIME_ELIGIBLE,
    RUNTIME_UNKNOWN,
    RUNTIME_UNAVAILABLE,
    RuntimeAdmissionObservation,
    RuntimeAdmissionSnapshot,
)
from .router import NoRoute, ResourceRouter, RouteRequest
from .snapshot import RoutingSnapshot


@dataclass(frozen=True)
class RuntimeAdmissionCandidate:
    """One exact catalog identity to evaluate against runtime-owned state."""

    provider_id: str
    provider_binding_id: str
    model_id: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider_id, self.provider_binding_id, self.model_id)


class RuntimeAdmissionEvaluator:
    """Evaluate exact routes without network, generation, or state mutation."""

    _SOURCE = "resource_router.read_only_admission"

    def __init__(self, router: ResourceRouter) -> None:
        if not isinstance(router, ResourceRouter):
            raise TypeError("router must be a ResourceRouter")
        self._router = router

    @staticmethod
    def _resource_identity(resource: Mapping[str, Any]) -> tuple[str, str, str | None]:
        provider = resource.get("provider_id")
        metadata = resource.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        binding = resource.get("provider_binding_id") or metadata.get("provider_binding_id") or provider
        model = resource.get("model_id") or metadata.get("model_id")
        return (str(provider), str(binding), str(model) if model is not None else None)

    @staticmethod
    def _observed_at(value: str | None) -> str:
        if value is None:
            return datetime.now(timezone.utc).isoformat()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("observed_at must be a non-empty ISO timestamp")
        return value.strip()

    def evaluate(
        self,
        candidate: RuntimeAdmissionCandidate,
        *,
        snapshot: RoutingSnapshot,
        observed_at: str | None = None,
    ) -> RuntimeAdmissionObservation:
        """Return one bounded status from the existing Router's read view.

        ``RUNTIME_ELIGIBLE`` means the current Resource/health/quota snapshot
        admits this exact route.  It is intentionally not a claim that a
        generation request succeeded; generation probing remains a separate,
        fresh-identity operation.
        """

        if not isinstance(candidate, RuntimeAdmissionCandidate):
            raise TypeError("candidate must be a RuntimeAdmissionCandidate")
        if not isinstance(snapshot, RoutingSnapshot):
            raise TypeError("snapshot must be a RoutingSnapshot")
        timestamp = self._observed_at(observed_at)
        matches = tuple(
            resource
            for resource in snapshot.resources
            if self._resource_identity(resource) == candidate.identity
        )
        if not matches:
            status = RUNTIME_UNAVAILABLE
        else:
            exact_snapshot = RoutingSnapshot(matches, snapshot.quota_observations_by_domain)
            request = RouteRequest(
                capabilities={"text"},
                allowed_providers={candidate.provider_id},
                allowed_provider_binding_ids={candidate.provider_binding_id},
            )
            try:
                self._router.choose(request, snapshot=exact_snapshot)
            except NoRoute:
                status = RUNTIME_UNAVAILABLE if any(self._resource_is_unavailable(resource) for resource in matches) else RUNTIME_UNKNOWN
            else:
                status = RUNTIME_ELIGIBLE
        return RuntimeAdmissionObservation(
            provider_id=candidate.provider_id,
            provider_binding_id=candidate.provider_binding_id,
            model_id=candidate.model_id,
            status=status,
            observed_at=timestamp,
            source=self._SOURCE,
        )

    @staticmethod
    def _resource_is_unavailable(resource: Mapping[str, Any]) -> bool:
        health = resource.get("health")
        if isinstance(health, str) and health.strip().lower() not in {"healthy", "degraded"}:
            return True
        available = resource.get("available")
        return isinstance(available, (int, float)) and not isinstance(available, bool) and available <= 0

    def evaluate_many(
        self,
        candidates: Iterable[RuntimeAdmissionCandidate],
        *,
        snapshot: RoutingSnapshot,
        observed_at: str | None = None,
    ) -> RuntimeAdmissionSnapshot:
        """Evaluate a bounded candidate sequence into the existing snapshot type."""

        observations = [self.evaluate(candidate, snapshot=snapshot, observed_at=observed_at) for candidate in candidates]
        return RuntimeAdmissionSnapshot.from_document(
            {"schema_version": 1, "observations": [observation.to_dict() for observation in observations]}
        )


__all__ = ["RuntimeAdmissionCandidate", "RuntimeAdmissionEvaluator"]
