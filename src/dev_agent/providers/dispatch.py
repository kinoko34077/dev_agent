"""Runtime provider registry and resource-aware dispatch seam."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.control import DispatchReservation, ResourceControlPlane
from ..resources.router import NoRoute, RouteRequest, RouteSelection
from ..resources.survival import SurvivalGovernor, SurvivalMode, SurvivalSnapshot
from .base import ModelProvider, ProviderError


class ProviderRegistry:
    def __init__(self, providers: list[ModelProvider] | tuple[ModelProvider, ...]) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}
        if not self._providers:
            raise ValueError("at least one provider is required")

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderError(f"unsupported provider: {provider_id}", category="unsupported_capability", retryable=False) from exc


@dataclass(frozen=True)
class DispatchAudit:
    provider_id: str
    resource_id: str
    outcome: str


class ProviderDispatcher(ModelProvider):
    """Select, reserve, invoke, and account for a concrete provider."""

    provider_id = "resource-router"
    handles_resource_policy = True

    def __init__(self, registry: ProviderRegistry, control: ResourceControlPlane, *, survival: SurvivalGovernor | None = None) -> None:
        self.registry = registry
        self.control = control
        self.survival = survival
        self.audits: list[DispatchAudit] = []

    def request(self, request_or_task_id: ModelRequest | str, explicit_request: ModelRequest | None = None) -> ModelResponse:
        """Dispatch one request, accepting both canonical and legacy call shapes.

        The provider protocol exposes ``request(ModelRequest)``.  Phase 6's
        dispatcher plan also described an explicit ``(task_id, request)``
        boundary, so accept that shape without allowing the two identities to
        diverge.
        """
        if explicit_request is None:
            if not isinstance(request_or_task_id, ModelRequest):
                raise TypeError("request must be a ModelRequest")
            request = request_or_task_id
        else:
            if not isinstance(request_or_task_id, str):
                raise TypeError("explicit task_id must be a string")
            if request_or_task_id != explicit_request.task_id:
                raise ValueError("explicit task_id does not match request.task_id")
            request = explicit_request
        excluded: set[str] = set()
        last_error: ProviderError | None = None
        while True:
            try:
                selection = self._selection(request, excluded)
            except NoRoute:
                if last_error is not None:
                    raise last_error
                raise
            provider = self.registry.get(selection.provider_id)
            # Resolve the concrete provider before acquiring a budget/capacity
            # reservation.  A stale resource observation must not strand a
            # reservation when registry and ledger contents diverge.
            reservation = self.control.reserve_selection(request.task_id, selection)
            try:
                response = provider.request(request)
            except ProviderError as exc:
                self.control.record_provider_error(selection.provider_id, reservation, exc)
                self.audits.append(DispatchAudit(selection.provider_id, selection.resource_id, exc.category))
                last_error = exc
                excluded.add(selection.resource_id)
                if not exc.retryable:
                    raise
                continue
            except Exception:
                self.control.uncertain(reservation)
                self.control.router.ledger.record_provider_failure(selection.provider_id)
                raise
            self.control.reconcile_response(reservation, response)
            self.control.router.ledger.record_provider_success(selection.provider_id)
            self.audits.append(DispatchAudit(selection.provider_id, selection.resource_id, "succeeded"))
            return response

    def _selection(self, request: ModelRequest, excluded: set[str]) -> RouteSelection:
        max_cost_minor = None
        if self.survival is not None:
            budget = self.control.governor.snapshot()
            healthy = sum(resource["health"] in {"healthy", "degraded"} for resource in self.control.router.ledger.list_resources())
            state = self.survival.evaluate(SurvivalSnapshot(budget["normal_available_minor"], budget["recovery_available_minor"], healthy))
            if state.mode in {SurvivalMode.CONSERVE, SurvivalMode.SURVIVAL}:
                # Paid normal dispatch is prohibited in constrained modes.
                # Recovery-only paid work requires a distinct future request type.
                max_cost_minor = 0
        return self.control.router.choose(RouteRequest(capabilities=set(request.requested_capabilities) or {"text"}, sensitivity=request.sensitivity, excluded_resource_ids=excluded, max_cost_minor=max_cost_minor))
