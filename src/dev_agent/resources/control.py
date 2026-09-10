"""Controller-facing reservation seam for Phase 6 resource enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.protocol import ModelRequest, ModelResponse
from .budget import BudgetExceeded, BudgetGovernor, BudgetReconciliationRequired, BudgetReservation, MaintenanceActive, ResourceUnavailable, UnknownPrice
from .ledger import MoneyAmount
from .router import NoRoute, ResourceRouter, RouteRequest, RouteSelection
from .quota_policy import classify_provider_error
from .ledger import unknown_quota_wake_reason


class DispatchDenied(RuntimeError):
    """A provider dispatch cannot be started under current resource policy."""

    def __init__(self, category: str, message: str, *, wake_at: float | None = None, wake_reason: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.wake_at = wake_at
        self.wake_reason = wake_reason


@dataclass(frozen=True)
class DispatchReservation:
    budget: BudgetReservation
    provider_id: str
    native_unit: str = "request"
    estimated_cost_minor: int | None = None
    price_currency: str | None = None
    provider_binding_id: str | None = None
    model_id: str | None = None
    unknown_quota_domain: str | None = None


class ResourcePolicy(Protocol):
    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest, *, intent_key: str | None = None) -> DispatchReservation: ...
    def mark_dispatching(self, reservation: DispatchReservation) -> None: ...
    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None: ...
    def observe_provider_response(self, reservation: DispatchReservation, response: ModelResponse) -> bool: ...
    def release(self, reservation: DispatchReservation) -> None: ...
    def uncertain(self, reservation: DispatchReservation) -> None: ...
    def record_provider_success(self, provider_id: str, *, resource_id: str | None = None, provider_binding_id: str | None = None) -> None: ...
    def record_provider_failure(self, provider_id: str, *, resource_id: str | None = None, provider_binding_id: str | None = None, threshold: int = 3, cooldown_seconds: float = 60.0) -> None: ...


class ResourceControlPlane:
    def __init__(self, router: ResourceRouter, governor: BudgetGovernor) -> None:
        self.router = router
        self.governor = governor
        self._maintenance = False

    def set_maintenance(self, enabled: bool) -> None:
        self._maintenance = bool(enabled)
        self.governor.ledger.set_maintenance(self._maintenance)

    def _ensure_dispatch_allowed(self) -> None:
        if self._maintenance or self.governor.ledger.maintenance_enabled():
            raise DispatchDenied("maintenance", "new provider dispatch is disabled during maintenance")

    def routing_snapshot(self):
        """Return the single read snapshot used by a routing decision.

        Provider dispatchers must not reach through the control plane into the
        router's backing ledger.  Keeping this read on the control-plane
        facade also leaves room for one coordinated snapshot of resource,
        quota, and health state as the routing policy grows.
        """
        return self.router.snapshot()

    def budget_snapshot(self):
        """Return the budget view needed by survival policy evaluation."""
        return self.governor.snapshot()

    @staticmethod
    def _route_request(
        request: ModelRequest,
        provider_id: str | None = None,
        *,
        excluded_resource_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
        max_cost_minor: int | None = None,
    ) -> RouteRequest:
        """Translate the request's host policy into the compatibility route.

        The canonical ``ProviderDispatcher`` builds this filter itself.  The
        legacy direct-provider path still enters through this facade, so it
        must receive the same exact-tier and unknown-quota constraints rather
        than silently falling back to an unbounded provider-name lookup.
        """

        metadata = request.metadata
        allowed_tiers = None
        if metadata.get("intelligence_routing") == "bounded":
            if "allowed_intelligence_tiers" not in metadata:
                raise ValueError("bounded intelligence routing requires allowed_intelligence_tiers")
            allowed_tiers = metadata["allowed_intelligence_tiers"]
        return RouteRequest(
            capabilities=set(request.requested_capabilities) or {"text"},
            sensitivity=request.sensitivity,
            allowed_providers={provider_id} if provider_id is not None else None,
            max_cost_minor=max_cost_minor,
            excluded_resource_ids=set(excluded_resource_ids),
            allow_unknown_quota=metadata.get("allow_unknown_quota") is True,
            allowed_intelligence_tiers=allowed_tiers,
            allowed_provider_binding_ids=metadata.get("allowed_provider_binding_ids"),
            excluded_provider_binding_ids=metadata.get("excluded_provider_binding_ids", ()),
        )

    def select_route(
        self,
        request: ModelRequest,
        *,
        excluded_resource_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
        max_cost_minor: int | None = None,
        snapshot=None,
    ) -> RouteSelection:
        """Select a resource without exposing the Router to dispatchers.

        Routing policy is owned by the ResourceControlPlane boundary.  The
        optional snapshot lets a caller that already evaluated survival state
        reuse the same read, while the Router remains the only component that
        performs deterministic candidate selection.
        """

        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        try:
            route_request = self._route_request(
                request,
                excluded_resource_ids=excluded_resource_ids,
                max_cost_minor=max_cost_minor,
            )
        except ValueError as exc:
            raise DispatchDenied("invalid_request", str(exc)) from exc
        return self.router.choose(
            route_request,
            snapshot=self.routing_snapshot() if snapshot is None else snapshot,
        )

    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest, *, intent_key: str | None = None) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        try:
            selection = self.router.choose(self._route_request(request, provider_id))
            return self.reserve_selection(task_id, selection, intent_key=intent_key)
        except NoRoute as exc:
            raise DispatchDenied("no_route", str(exc)) from exc

    def reserve_selection(self, task_id: str, selection: RouteSelection, *, intent_key: str | None = None) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        price = None if selection.estimated_cost_minor is None or selection.price_currency is None else MoneyAmount(selection.price_currency, selection.estimated_cost_minor)
        unknown_quota_domain = selection.quota_domain if selection.unknown_quota else None
        unknown_quota_admitted = False
        if unknown_quota_domain is not None:
            admission = self.governor.ledger.claim_unknown_quota_admission(unknown_quota_domain)
            if not admission.admitted:
                retry_at = admission.retry_at_epoch
                raise DispatchDenied(
                    "quota_unknown",
                    f"unknown quota admission window is exhausted for {unknown_quota_domain}",
                    wake_at=retry_at,
                    wake_reason=unknown_quota_wake_reason(unknown_quota_domain),
                )
            unknown_quota_admitted = True
        try:
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost=price, intent_key=intent_key)
        except UnknownPrice as exc:
            if unknown_quota_admitted:
                self.governor.ledger.release_unknown_quota_admission(unknown_quota_domain)
            raise DispatchDenied("unknown_price", str(exc)) from exc
        except ResourceUnavailable as exc:
            if unknown_quota_admitted:
                self.governor.ledger.release_unknown_quota_admission(unknown_quota_domain)
            raise DispatchDenied("unavailable", str(exc)) from exc
        except MaintenanceActive as exc:
            if unknown_quota_admitted:
                self.governor.ledger.release_unknown_quota_admission(unknown_quota_domain)
            raise DispatchDenied("maintenance", str(exc)) from exc
        except BudgetExceeded as exc:
            if unknown_quota_admitted:
                self.governor.ledger.release_unknown_quota_admission(unknown_quota_domain)
            raise DispatchDenied("budget", str(exc)) from exc
        except ValueError as exc:
            if unknown_quota_admitted:
                self.governor.ledger.release_unknown_quota_admission(unknown_quota_domain)
            raise DispatchDenied("invalid_request", str(exc)) from exc
        return DispatchReservation(
            reservation,
            selection.provider_id,
            selection.native_unit,
            selection.estimated_cost_minor,
            selection.price_currency,
            selection.provider_binding_id,
            selection.model_id,
            unknown_quota_domain if unknown_quota_admitted else None,
        )

    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None:
        observed = response.usage.get("cost_minor")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            # A resource whose protected price is exactly zero has no charge
            # to reconcile even when a legacy/local adapter omits usage
            # metadata.  Paid reservations must never receive that implicit
            # default: their missing cost observation is an unresolved
            # external accounting outcome.
            if reservation.budget.estimated_cost_minor == 0:
                self.governor.reconcile(reservation.budget.reservation_id, actual_cost_minor=0)
                return
            self.governor.mark_unknown(reservation.budget.reservation_id)
            raise BudgetReconciliationRequired(
                "provider response did not include a valid non-negative usage.cost_minor"
            )
        try:
            self.governor.reconcile(reservation.budget.reservation_id, actual_cost=MoneyAmount(reservation.budget.estimated_cost.currency, observed))
        except BudgetExceeded:
            # The charge is known, but protected accounting cannot accept it.
            # Keep the reservation held for an explicit operator/provider
            # reconciliation rather than releasing capacity or losing the
            # evidence of an over-budget external effect.
            self.governor.mark_unknown(reservation.budget.reservation_id)
            raise

    def observe_provider_response(self, reservation: DispatchReservation, response: ModelResponse) -> bool:
        """Record provider liveness and ingest normalized quota telemetry.

        The response itself is an authoritative liveness observation even
        when the adapter has no quota headers.  Refreshing only the
        observation row keeps long-lived Operation processes routable without
        touching catalog/pricing/operator configuration.
        """
        self.governor.ledger.refresh_resource_observation(
            reservation.budget.resource_id,
            health="healthy",
        )
        return self.governor.ledger.ingest_quota_observation(reservation.budget.resource_id, response.usage)

    def mark_dispatching(self, reservation: DispatchReservation) -> None:
        self.governor.mark_dispatching(reservation.budget.reservation_id)

    def release(self, reservation: DispatchReservation) -> None:
        if reservation.unknown_quota_domain is not None:
            self.governor.ledger.release_unknown_quota_admission(reservation.unknown_quota_domain)
        self.governor.release(reservation.budget.reservation_id)

    def uncertain(self, reservation: DispatchReservation) -> None:
        self.governor.mark_unknown(reservation.budget.reservation_id)

    def record_provider_success(self, provider_id: str, *, resource_id: str | None = None, provider_binding_id: str | None = None) -> None:
        """Record provider health without exposing the ledger to callers."""
        self.governor.ledger.record_provider_success(provider_id, resource_id=resource_id, provider_binding_id=provider_binding_id)

    def record_provider_failure(self, provider_id: str, *, resource_id: str | None = None, provider_binding_id: str | None = None, threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        """Record provider health through the control-plane boundary."""
        self.governor.ledger.record_provider_failure(provider_id, resource_id=resource_id, provider_binding_id=provider_binding_id, threshold=threshold, cooldown_seconds=cooldown_seconds)

    def record_provider_error(self, provider_id: str, reservation: DispatchReservation, error: Exception) -> None:
        category = getattr(error, "category", "provider_error")
        requires_reconciliation = bool(getattr(error, "requires_reconciliation", False))
        decision = classify_provider_error(provider_id, error)
        if decision is not None:
            self.governor.ledger.record_quota_block(reservation.budget.resource_id, decision)
        if category in {"transport", "rate_limit", "quota"} or requires_reconciliation:
            self.record_provider_failure(
                provider_id,
                resource_id=reservation.budget.resource_id,
                provider_binding_id=reservation.provider_binding_id,
            )
        if requires_reconciliation:
            self.uncertain(reservation)
        else:
            self.governor.confirm_no_charge(reservation.budget.reservation_id)


__all__ = ["DispatchDenied", "DispatchReservation", "ResourceControlPlane", "ResourcePolicy"]
