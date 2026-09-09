"""Controller-facing reservation seam for Phase 6 resource enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.protocol import ModelRequest, ModelResponse
from .budget import BudgetExceeded, BudgetGovernor, BudgetReconciliationRequired, BudgetReservation, MaintenanceActive, ResourceUnavailable, UnknownPrice
from .ledger import MoneyAmount
from .router import NoRoute, ResourceRouter, RouteRequest, RouteSelection


class DispatchDenied(RuntimeError):
    """A provider dispatch cannot be started under current resource policy."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class DispatchReservation:
    budget: BudgetReservation
    provider_id: str
    native_unit: str = "request"
    estimated_cost_minor: int | None = None
    price_currency: str | None = None


class ResourcePolicy(Protocol):
    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest, *, intent_key: str | None = None) -> DispatchReservation: ...
    def mark_dispatching(self, reservation: DispatchReservation) -> None: ...
    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None: ...
    def observe_provider_response(self, reservation: DispatchReservation, response: ModelResponse) -> bool: ...
    def release(self, reservation: DispatchReservation) -> None: ...
    def uncertain(self, reservation: DispatchReservation) -> None: ...
    def record_provider_success(self, provider_id: str) -> None: ...
    def record_provider_failure(self, provider_id: str, *, threshold: int = 3, cooldown_seconds: float = 60.0) -> None: ...


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

    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest, *, intent_key: str | None = None) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        try:
            selection = self.router.choose(RouteRequest(capabilities=set(request.requested_capabilities) or {"text"}, sensitivity=request.sensitivity, allowed_providers={provider_id}))
            price = None if selection.estimated_cost_minor is None or selection.price_currency is None else MoneyAmount(selection.price_currency, selection.estimated_cost_minor)
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost=price, intent_key=intent_key)
        except NoRoute as exc:
            raise DispatchDenied("no_route", str(exc)) from exc
        except UnknownPrice as exc:
            raise DispatchDenied("unknown_price", str(exc)) from exc
        except ResourceUnavailable as exc:
            raise DispatchDenied("unavailable", str(exc)) from exc
        except MaintenanceActive as exc:
            raise DispatchDenied("maintenance", str(exc)) from exc
        except BudgetExceeded as exc:
            raise DispatchDenied("budget", str(exc)) from exc
        except ValueError as exc:
            raise DispatchDenied("invalid_request", str(exc)) from exc
        return DispatchReservation(reservation, provider_id, selection.native_unit, selection.estimated_cost_minor, selection.price_currency)

    def reserve_selection(self, task_id: str, selection: RouteSelection, *, intent_key: str | None = None) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        price = None if selection.estimated_cost_minor is None or selection.price_currency is None else MoneyAmount(selection.price_currency, selection.estimated_cost_minor)
        try:
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost=price, intent_key=intent_key)
        except UnknownPrice as exc:
            raise DispatchDenied("unknown_price", str(exc)) from exc
        except ResourceUnavailable as exc:
            raise DispatchDenied("unavailable", str(exc)) from exc
        except MaintenanceActive as exc:
            raise DispatchDenied("maintenance", str(exc)) from exc
        except BudgetExceeded as exc:
            raise DispatchDenied("budget", str(exc)) from exc
        return DispatchReservation(reservation, selection.provider_id, selection.native_unit, selection.estimated_cost_minor, selection.price_currency)

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
        """Ingest only the normalized, provider-neutral quota telemetry."""
        return self.governor.ledger.ingest_quota_observation(reservation.budget.resource_id, response.usage)

    def mark_dispatching(self, reservation: DispatchReservation) -> None:
        self.governor.mark_dispatching(reservation.budget.reservation_id)

    def release(self, reservation: DispatchReservation) -> None:
        self.governor.release(reservation.budget.reservation_id)

    def uncertain(self, reservation: DispatchReservation) -> None:
        self.governor.mark_unknown(reservation.budget.reservation_id)

    def record_provider_success(self, provider_id: str) -> None:
        """Record provider health without exposing the ledger to callers."""
        self.governor.ledger.record_provider_success(provider_id)

    def record_provider_failure(self, provider_id: str, *, threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        """Record provider health through the control-plane boundary."""
        self.governor.ledger.record_provider_failure(provider_id, threshold=threshold, cooldown_seconds=cooldown_seconds)

    def record_provider_error(self, provider_id: str, reservation: DispatchReservation, error: Exception) -> None:
        category = getattr(error, "category", "provider_error")
        requires_reconciliation = bool(getattr(error, "requires_reconciliation", False))
        if category in {"transport", "rate_limit", "quota"} or requires_reconciliation:
            self.record_provider_failure(provider_id)
        if requires_reconciliation:
            self.uncertain(reservation)
        else:
            self.governor.confirm_no_charge(reservation.budget.reservation_id)


__all__ = ["DispatchDenied", "DispatchReservation", "ResourceControlPlane", "ResourcePolicy"]
