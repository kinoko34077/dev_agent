"""Explicit, bounded execution of an approved escalation handoff.

The executor is deliberately outside ``Controller``.  It consumes a durable
``EscalationDispatchRequest``, revalidates the persisted review and task
state, then delegates the actual Provider work to the canonical
``ProviderDispatcher``.  The existing effect-intent state machine is used for
the dispatch identity; no new副作用 ledger or provider bypass is introduced.

This boundary does not complete a Task or promote a model.  The caller still
owns the subsequent host evaluation and Task lifecycle transition.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable

from ..domain.protocol import IntelligenceTier, ModelRequest, ModelResponse, Task, TaskStatus
from ..providers.base import ProviderError
from ..resources.control import DispatchDenied
from ..state.store import StateStore
from .escalation import EscalationDispatchRequest, EscalationTarget
from .evaluator import EvaluatorDecision
from .policy import TaskIntelligencePolicy
from .routing import IntelligenceRoutePolicy


_TIER_ORDER = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)
_DISPATCHABLE_TASK_STATES = frozenset({TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.FAILED})
_UNCERTAIN_INTENT_STATES = frozenset({"dispatching", "unknown", "reconciling"})
_FINAL_INTENT_STATES = frozenset({"confirmed_failed", "reconciled"})


class EscalationExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"
    CONFIRMED_FAILED = "confirmed_failed"


class EscalationExecutionDenied(RuntimeError):
    """The approved handoff failed a pre-dispatch safety recheck."""

    def __init__(self, message: str, *, category: str = "dispatch_denied") -> None:
        super().__init__(message)
        self.category = category


class EscalationExecutionError(RuntimeError):
    """The handoff was started but cannot safely be treated as complete."""

    def __init__(self, message: str, *, category: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable

    @property
    def requires_reconciliation(self) -> bool:
        return self.category == "reconciliation_required"


@dataclass(frozen=True)
class EscalationExecutionResult:
    dispatch_id: str
    plan_id: str
    task_id: str
    attempt: int
    status: EscalationExecutionStatus
    response: ModelResponse | None = None
    provider_binding_id: str | None = None
    error_category: str | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "status": self.status.value,
            "response": self.response.to_dict() if self.response is not None else None,
            "provider_binding_id": self.provider_binding_id,
            "error_category": self.error_category,
            "replayed": self.replayed,
        }


class EscalationExecutor:
    """Execute one explicitly approved, identity-bound escalation request."""

    def __init__(
        self,
        store: StateStore,
        dispatcher,
        *,
        intelligence_policy: TaskIntelligencePolicy | None = None,
        lease_guard: Callable[[], None] | None = None,
        lease_proof: Any | None = None,
        actor: str = "host-escalation-executor",
    ) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        self._store = store
        self._dispatcher = dispatcher
        self._intelligence_policy = intelligence_policy or TaskIntelligencePolicy()
        self._lease_guard = lease_guard
        self._lease_proof = lease_proof
        self._actor = actor.strip()

    def execute(
        self,
        dispatch_request: EscalationDispatchRequest,
        model_request: ModelRequest,
    ) -> EscalationExecutionResult:
        """Revalidate and execute one handoff through ProviderDispatcher.

        ``model_request`` supplies the already-approved conversation/tool
        input.  Its request id is replaced with the durable dispatch id so
        ProviderDispatcher's existing intent/audit path is tied to this
        handoff.  No provider instance, credential, or raw provider object is
        accepted in ``EscalationDispatchRequest``.
        """
        self._validate_inputs(dispatch_request, model_request)
        if dispatch_request.task_id != model_request.task_id:
            raise EscalationExecutionDenied("model request task does not match dispatch request", category="identity")
        task = self._load_dispatchable_task(dispatch_request.task_id)
        self._validate_ready_handoff(dispatch_request)
        intelligence = self._validate_intelligence(task, dispatch_request)
        if dispatch_request.attempt > task.limits.max_retries + 1:
            raise EscalationExecutionDenied("dispatch attempt exceeds task retry ceiling", category="task_limits")
        derived_request = self._build_request(dispatch_request, model_request, task, intelligence)
        self._guard_before_dispatch()

        intent_key = f"escalation:{dispatch_request.dispatch_id}"
        intent = self._ensure_intent(dispatch_request, intent_key)
        status = intent.get("status")
        if status == "succeeded":
            response = self._response_from_intent(intent)
            return EscalationExecutionResult(
                dispatch_id=dispatch_request.dispatch_id,
                plan_id=dispatch_request.plan_id,
                task_id=dispatch_request.task_id,
                attempt=dispatch_request.attempt,
                status=EscalationExecutionStatus.SUCCEEDED,
                response=response,
                provider_binding_id=dispatch_request.provider_binding_id,
                replayed=True,
            )
        if status in _UNCERTAIN_INTENT_STATES:
            raise EscalationExecutionError(
                "escalation dispatch requires reconciliation",
                category="reconciliation_required",
            )
        if status in _FINAL_INTENT_STATES:
            raise EscalationExecutionError(
                "escalation dispatch was already finalized",
                category="dispatch_finalized",
            )
        if status != "prepared":
            raise EscalationExecutionDenied(
                f"escalation intent is not ready: {status}",
                category="intent_state",
            )

        dispatch_payload = self._identity_payload(dispatch_request)
        try:
            self._store.transition_effect_intent(
                intent_key,
                to_status="dispatching",
                result=dispatch_payload,
                lease_proof=self._lease_proof,
            )
        except Exception as exc:
            raise EscalationExecutionDenied(
                f"escalation dispatch could not claim its intent: {exc}",
                category="lease_or_intent",
            ) from exc
        self._record_event("escalation.dispatching", dispatch_request, dispatch_payload)

        try:
            self._guard_before_dispatch()
            response = self._dispatcher.request(derived_request)
            if not isinstance(response, ModelResponse):
                raise TypeError("provider dispatcher must return ModelResponse")
            self._guard_after_dispatch()
        except ProviderError as exc:
            if exc.requires_reconciliation:
                return self._hold_unknown(dispatch_request, intent_key, exc)
            self._finalize_known_failure(dispatch_request, intent_key, exc)
            raise EscalationExecutionError(
                str(exc),
                category=exc.category,
                retryable=exc.retryable,
            ) from exc
        except DispatchDenied as exc:
            self._finalize_known_failure(dispatch_request, intent_key, exc)
            raise EscalationExecutionError(str(exc), category=exc.category) from exc
        except EscalationExecutionError as exc:
            if exc.requires_reconciliation:
                return self._hold_unknown(dispatch_request, intent_key, exc)
            raise
        except Exception as exc:
            return self._hold_unknown(dispatch_request, intent_key, exc)

        result = EscalationExecutionResult(
            dispatch_id=dispatch_request.dispatch_id,
            plan_id=dispatch_request.plan_id,
            task_id=dispatch_request.task_id,
            attempt=dispatch_request.attempt,
            status=EscalationExecutionStatus.SUCCEEDED,
            response=response,
            provider_binding_id=dispatch_request.provider_binding_id,
        )
        try:
            self._store.transition_effect_intent(
                intent_key,
                to_status="succeeded",
                result={"dispatch": result.to_dict(), "response": response.to_dict()},
                lease_proof=self._lease_proof,
            )
        except Exception as exc:
            return self._hold_unknown(dispatch_request, intent_key, exc)
        self._record_event("escalation.succeeded", dispatch_request, result.to_dict())
        return result

    @staticmethod
    def _validate_inputs(dispatch_request: EscalationDispatchRequest, model_request: ModelRequest) -> None:
        if not isinstance(dispatch_request, EscalationDispatchRequest):
            raise TypeError("dispatch_request must be EscalationDispatchRequest")
        if not isinstance(model_request, ModelRequest):
            raise TypeError("model_request must be ModelRequest")

    def _load_dispatchable_task(self, task_id: str) -> Task:
        task = self._store.load_task(task_id)
        if task is None:
            raise EscalationExecutionDenied("task state is missing", category="task_state")
        if task.status not in _DISPATCHABLE_TASK_STATES:
            raise EscalationExecutionDenied(
                f"task state is not dispatchable: {task.status.value}",
                category="task_state",
            )
        return task

    def _validate_ready_handoff(self, dispatch_request: EscalationDispatchRequest) -> None:
        snapshot = self._store.snapshot()
        events = snapshot.get("events", []) if isinstance(snapshot, dict) else []
        accepted = False
        ready = False
        expected = dispatch_request.to_dict()
        for item in events:
            if not isinstance(item, dict) or item.get("task_id") != dispatch_request.task_id:
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            if item.get("event_type") == "escalation.accepted":
                accepted = accepted or (
                    payload.get("review") == "accepted"
                    and payload.get("plan_id") == dispatch_request.plan_id
                    and payload.get("actor") == dispatch_request.approved_by
                    and payload.get("approval_reference") == dispatch_request.approval_reference
                )
            if item.get("event_type") == "escalation.dispatch_ready":
                recorded = payload.get("request")
                ready = ready or (
                    payload.get("status") == "ready"
                    and payload.get("plan_id") == dispatch_request.plan_id
                    and payload.get("dispatch_id") == dispatch_request.dispatch_id
                    and isinstance(recorded, dict)
                    and recorded == expected
                )
        if not accepted:
            raise EscalationExecutionDenied("dispatch_ready has no matching accepted review", category="approval")
        if not ready:
            raise EscalationExecutionDenied("dispatch_ready identity is missing or mismatched", category="dispatch_ready")

    def _validate_intelligence(self, task: Task, request: EscalationDispatchRequest):
        decision = self._intelligence_policy.decide(task)
        if request.current_tier is not None and _TIER_ORDER.index(request.current_tier) < _TIER_ORDER.index(decision.minimum_tier):
            raise EscalationExecutionDenied("current intelligence tier is below task policy minimum", category="intelligence_policy")
        if request.allowed_tiers is not None:
            if any(_TIER_ORDER.index(tier) < _TIER_ORDER.index(decision.minimum_tier) for tier in request.allowed_tiers):
                raise EscalationExecutionDenied("allowed intelligence tiers violate task policy minimum", category="intelligence_policy")
        if request.target is EscalationTarget.HIGHER_TIER:
            if request.current_tier is None or request.allowed_tiers is None or request.next_tier not in request.allowed_tiers:
                raise EscalationExecutionDenied("higher-tier dispatch lacks a durable allowed-tier bound", category="intelligence_policy")
        return decision

    @staticmethod
    def _build_request(
        dispatch_request: EscalationDispatchRequest,
        model_request: ModelRequest,
        task: Task,
        intelligence,
    ) -> ModelRequest:
        metadata = dict(model_request.metadata)
        metadata.update(
            {
                "intelligence_routing": "bounded",
                "escalation_dispatch_id": dispatch_request.dispatch_id,
                "escalation_plan_id": dispatch_request.plan_id,
                "escalation_attempt": dispatch_request.attempt,
            }
        )
        if dispatch_request.target is EscalationTarget.HIGHER_TIER:
            allowed_tiers = (dispatch_request.next_tier.value,)
            selected_tier = dispatch_request.next_tier
        else:
            tier = dispatch_request.current_tier or intelligence.minimum_tier
            allowed_tiers = (tier.value,)
            selected_tier = tier
        metadata["allowed_intelligence_tiers"] = list(allowed_tiers)
        difficult = any(reason in {"risk:high", "risk:critical"} for reason in intelligence.reasons)
        metadata["thinking_effort"] = IntelligenceRoutePolicy.thinking_effort_for_tier(selected_tier, difficult=difficult)
        metadata["minimum_thinking_effort"] = metadata["thinking_effort"]
        if dispatch_request.target is EscalationTarget.SAME_PROVIDER:
            if dispatch_request.provider_binding_id is None:
                raise EscalationExecutionDenied("same-provider retry requires provider_binding_id", category="provider_binding")
            metadata["allowed_provider_binding_ids"] = [dispatch_request.provider_binding_id]
            metadata.pop("excluded_provider_binding_ids", None)
        elif dispatch_request.target is EscalationTarget.OTHER_PROVIDER:
            if dispatch_request.provider_binding_id is None:
                raise EscalationExecutionDenied("other-provider retry requires source provider_binding_id", category="provider_binding")
            metadata["excluded_provider_binding_ids"] = [dispatch_request.provider_binding_id]
            metadata.pop("allowed_provider_binding_ids", None)
        else:
            metadata.pop("allowed_provider_binding_ids", None)
            metadata.pop("excluded_provider_binding_ids", None)
        capabilities = list(model_request.requested_capabilities)
        for capability in task.required_capabilities:
            if capability not in capabilities:
                capabilities.append(capability)
        return replace(
            model_request,
            request_id=dispatch_request.dispatch_id,
            requested_capabilities=capabilities,
            metadata=metadata,
        )

    def _ensure_intent(self, request: EscalationDispatchRequest, key: str) -> dict[str, Any]:
        intent = self._store.get_effect_intent(key)
        if intent is None:
            created = self._store.create_effect_intent(
                key,
                task_id=request.task_id,
                tool_name="provider:escalation",
                arguments=self._identity_payload(request),
            )
            if created:
                self._store.transition_effect_intent(
                    key,
                    to_status="prepared",
                    result={"status": "ready", **self._identity_payload(request)},
                )
            intent = self._store.get_effect_intent(key)
        if not isinstance(intent, dict):
            raise EscalationExecutionDenied("escalation intent could not be loaded", category="intent_state")
        if intent.get("task_id") != request.task_id or intent.get("tool_name") != "provider:escalation":
            raise EscalationExecutionDenied("escalation intent identity does not match", category="identity")
        arguments = intent.get("arguments")
        if not isinstance(arguments, dict) or arguments != self._identity_payload(request):
            raise EscalationExecutionDenied("escalation intent payload does not match", category="identity")
        return intent

    @staticmethod
    def _identity_payload(request: EscalationDispatchRequest) -> dict[str, Any]:
        payload = request.to_dict()
        return {
            "dispatch_id": payload["dispatch_id"],
            "plan_id": payload["plan_id"],
            "task_id": payload["task_id"],
            "attempt": payload["attempt"],
            "decision": payload["decision"],
            "target": payload["target"],
            "next_tier": payload["next_tier"],
            "provider_binding_id": payload["provider_binding_id"],
            "current_tier": payload["current_tier"],
            "allowed_tiers": payload["allowed_tiers"],
        }

    def _guard_before_dispatch(self) -> None:
        if self._lease_guard is not None:
            try:
                self._lease_guard()
            except Exception as exc:
                raise EscalationExecutionDenied("lease ownership is no longer valid", category="lease_lost") from exc

    def _guard_after_dispatch(self) -> None:
        if self._lease_guard is not None:
            try:
                self._lease_guard()
            except Exception as exc:
                raise EscalationExecutionError(
                    "provider response arrived after lease loss",
                    category="reconciliation_required",
                ) from exc

    def _response_from_intent(self, intent: dict[str, Any]) -> ModelResponse:
        result = intent.get("result")
        response = result.get("response") if isinstance(result, dict) else None
        if not isinstance(response, dict):
            raise EscalationExecutionError("durable escalation result is malformed", category="reconciliation_required")
        try:
            return ModelResponse.from_dict(response)
        except Exception as exc:
            raise EscalationExecutionError("durable escalation response is malformed", category="reconciliation_required") from exc

    def _hold_unknown(
        self,
        request: EscalationDispatchRequest,
        key: str,
        error: Exception,
    ) -> EscalationExecutionResult:
        category = getattr(error, "category", "reconciliation_required")
        try:
            self._store.transition_effect_intent(
                key,
                to_status="unknown",
                result={
                    **self._identity_payload(request),
                    "error_category": "reconciliation_required",
                    "cause": category,
                    "message": str(error),
                },
            )
        except Exception as transition_error:
            raise EscalationExecutionError(
                f"escalation outcome is unresolved: {transition_error}",
                category="reconciliation_required",
            ) from error
        result = EscalationExecutionResult(
            dispatch_id=request.dispatch_id,
            plan_id=request.plan_id,
            task_id=request.task_id,
            attempt=request.attempt,
            status=EscalationExecutionStatus.UNKNOWN,
            provider_binding_id=request.provider_binding_id,
            error_category=category,
        )
        self._record_event("escalation.unknown", request, result.to_dict())
        raise EscalationExecutionError(
            f"escalation outcome requires reconciliation: {error}",
            category="reconciliation_required",
        ) from error

    def _finalize_known_failure(self, request: EscalationDispatchRequest, key: str, error: Exception) -> None:
        category = getattr(error, "category", "dispatch_denied")
        self._store.transition_effect_intent(
            key,
            to_status="confirmed_failed",
            result={
                **self._identity_payload(request),
                "error_category": category,
                "message": str(error),
            },
        )
        self._record_event(
            "escalation.confirmed_failed",
            request,
            {**self._identity_payload(request), "error_category": category},
        )

    def _record_event(self, event_type: str, request: EscalationDispatchRequest, payload: dict[str, Any]) -> None:
        from ..domain.protocol import Event

        self._store.append_event(
            Event(
                event_type=event_type,
                task_id=request.task_id,
                provider=self._actor,
                request_id=request.dispatch_id,
                payload={"actor": self._actor, **payload},
            )
        )


__all__ = [
    "EscalationExecutionDenied",
    "EscalationExecutionError",
    "EscalationExecutionResult",
    "EscalationExecutionStatus",
    "EscalationExecutor",
]
