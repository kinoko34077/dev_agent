"""Durable provider dispatch intent and audit journal."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.router import RouteSelection
from .base import ProviderError

if TYPE_CHECKING:
    from ..state.store import StateStore


class ProviderDispatchJournal:
    """Own provider intent/replay/audit persistence behind one small seam."""

    def __init__(self) -> None:
        self._state_store: StateStore | None = None
        self._lease_proof: Callable[[], Any | None] | None = None

    def bind(
        self,
        *,
        state_store: StateStore | None = None,
        lease_proof: Callable[[], Any | None] | None = None,
    ) -> None:
        self._state_store = state_store
        self._lease_proof = lease_proof

    @staticmethod
    def intent_key(request: ModelRequest, selection: RouteSelection) -> str:
        return f"provider:{request.request_id}:{selection.resource_id}"

    def get_intent(self, key: str | None) -> dict[str, Any] | None:
        """Read one durable provider intent through the journal boundary."""

        if key is None or self._state_store is None:
            return None
        return self._state_store.get_effect_intent(key)

    def prepare_intent(self, request: ModelRequest, selection: RouteSelection) -> str | None:
        if self._state_store is None:
            return None
        key = self.intent_key(request, selection)
        intent = self.get_intent(key)
        if intent is None:
            self._state_store.create_effect_intent(
                key,
                task_id=request.task_id,
                tool_name=f"provider:{selection.provider_id}",
                arguments={
                    "request_id": request.request_id,
                    "task_id": request.task_id,
                    "provider_id": selection.provider_id,
                    "provider_binding_id": selection.provider_binding_id or selection.provider_id,
                    "model_id": selection.model_id,
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

    def transition(self, key: str | None, *, status: str, result: dict[str, Any]) -> None:
        if key is None or self._state_store is None:
            return
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

    def reconcile_result(
        self,
        key: str | None,
        *,
        result: dict[str, Any],
        actor: str = "runtime-late-provider",
        source: str = "provider_late_completion",
        external_id: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """Persist a late provider result through the reconciliation boundary.

        The original queue lease is intentionally not consulted here.  A
        timeout has already returned control to the runtime and the Python
        provider thread can finish only after that lease is gone.  The
        reconciliation record makes the result replayable while preserving
        the no-blind-retry rule.
        """

        if key is None or self._state_store is None:
            return
        self._state_store.reconcile_effect_result(
            key,
            status="succeeded",
            actor=actor,
            source=source,
            external_id=external_id,
            evidence=evidence,
            result=result,
        )

    def result(self, key: str | None) -> ModelResponse | None:
        intent = self.get_intent(key)
        if intent is None or intent["status"] != "succeeded":
            return None
        result = intent.get("result") or {}
        response = result.get("response")
        if not isinstance(response, dict):
            raise ProviderError("durable provider result is malformed", category="provider_decode", retryable=False)
        return ModelResponse.from_dict(response)

    def record_audit(
        self,
        request: ModelRequest,
        selection: RouteSelection,
        outcome: str,
        intent_key: str | None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._state_store is None:
            return
        audit_details = dict(details or {})
        audit_details.setdefault("provider_binding_id", selection.provider_binding_id or selection.provider_id)
        audit_details.setdefault("model_id", selection.model_id)
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
            details=audit_details,
        )


__all__ = ["ProviderDispatchJournal"]
