"""Runtime provider registry and resource-aware dispatch seam."""

from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from typing import Any, Callable, TYPE_CHECKING

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.budget import BudgetExceeded, BudgetReconciliationRequired
from ..resources.control import DispatchDenied, DispatchReservation, ResourceControlPlane
from ..resources.router import NoRoute, RouteSelection
from ..resources.survival import SurvivalGovernor, SurvivalMode, SurvivalSnapshot
from .base import ModelProvider, ProviderError
from .journal import ProviderDispatchJournal
from .registry import ProviderRegistry

if TYPE_CHECKING:
    from ..state.store import StateStore


@dataclass(frozen=True)
class DispatchAudit:
    provider_id: str
    resource_id: str
    outcome: str
    provider_binding_id: str | None = None
    model_id: str | None = None


class ProviderDispatcher(ModelProvider):
    """Canonical multi-provider runtime boundary.

    The dispatcher owns provider selection, durable provider intent/audit,
    fallback, resource reservation, and reconciliation before invoking the
    concrete provider registered in ProviderRegistry. Controller callers
    should use this path when resource-aware provider execution is required.
    """

    provider_id = "resource-router"
    PROVIDER_PATH_ROLE = "canonical_dispatcher_registry"
    handles_resource_policy = True

    def __init__(self, registry: ProviderRegistry, control: ResourceControlPlane, *, survival: SurvivalGovernor | None = None) -> None:
        self.registry = registry
        self.control = control
        self.survival = survival
        self.audits: list[DispatchAudit] = []
        self._journal = ProviderDispatchJournal()
        self._lease_guard: Callable[[], None] | None = None
        self._execution_callback: ContextVar[Callable[[ModelProvider, ModelRequest], ModelResponse] | None] = ContextVar(
            f"provider_dispatch_execution:{id(self)}", default=None
        )

    def bind_runtime(self, *, state_store: StateStore, lease_guard: Callable[[], None] | None = None, lease_proof: Callable[[], Any | None] | None = None) -> None:
        """Attach the durable runtime boundary used by Controller.

        Provider dispatches may be constructed independently for adapter tests,
        so the state store is optional.  When Controller owns this dispatcher,
        every concrete provider attempt gets a durable effect intent before the
        provider call starts.
        """
        self._journal.bind(state_store=state_store, lease_proof=lease_proof)
        self._lease_guard = lease_guard

    @staticmethod
    def _intent_key(request: ModelRequest, selection: RouteSelection) -> str:
        return ProviderDispatchJournal.intent_key(request, selection)

    def _prepare_intent(self, request: ModelRequest, selection: RouteSelection) -> str | None:
        return self._journal.prepare_intent(request, selection)

    def _intent(self, key: str | None, *, status: str, result: dict[str, Any]) -> None:
        self._journal.transition(key, status=status, result=result)

    def _intent_result(self, key: str | None) -> ModelResponse | None:
        return self._journal.result(key)

    def _record_audit(self, request: ModelRequest, selection: RouteSelection, outcome: str, intent_key: str | None, *, details: dict[str, Any] | None = None) -> None:
        entry = DispatchAudit(selection.provider_id, selection.resource_id, outcome, selection.provider_binding_id, selection.model_id)
        self._journal.record_audit(request, selection, outcome, intent_key, details=details)
        # Keep the compatibility in-memory view only after the durable audit
        # has committed; it must never report a success that exists solely in
        # RAM after a persistence failure.
        self.audits.append(entry)

    def request_with_execution(
        self,
        request: ModelRequest,
        *,
        execute: Callable[[ModelProvider, ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Run dispatch with a caller-owned, binding-scoped execution lane.

        The dispatcher remains the owner of selection, reservation, intent,
        fallback, and reconciliation.  The runtime supplies only the bounded
        invocation of the selected concrete binding, so an unkillable call in
        one binding cannot consume the timeout lane for the whole provider
        pool.  A ContextVar keeps this opt-in callback isolated when one
        dispatcher instance is shared by multiple workers.
        """

        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not callable(execute):
            raise TypeError("execute must be callable")
        token = self._execution_callback.set(execute)
        try:
            return self.request(request)
        finally:
            self._execution_callback.reset(token)

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
            except NoRoute as exc:
                if last_error is not None:
                    raise last_error
                raise DispatchDenied("no_route", str(exc)) from exc
            provider = self.registry.get_binding(selection.provider_binding_id or selection.provider_id)
            # Resolve the concrete provider and durable intent before
            # acquiring a budget/capacity reservation.  A replayed succeeded
            # intent must not create a fresh reservation, and a stale
            # resource observation must not strand one when registry and
            # ledger contents diverge.
            intent_key = self._prepare_intent(request, selection)
            cached = self._intent_result(intent_key)
            if cached is not None:
                self._record_audit(request, selection, "durable_replay", intent_key)
                return cached
            try:
                reservation = self.control.reserve_selection(request.task_id, selection, intent_key=intent_key)
            except DispatchDenied as exc:
                if exc.category == "quota_unknown":
                    # No provider call has started.  Exclude this local
                    # admission lane and try another eligible binding; if no
                    # lane remains, the original denial carries the durable
                    # wake boundary back to the Controller.
                    last_error = exc
                    excluded.add(selection.resource_id)
                    continue
                raise
            try:
                self.control.mark_dispatching(reservation)
            except BudgetReconciliationRequired as exc:
                self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "reconciliation_required", "message": str(exc)})
                self._record_audit(request, selection, "budget_reconciliation", intent_key, details={"category": "reconciliation_required"})
                raise ProviderError(str(exc), category="reconciliation_required", retryable=False) from exc
            try:
                self._intent(intent_key, status="dispatching", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id})
            except Exception as exc:
                self.control.release(reservation)
                self._intent(intent_key, status="confirmed_failed", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "lease_lost", "message": str(exc)})
                self._record_audit(request, selection, "lease_lost", intent_key, details={"category": "lease_lost"})
                raise ProviderError("provider dispatch rejected by stale lease", category="lease_lost", retryable=True) from exc
            try:
                if self._lease_guard is not None:
                    self._lease_guard()
            except Exception as exc:
                # The durable intent proves the boundary was prepared, while
                # this fencing check proves the concrete provider was not
                # entered by this worker.  Close the reservation as a
                # confirmed no-charge outcome and leave an auditable terminal
                # intent instead of reporting an external ambiguity.
                self.control.release(reservation)
                self._intent(intent_key, status="confirmed_failed", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "lease_lost", "message": str(exc)})
                self._record_audit(request, selection, "lease_lost", intent_key, details={"category": "lease_lost"})
                raise ProviderError("provider dispatch rejected by stale lease", category="lease_lost", retryable=True) from exc
            try:
                execute = self._execution_callback.get()
                response = execute(provider, request) if execute is not None else provider.request(request)
            except ProviderError as exc:
                self.control.record_provider_error(selection.provider_id, reservation, exc)
                outcome = "unknown" if exc.requires_reconciliation else "confirmed_failed"
                self._intent(intent_key, status=outcome, result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": exc.category, "message": str(exc)})
                self._record_audit(request, selection, exc.category, intent_key, details={"category": exc.category, "retryable": exc.retryable})
                # A transport failure occurs after the concrete provider was
                # invoked.  Its external outcome is therefore ambiguous even
                # when the provider labels the error retryable; fail closed
                # and require reconciliation instead of sending a duplicate
                # request through another route.
                if exc.requires_reconciliation:
                    raise
                last_error = exc
                excluded.add(selection.resource_id)
                if not exc.retryable:
                    raise
                continue
            except Exception as exc:
                if getattr(exc, "is_provider_execution_saturated", False) is True:
                    # The bounded binding executor rejected this call before
                    # entering the concrete provider.  It is therefore safe
                    # to close this reservation and try another binding for
                    # this request.  The timed-out call that caused the lane
                    # saturation remains tracked by that binding's executor.
                    self.control.release(reservation)
                    self._intent(
                        intent_key,
                        status="confirmed_failed",
                        result={
                            "provider_id": selection.provider_id,
                            "resource_id": selection.resource_id,
                            "error_category": "provider_execution_saturated",
                        },
                    )
                    self._record_audit(
                        request,
                        selection,
                        "provider_execution_saturated",
                        intent_key,
                        details={"category": "provider_execution_saturated", "external_call_started": False},
                    )
                    last_error = exc
                    excluded.add(selection.resource_id)
                    continue
                self.control.uncertain(reservation)
                self.control.record_provider_failure(
                    selection.provider_id,
                    resource_id=selection.resource_id,
                    provider_binding_id=selection.provider_binding_id,
                )
                self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "transport", "message": str(exc)})
                self._record_audit(request, selection, "transport", intent_key, details={"category": "transport"})
                # Once a concrete provider has been invoked, an untyped
                # exception still leaves the external outcome ambiguous. Do
                # not let Controller classify it as a local decode failure.
                raise ProviderError(f"provider transport failed: {exc}", category="transport", retryable=True) from exc
            try:
                if self._lease_guard is not None:
                    self._lease_guard()
            except Exception as exc:
                # A lease can expire while the concrete provider is running.
                # Its response is externally real but ownership is no longer
                # authoritative; keep the reservation and intent unknown.
                try:
                    self.control.uncertain(reservation)
                except Exception:
                    pass
                try:
                    self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "reconciliation_required", "cause": "lease_lost", "message": str(exc)})
                    self._record_audit(request, selection, "lease_lost_after_dispatch", intent_key, details={"category": "reconciliation_required", "cause": "lease_lost"})
                except Exception:
                    pass
                raise ProviderError("provider dispatch completed after lease loss; reconciliation required", category="reconciliation_required", retryable=False) from exc
            try:
                if not isinstance(response, ModelResponse):
                    raise TypeError("provider must return ModelResponse")
                if response.provider != selection.provider_id:
                    raise ValueError("provider response identity mismatch")
                if selection.model_id is not None and response.model != selection.model_id:
                    raise ValueError("provider response model identity mismatch")
                quota_observed = self.control.observe_provider_response(reservation, response)
                self.control.reconcile_response(reservation, response)
            except BudgetExceeded as exc:
                # The provider already returned an external result, but the
                # observed charge cannot be accepted by the protected budget.
                # ResourceControlPlane keeps the reservation unknown; expose
                # that ambiguity to Controller instead of misclassifying it
                # as a provider decode failure or retrying the request.
                self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "reconciliation_required", "message": str(exc)})
                self._record_audit(request, selection, "budget_reconciliation", intent_key, details={"category": "reconciliation_required"})
                raise ProviderError(str(exc), category="reconciliation_required", retryable=False) from exc
            except Exception as exc:
                self.control.uncertain(reservation)
                self.control.record_provider_failure(
                    selection.provider_id,
                    resource_id=selection.resource_id,
                    provider_binding_id=selection.provider_binding_id,
                )
                self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "provider_decode", "message": str(exc)})
                self._record_audit(request, selection, "provider_decode", intent_key, details={"category": "provider_decode"})
                raise ProviderError(f"provider response could not be decoded: {exc}", category="provider_decode", retryable=False) from exc
            try:
                self._intent(intent_key, status="succeeded", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "outcome": "succeeded", "response": response.to_dict()})
                self.control.record_provider_success(
                    selection.provider_id,
                    resource_id=selection.resource_id,
                    provider_binding_id=selection.provider_binding_id,
                )
                self._record_audit(request, selection, "succeeded", intent_key, details={"quota_observed": quota_observed})
            except Exception as exc:
                # The concrete provider has already returned and the budget
                # result was accepted.  Losing the durable result/audit here
                # must not become a local provider_decode failure: a retry
                # could duplicate a paid external request.  Keep the intent
                # boundary fail-closed and let Controller persist a waiting
                # reconciliation state.
                raise ProviderError(
                    f"provider result persistence requires reconciliation: {exc}",
                    category="reconciliation_required",
                    retryable=False,
                ) from exc
            return response

    def _selection(self, request: ModelRequest, excluded: set[str]) -> RouteSelection:
        snapshot = self.control.routing_snapshot()
        max_cost_minor = None
        if self.survival is not None:
            budget = self.control.budget_snapshot()
            healthy = sum(resource["health"] in {"healthy", "degraded"} for resource in snapshot.resources)
            state = self.survival.evaluate(SurvivalSnapshot(budget["normal_available_minor"], budget["recovery_available_minor"], healthy))
            if state.mode in {SurvivalMode.CONSERVE, SurvivalMode.SURVIVAL}:
                # Paid normal dispatch is prohibited in constrained modes.
                # Recovery-only paid work requires a distinct future request type.
                max_cost_minor = 0
        return self.control.select_route(
            request,
            excluded_resource_ids=excluded,
            max_cost_minor=max_cost_minor,
            snapshot=snapshot,
        )
