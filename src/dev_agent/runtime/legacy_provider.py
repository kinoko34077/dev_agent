"""Compatibility-only persistence for the direct ModelProvider path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ProviderError
from ..resources.budget import BudgetReconciliationRequired

if TYPE_CHECKING:
    from ..state.store import StateStore


class LegacyDirectProviderJournal:
    """Keep the pre-dispatcher provider path behind one explicit boundary.

    The canonical multi-provider path uses ``ProviderDispatchJournal``.  This
    journal exists only for callers that still construct a Controller around a
    single ModelProvider, and must not become a second provider-routing path.
    """

    def __init__(
        self,
        state_store: StateStore,
        *,
        provider_id: str,
        lease_proof: Callable[[], Any | None],
    ) -> None:
        self._state_store = state_store
        self._provider_id = provider_id
        self._lease_proof = lease_proof

    def prepare_intent(self, request: ModelRequest, reservation: Any, *, intent_key: str | None = None) -> str:
        key = intent_key or f"provider:{request.request_id}:{reservation.budget.resource_id}"
        intent = self._state_store.get_effect_intent(key)
        if intent is None:
            self._state_store.create_effect_intent(
                key,
                task_id=request.task_id,
                tool_name=f"provider:{self._provider_id}",
                arguments={
                    "request_id": request.request_id,
                    "task_id": request.task_id,
                    "provider_id": self._provider_id,
                    "resource_id": reservation.budget.resource_id,
                    "native_unit": getattr(reservation, "native_unit", "request"),
                    "estimated_cost_minor": getattr(reservation, "estimated_cost_minor", reservation.budget.estimated_cost_minor),
                    "price_currency": getattr(reservation, "price_currency", reservation.budget.estimated_cost.currency),
                    "max_output_tokens": request.max_output_tokens,
                },
            )
            self._state_store.transition_effect_intent(key, to_status="prepared")
        elif intent.get("status") == "pending":
            self._state_store.transition_effect_intent(key, to_status="prepared")
        return key

    def record_audit(
        self,
        request: ModelRequest,
        reservation: Any,
        outcome: str,
        intent_key: str | None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        recorder = getattr(self._state_store, "record_provider_audit", None)
        if recorder is None:
            return
        recorder(
            task_id=request.task_id,
            request_id=request.request_id,
            intent_key=intent_key,
            provider_id=self._provider_id,
            resource_id=reservation.budget.resource_id,
            native_unit=getattr(reservation, "native_unit", "request"),
            estimated_cost_minor=getattr(reservation, "estimated_cost_minor", reservation.budget.estimated_cost_minor),
            price_currency=getattr(reservation, "price_currency", reservation.budget.estimated_cost.currency),
            outcome=outcome,
            details=details,
        )

    def transition(self, key: str | None, *, status: str, result: dict[str, Any]) -> None:
        if key is None:
            return
        proof = self._lease_proof() if status in {"dispatching", "succeeded"} else None
        payload = dict(result)
        if status in {"dispatching", "succeeded"} and proof is not None:
            payload["dispatch_lease"] = {
                "task_id": proof.task_id,
                "worker_id": proof.worker_id,
                "lease_token": proof.lease_token,
                "state_version": proof.state_version,
            }
        self._state_store.transition_effect_intent(key, to_status=status, result=payload, lease_proof=proof)

    def replay(self, key: str) -> ModelResponse:
        intent = self._state_store.get_effect_intent(key)
        if intent is None or intent.get("status") != "succeeded":
            raise ProviderError("durable provider result is not replayable", category="provider_decode", retryable=False)
        result = intent.get("result") or {}
        response = result.get("response") if isinstance(result, dict) else None
        if not isinstance(response, dict):
            raise ProviderError("durable provider result is malformed", category="provider_decode", retryable=False)
        try:
            return ModelResponse.from_dict(response)
        except Exception as exc:
            raise ProviderError("durable provider result is malformed", category="provider_decode", retryable=False) from exc


@dataclass(frozen=True)
class LegacyProviderPreparation:
    """Result of preparing one compatibility-provider dispatch."""

    status: str
    intent_key: str | None = None
    reservation: Any | None = None
    replayed_response: ModelResponse | None = None
    cause: str | None = None
    category: str | None = None
    message: str | None = None


class LegacyDirectProviderExecutor:
    """Own the resource/intent preparation boundary of the legacy path.

    The canonical multi-provider path is still owned by ProviderDispatcher.
    This object exists only to keep compatibility preparation out of
    Controller.  Provider execution and task-state transitions remain
    explicit at the Controller boundary until the next refactor slice.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        provider: Any,
        resource_policy: Any | None,
        state_store: StateStore,
        prepare_intent: Callable[..., str],
        record_audit: Callable[..., None],
        transition_intent: Callable[..., None],
        replay: Callable[[str], ModelResponse],
    ) -> None:
        self._provider_id = provider_id
        self._provider = provider
        self._resource_policy = resource_policy
        self._state_store = state_store
        self._prepare_intent = prepare_intent
        self._record_audit = record_audit
        self._transition_intent = transition_intent
        self._replay = replay

    def applies(self) -> bool:
        return self._resource_policy is not None and not getattr(self._provider, "handles_resource_policy", False)

    def prepare(self, request: ModelRequest) -> LegacyProviderPreparation:
        if not self.applies():
            return LegacyProviderPreparation(status="not_applicable")

        intent_key = f"provider:{request.request_id}:{self._provider_id}"
        existing_intent = self._state_store.get_effect_intent(intent_key)
        existing_status = existing_intent.get("status") if existing_intent is not None else None
        if existing_status in {"dispatching", "unknown", "reconciling"}:
            return LegacyProviderPreparation(
                status="waiting_reconciliation",
                intent_key=intent_key,
                cause="provider_intent_pending",
                message="provider effect intent requires reconciliation before retry",
            )
        if existing_status in {"confirmed_failed", "reconciled"}:
            return LegacyProviderPreparation(
                status="terminal",
                intent_key=intent_key,
                category="provider_effect_terminal",
                message="provider effect intent is already terminal",
            )
        if existing_status == "succeeded":
            return LegacyProviderPreparation(
                status="replay",
                intent_key=intent_key,
                replayed_response=self._replay(intent_key),
            )

        try:
            reservation = self._resource_policy.reserve_for_provider(
                request.task_id,
                self._provider_id,
                request,
                intent_key=intent_key,
            )
            intent_key = self._prepare_intent(request, reservation, intent_key=intent_key)
            self._resource_policy.mark_dispatching(reservation)
            self._transition_intent(
                intent_key,
                status="dispatching",
                result={"provider_id": self._provider_id, "resource_id": reservation.budget.resource_id},
            )
            self._record_audit(request, reservation, "dispatching", intent_key)
            return LegacyProviderPreparation(status="dispatch", intent_key=intent_key, reservation=reservation)
        except BudgetReconciliationRequired as exc:
            return LegacyProviderPreparation(
                status="waiting_reconciliation",
                intent_key=intent_key,
                cause="budget_reconciliation",
                message=str(exc),
            )
        except Exception as exc:
            return LegacyProviderPreparation(status="blocked_budget", message=str(exc))


__all__ = ["LegacyDirectProviderExecutor", "LegacyDirectProviderJournal", "LegacyProviderPreparation"]
