"""Controller-facing reservation seam for Phase 6 resource enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.protocol import ModelRequest, ModelResponse
from .budget import BudgetExceeded, BudgetGovernor, BudgetReservation, ResourceUnavailable, UnknownPrice
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


class ResourcePolicy(Protocol):
    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest) -> DispatchReservation: ...
    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None: ...
    def release(self, reservation: DispatchReservation) -> None: ...
    def uncertain(self, reservation: DispatchReservation) -> None: ...


class ResourceControlPlane:
    def __init__(self, router: ResourceRouter, governor: BudgetGovernor) -> None:
        self.router = router
        self.governor = governor
        self._maintenance = False

    def set_maintenance(self, enabled: bool) -> None:
        self._maintenance = bool(enabled)

    def _ensure_dispatch_allowed(self) -> None:
        if self._maintenance:
            raise DispatchDenied("maintenance", "new provider dispatch is disabled during maintenance")

    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        try:
            selection = self.router.choose(RouteRequest(capabilities=set(request.requested_capabilities) or {"text"}, sensitivity=request.sensitivity, allowed_providers={provider_id}))
            price = None if selection.estimated_cost_minor is None or selection.price_currency is None else MoneyAmount(selection.price_currency, selection.estimated_cost_minor)
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost=price)
        except NoRoute as exc:
            raise DispatchDenied("no_route", str(exc)) from exc
        except UnknownPrice as exc:
            raise DispatchDenied("unknown_price", str(exc)) from exc
        except ResourceUnavailable as exc:
            raise DispatchDenied("unavailable", str(exc)) from exc
        except BudgetExceeded as exc:
            raise DispatchDenied("budget", str(exc)) from exc
        except ValueError as exc:
            raise DispatchDenied("invalid_request", str(exc)) from exc
        return DispatchReservation(reservation, provider_id)

    def reserve_selection(self, task_id: str, selection: RouteSelection) -> DispatchReservation:
        self._ensure_dispatch_allowed()
        price = None if selection.estimated_cost_minor is None or selection.price_currency is None else MoneyAmount(selection.price_currency, selection.estimated_cost_minor)
        try:
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost=price)
        except UnknownPrice as exc:
            raise DispatchDenied("unknown_price", str(exc)) from exc
        except ResourceUnavailable as exc:
            raise DispatchDenied("unavailable", str(exc)) from exc
        except BudgetExceeded as exc:
            raise DispatchDenied("budget", str(exc)) from exc
        return DispatchReservation(reservation, selection.provider_id)

    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None:
        observed = response.usage.get("cost_minor")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            self.governor.mark_unknown(reservation.budget.reservation_id)
            return
        self.governor.reconcile(reservation.budget.reservation_id, actual_cost=MoneyAmount(reservation.budget.estimated_cost.currency, observed))

    def release(self, reservation: DispatchReservation) -> None:
        self.governor.release(reservation.budget.reservation_id)

    def uncertain(self, reservation: DispatchReservation) -> None:
        self.governor.mark_unknown(reservation.budget.reservation_id)

    def record_provider_error(self, provider_id: str, reservation: DispatchReservation, error: Exception) -> None:
        category = getattr(error, "category", "provider_error")
        if category in {"transport", "rate_limit", "quota"}:
            self.router.ledger.record_provider_failure(provider_id)
        if category == "transport":
            self.uncertain(reservation)
        else:
            self.release(reservation)


__all__ = ["DispatchDenied", "DispatchReservation", "ResourceControlPlane", "ResourcePolicy"]
