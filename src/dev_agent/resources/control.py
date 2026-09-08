"""Controller-facing reservation seam for Phase 6 resource enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.protocol import ModelRequest, ModelResponse
from .budget import BudgetGovernor, BudgetReservation
from .router import NoRoute, ResourceRouter, RouteRequest


class DispatchDenied(RuntimeError):
    """A provider dispatch cannot be started under current resource policy."""


@dataclass(frozen=True)
class DispatchReservation:
    budget: BudgetReservation
    provider_id: str


class ResourceControlPlane:
    def __init__(self, router: ResourceRouter, governor: BudgetGovernor) -> None:
        self.router = router
        self.governor = governor

    def reserve_for_provider(self, task_id: str, provider_id: str, request: ModelRequest) -> DispatchReservation:
        try:
            selection = self.router.choose(RouteRequest(capabilities=set(request.requested_capabilities) or {"text"}, sensitivity=request.sensitivity, allowed_providers={provider_id}, max_cost_minor=int(request.cost_ceiling)))
            reservation = self.governor.reserve(task_id, selection.resource_id, estimated_cost_minor=selection.estimated_cost_minor)
        except Exception as exc:
            if isinstance(exc, (NoRoute, RuntimeError, ValueError)):
                raise DispatchDenied(str(exc)) from exc
            raise
        return DispatchReservation(reservation, provider_id)

    def reconcile_response(self, reservation: DispatchReservation, response: ModelResponse) -> None:
        observed = response.usage.get("cost_minor")
        actual = reservation.budget.estimated_cost_minor if observed is None else observed
        self.governor.reconcile(reservation.budget.reservation_id, actual_cost_minor=actual)

    def release(self, reservation: DispatchReservation) -> None:
        self.governor.release(reservation.budget.reservation_id)

    def uncertain(self, reservation: DispatchReservation) -> None:
        self.governor.mark_unknown(reservation.budget.reservation_id)


__all__ = ["DispatchDenied", "DispatchReservation", "ResourceControlPlane"]
