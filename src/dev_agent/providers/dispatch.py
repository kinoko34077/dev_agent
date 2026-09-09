"""Runtime provider registry and resource-aware dispatch seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.budget import BudgetExceeded, BudgetReconciliationRequired
from ..resources.control import DispatchDenied, DispatchReservation, ResourceControlPlane
from ..resources.router import NoRoute, RouteRequest, RouteSelection
from ..resources.survival import SurvivalGovernor, SurvivalMode, SurvivalSnapshot
from .base import ModelProvider, ProviderError

if TYPE_CHECKING:
    from ..state.store import StateStore


class ProviderRegistry:
    def __init__(self, providers: list[ModelProvider] | tuple[ModelProvider, ...]) -> None:
        self._providers: dict[str, ModelProvider] = {}
        for provider in providers:
            if not isinstance(provider.provider_id, str) or not provider.provider_id.strip():
                raise ValueError("provider_id must be a non-empty string")
            if provider.provider_id in self._providers:
                raise ValueError(f"duplicate provider_id: {provider.provider_id}")
            self._providers[provider.provider_id] = provider
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
        self._state_store: StateStore | None = None
        self._lease_guard: Callable[[], None] | None = None
        self._lease_proof: Callable[[], Any | None] | None = None

    def bind_runtime(self, *, state_store: StateStore, lease_guard: Callable[[], None] | None = None, lease_proof: Callable[[], Any | None] | None = None) -> None:
        """Attach the durable runtime boundary used by Controller.

        Provider dispatches may be constructed independently for adapter tests,
        so the state store is optional.  When Controller owns this dispatcher,
        every concrete provider attempt gets a durable effect intent before the
        provider call starts.
        """
        self._state_store = state_store
        self._lease_guard = lease_guard
        self._lease_proof = lease_proof

    @staticmethod
    def _intent_key(request: ModelRequest, selection: RouteSelection) -> str:
        return f"provider:{request.request_id}:{selection.resource_id}"

    def _prepare_intent(self, request: ModelRequest, selection: RouteSelection) -> str | None:
        if self._state_store is None:
            return None
        key = self._intent_key(request, selection)
        intent = self._state_store.get_effect_intent(key)
        if intent is None:
            self._state_store.create_effect_intent(
                key,
                task_id=request.task_id,
                tool_name=f"provider:{selection.provider_id}",
                arguments={
                    "request_id": request.request_id,
                    "task_id": request.task_id,
                    "provider_id": selection.provider_id,
                    "resource_id": selection.resource_id,
                    "native_unit": selection.native_unit,
                    "estimated_cost_minor": selection.estimated_cost_minor,
                    "price_currency": selection.price_currency,
                    "max_output_tokens": request.max_output_tokens,
                },
            )
            self._state_store.transition_effect_intent(key, to_status="prepared")
            return key
        status = intent["status"]
        if status == "succeeded":
            return key
        if status in {"dispatching", "unknown", "reconciling"}:
            raise ProviderError("provider dispatch requires reconciliation", category="reconciliation_required", retryable=False)
        if status in {"confirmed_failed", "reconciled"}:
            raise ProviderError("provider dispatch was already finalized", category="reconciliation_required", retryable=False)
        return key

    def _intent(self, key: str | None, *, status: str, result: dict[str, Any]) -> None:
        if key is not None:
            proof = self._lease_proof() if self._lease_proof is not None and status in {"dispatching", "succeeded"} else None
            payload = dict(result)
            if status in {"dispatching", "succeeded"} and proof is not None:
                payload["dispatch_lease"] = {
                    "task_id": proof.task_id,
                    "worker_id": proof.worker_id,
                    "lease_token": proof.lease_token,
                    "state_version": proof.state_version,
                }
            self._state_store.transition_effect_intent(key, to_status=status, result=payload, lease_proof=proof)

    def _intent_result(self, key: str | None) -> ModelResponse | None:
        if key is None or self._state_store is None:
            return None
        intent = self._state_store.get_effect_intent(key)
        if intent is None or intent["status"] != "succeeded":
            return None
        result = intent.get("result") or {}
        response = result.get("response")
        if not isinstance(response, dict):
            raise ProviderError("durable provider result is malformed", category="provider_decode", retryable=False)
        return ModelResponse.from_dict(response)

    def _record_audit(self, request: ModelRequest, selection: RouteSelection, outcome: str, intent_key: str | None, *, details: dict[str, Any] | None = None) -> None:
        entry = DispatchAudit(selection.provider_id, selection.resource_id, outcome)
        if self._state_store is not None:
            self._state_store.record_provider_audit(
                task_id=request.task_id,
                request_id=request.request_id,
                intent_key=intent_key,
                provider_id=selection.provider_id,
                resource_id=selection.resource_id,
                native_unit=selection.native_unit,
                estimated_cost_minor=selection.estimated_cost_minor,
                price_currency=selection.price_currency,
                outcome=outcome,
                details=details,
            )
        # Keep the compatibility in-memory view only after the durable audit
        # has committed; it must never report a success that exists solely in
        # RAM after a persistence failure.
        self.audits.append(entry)

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
            provider = self.registry.get(selection.provider_id)
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
            reservation = self.control.reserve_selection(request.task_id, selection, intent_key=intent_key)
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
                response = provider.request(request)
            except ProviderError as exc:
                self.control.record_provider_error(selection.provider_id, reservation, exc)
                outcome = "unknown" if exc.category in {"transport", "provider_decode", "reconciliation_required"} else "confirmed_failed"
                self._intent(intent_key, status=outcome, result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": exc.category, "message": str(exc)})
                self._record_audit(request, selection, exc.category, intent_key, details={"category": exc.category, "retryable": exc.retryable})
                # A transport failure occurs after the concrete provider was
                # invoked.  Its external outcome is therefore ambiguous even
                # when the provider labels the error retryable; fail closed
                # and require reconciliation instead of sending a duplicate
                # request through another route.
                if exc.category == "transport":
                    raise
                last_error = exc
                excluded.add(selection.resource_id)
                if not exc.retryable:
                    raise
                continue
            except Exception as exc:
                self.control.uncertain(reservation)
                self.control.router.ledger.record_provider_failure(selection.provider_id)
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
                self.control.router.ledger.record_provider_failure(selection.provider_id)
                self._intent(intent_key, status="unknown", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "error_category": "provider_decode", "message": str(exc)})
                self._record_audit(request, selection, "provider_decode", intent_key, details={"category": "provider_decode"})
                raise ProviderError(f"provider response could not be decoded: {exc}", category="provider_decode", retryable=False) from exc
            try:
                self._intent(intent_key, status="succeeded", result={"provider_id": selection.provider_id, "resource_id": selection.resource_id, "outcome": "succeeded", "response": response.to_dict()})
                self.control.router.ledger.record_provider_success(selection.provider_id)
                self._record_audit(request, selection, "succeeded", intent_key)
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
