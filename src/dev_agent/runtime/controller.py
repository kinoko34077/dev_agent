"""Bounded, checkpoint-resumable execution controller."""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
from threading import Event
from time import monotonic, time
from typing import Any, Callable

from ..domain.protocol import Event as ProtocolEvent
from ..domain.protocol import ModelRequest, ModelResponse, Step, StepStatus, Task, TaskStatus, ToolCall, ToolResult, ToolResultStatus
from ..providers.base import ModelProvider, ProviderError
from ..security.event_artifacts import EventArtifactStore
from ..security.audit import AuditRecorder
from ..resources.control import DispatchDenied, ResourcePolicy
from ..resources.budget import BudgetExceeded, BudgetReconciliationRequired
from ..state.store import StateStore
from ..tools.runtime import ToolRuntime
from ..intelligence import IntelligenceRoutePolicy, TaskIntelligencePolicy
from ..intelligence.capabilities import execution_capabilities
from .legacy_provider import LegacyDirectProviderExecutor, LegacyDirectProviderJournal
from .model_turn import ModelTurnExecutor, ProviderExecutionSaturated, ProviderRequestCancelled
from .state import RuntimeState


class RuntimeFailure(RuntimeError):
    """A terminal, normalized runtime failure."""


_ProviderCancelled = ProviderRequestCancelled


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable per-run ownership and fencing context.

    A Controller may be shared by multiple workers.  Lease state therefore
    cannot live in mutable Controller attributes: one worker must never be
    able to replace another worker's guard or proof while it is dispatching.
    The context is installed only in the calling execution context and is
    visible to provider callbacks through ``ContextVar`` isolation.
    """

    lease_guard: Callable[[], None] | None = None
    lease_proof: Any | None = None


class Controller:
    # New resource-aware provider integrations use
    # Controller -> ProviderDispatcher -> ProviderRegistry. The direct
    # provider branch below is retained only for compatibility with existing
    # ModelProvider callers that predate the dispatcher boundary.
    DIRECT_PROVIDER_PATH_ROLE = "compatibility_legacy"

    # Keep the historic names available to callers while the implementation
    # lives in the single canonical AuditRecorder boundary.
    MAX_EVENT_STRING_CHARS = AuditRecorder.MAX_STRING_CHARS
    MAX_EVENT_PAYLOAD_BYTES = AuditRecorder.MAX_PAYLOAD_BYTES
    EVENT_ARTIFACT_RETENTION_SECONDS = AuditRecorder.RETENTION_SECONDS
    _SECRET_KEY_WORDS = tuple(AuditRecorder.SECRET_KEYS)
    _SECRET_PATTERNS = AuditRecorder.SECRET_PATTERNS

    def __init__(self, provider: ModelProvider, tools: ToolRuntime, store: StateStore, *, event_artifacts: EventArtifactStore | None = None, resource_policy: ResourcePolicy | None = None, lease_guard: Callable[[], None] | None = None, lease_proof: Any | None = None, intelligence_policy: TaskIntelligencePolicy | None = None, intelligence_routing: bool = False, allow_unknown_quota: bool = False) -> None:
        self.provider = provider
        self.tools = tools.bound_to(store)
        self.store = store
        self.event_artifacts = event_artifacts
        self.resource_policy = resource_policy
        self.intelligence_policy = intelligence_policy or TaskIntelligencePolicy()
        if not isinstance(intelligence_routing, bool):
            raise TypeError("intelligence_routing must be a boolean")
        if not isinstance(allow_unknown_quota, bool):
            raise TypeError("allow_unknown_quota must be a boolean")
        self.intelligence_routing = intelligence_routing
        self.allow_unknown_quota = allow_unknown_quota
        self._default_execution_context = ExecutionContext(lease_guard=lease_guard, lease_proof=lease_proof)
        self._execution_context: ContextVar[ExecutionContext | None] = ContextVar(
            f"dev_agent_execution_context:{id(self)}", default=None
        )
        self._cancellation_events: dict[str, Event] = {}
        self._cancellation_reasons: dict[str, str] = {}
        self._active_tasks: set[str] = set()
        self._running_tasks: dict[str, Task] = {}
        self._model_turn_executor = ModelTurnExecutor(self.provider, lease_guard=self._active_lease_guard)
        self._legacy_provider_journal = LegacyDirectProviderJournal(
            store,
            provider_id=self.provider.provider_id,
            lease_proof=self._active_lease_proof,
        )
        self._legacy_provider_executor = LegacyDirectProviderExecutor(
            provider_id=self.provider.provider_id,
            provider=self.provider,
            resource_policy=resource_policy,
            state_store=store,
            prepare_intent=lambda request, reservation, **kwargs: self._prepare_provider_intent(request, reservation, **kwargs),
            record_audit=lambda request, reservation, outcome, intent_key, **kwargs: self._record_provider_audit(request, reservation, outcome, intent_key, **kwargs),
            transition_intent=lambda key, **kwargs: self._provider_intent(key, **kwargs),
            replay=lambda key: self._provider_replay(key),
            request_provider=lambda request, deadline, cancel_event: self._provider_request(request, deadline, cancel_event),
            lease_guard=self._active_lease_guard,
            has_lease_guard=lambda: self.lease_guard is not None,
        )
        binder = getattr(provider, "bind_runtime", None)
        if callable(binder):
            binder(state_store=store, lease_guard=self._active_lease_guard, lease_proof=self._active_lease_proof)

    def _current_execution_context(self) -> ExecutionContext:
        return self._execution_context.get() or self._default_execution_context

    def _active_lease_guard(self) -> None:
        guard = self._current_execution_context().lease_guard
        if guard is not None:
            guard()

    def _active_lease_proof(self) -> Any | None:
        return self._current_execution_context().lease_proof

    @property
    def lease_guard(self) -> Callable[[], None] | None:
        """Read-only compatibility view of the current run's lease guard."""
        return self._current_execution_context().lease_guard

    @property
    def lease_proof(self) -> Any | None:
        """Read-only compatibility view of the current run's lease proof."""
        return self._current_execution_context().lease_proof

    def _event_record(self, task: Task, event_type: str, payload: dict[str, Any], *, step_id: str | None = None, request_id: str | None = None) -> ProtocolEvent:
        return ProtocolEvent(event_type=event_type, task_id=task.task_id, step_id=step_id, request_id=request_id, provider=self.provider.provider_id, payload=AuditRecorder.sanitize_payload(payload, artifact_store=self.event_artifacts))

    def _event(self, task: Task, event_type: str, payload: dict[str, Any], *, step_id: str | None = None, request_id: str | None = None) -> None:
        """Append a non-transition event for compatibility with callers."""
        self.store.append_event(self._event_record(task, event_type, payload, step_id=step_id, request_id=request_id))

    @classmethod
    def _safe_event_payload(cls, payload: dict[str, Any], *, artifact_store: EventArtifactStore | None = None) -> dict[str, Any]:
        """Compatibility entry point backed by the canonical audit sanitizer."""
        return AuditRecorder.sanitize_payload(payload, artifact_store=artifact_store)

    @staticmethod
    def _checkpoint_payload(task: Task, step: Step, phase: str, state: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": task.task_id, "step_id": step.step_id, "phase": phase, "state": state}

    def _commit(self, *, task: Task | None = None, step: Step | None = None, checkpoint: dict[str, Any] | None = None, events: list[ProtocolEvent] | None = None, tool_result: ToolResult | None = None) -> None:
        self._active_lease_guard()
        self.store.commit_transition(task=task, step=step, checkpoint=checkpoint, events=events, tool_result=tool_result, lease_proof=self._active_lease_proof())

    @staticmethod
    def _kernel_operation_key(task: Task, step: Step, call: ToolCall, index: int, *, effective_arguments: dict[str, Any] | None = None) -> str:
        canonical = json.dumps({"tool_name": call.tool_name, "arguments": effective_arguments if effective_arguments is not None else call.arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"op:{task.task_id}:{step.order}:{index}:{digest}"

    def _prepare_provider_intent(self, request: ModelRequest, reservation: Any, *, intent_key: str | None = None) -> str:
        return self._legacy_provider_journal.prepare_intent(request, reservation, intent_key=intent_key)

    def _record_provider_audit(self, request: ModelRequest, reservation: Any, outcome: str, intent_key: str | None, *, details: dict[str, Any] | None = None) -> None:
        self._legacy_provider_journal.record_audit(request, reservation, outcome, intent_key, details=details)

    def _provider_intent(self, key: str | None, *, status: str, result: dict[str, Any]) -> None:
        self._legacy_provider_journal.transition(key, status=status, result=result)

    def _provider_replay(self, key: str) -> ModelResponse:
        return self._legacy_provider_journal.replay(key)

    def _fail(self, task: Task, state: dict[str, Any], category: str, message: str, *, step: Step | None = None, request_id: str | None = None, tool_result: ToolResult | None = None, extra_events: list[ProtocolEvent] | None = None) -> None:
        if step is not None:
            step.status = StepStatus.FAILED
            state["active_step"] = step.to_dict()
        task.status = TaskStatus.FAILED
        events = list(extra_events or [])
        events.append(self._event_record(task, "task.failed", {"category": category, "message": message}, step_id=step.step_id if step else None, request_id=request_id))
        checkpoint = self._checkpoint_payload(task, step, "failure", state) if step is not None else None
        self._commit(task=task, step=step, checkpoint=checkpoint, events=events, tool_result=tool_result)
        raise RuntimeFailure(f"{category}: {message}")

    def _cancel(self, task: Task, state: dict[str, Any], *, step: Step | None = None, message: str = "task execution was cancelled", tool_result: ToolResult | None = None, extra_events: list[ProtocolEvent] | None = None) -> None:
        message = self._cancellation_reasons.get(task.task_id, task.metadata.get("cancellation_reason", message))
        if step is not None:
            step.status = StepStatus.CANCELLED
            state["active_step"] = step.to_dict()
        state["cancellation"] = {
            "state": "terminated",
            "reason": message,
            "requested": True,
        }
        task.status = TaskStatus.CANCELLED
        events = list(extra_events or [])
        events.append(self._event_record(task, "task.cancelled", {"category": "cancelled", "message": message, "cancellation_state": "terminated"}, step_id=step.step_id if step else None))
        checkpoint = self._checkpoint_payload(task, step, "cancelled", state) if step is not None else None
        self._commit(task=task, step=step, checkpoint=checkpoint, events=events, tool_result=tool_result)

    def _cancel_unable_to_confirm(self, task: Task, state: dict[str, Any], *, step: Step, message: str) -> None:
        """Persist an ambiguous cancellation while a provider may still run."""
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        state["cancellation"] = {
            "state": "unable_to_confirm",
            "reason": self._cancellation_reasons.get(task.task_id, message),
            "requested": True,
            "source": "provider_request",
        }
        task.status = TaskStatus.WAITING_RECONCILIATION
        event = self._event_record(
            task,
            "task.waiting_reconciliation",
            {
                "category": "cancelled",
                "message": message,
                "source": "provider_request",
                "cancellation_state": "unable_to_confirm",
            },
            step_id=step.step_id,
        )
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_reconciliation", state), events=[event])

    def _block_budget(self, task: Task, state: dict[str, Any], *, step: Step, message: str) -> None:
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        task.status = TaskStatus.BLOCKED_BUDGET
        event = self._event_record(task, "task.blocked_budget", {"category": "budget", "message": message}, step_id=step.step_id)
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "blocked_budget", state), events=[event])

    def _block_quota(self, task: Task, state: dict[str, Any], *, step: Step, message: str) -> None:
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        task.status = TaskStatus.BLOCKED_QUOTA
        event = self._event_record(task, "task.blocked_quota", {"category": "quota", "message": message}, step_id=step.step_id)
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "blocked_quota", state), events=[event])

    def _wait_for_resource(self, task: Task, state: dict[str, Any], *, step: Step, category: str, message: str) -> None:
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        task.metadata["wait_reason"] = f"resource:{category}"
        task.status = TaskStatus.WAITING_DEPENDENCY
        event = self._event_record(
            task,
            "task.waiting_resource",
            {"category": category, "message": message, "wait_reason": f"resource:{category}"},
            step_id=step.step_id,
        )
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_resource", state), events=[event])

    def _wait_for_maintenance(self, task: Task, state: dict[str, Any], *, step: Step, message: str) -> None:
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        task.metadata["wait_reason"] = "maintenance"
        task.status = TaskStatus.WAITING_DEPENDENCY
        event = self._event_record(
            task,
            "task.waiting_maintenance",
            {"category": "maintenance", "message": message, "wait_reason": "maintenance"},
            step_id=step.step_id,
        )
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_maintenance", state), events=[event])

    def _handle_dispatch_denied(self, task: Task, state: dict[str, Any], *, step: Step, error: DispatchDenied) -> Task:
        """Map resource-policy denial to its real durable task meaning."""

        category = error.category.strip().lower() if isinstance(error.category, str) else "dispatch_denied"
        if category == "budget":
            self._block_budget(task, state, step=step, message=str(error))
            return task
        if category in {"quota", "rate_limit"}:
            self._block_quota(task, state, step=step, message=str(error))
            return task
        if category == "maintenance":
            self._wait_for_maintenance(task, state, step=step, message=str(error))
            return task
        if category in {"no_route", "unavailable"}:
            self._wait_for_resource(task, state, step=step, category=category, message=str(error))
            return task
        self._fail(task, state, category, str(error), step=step)
        return task

    @staticmethod
    def _initial_state(task: Task) -> RuntimeState:
        return RuntimeState.initial(task, now=time())

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        try:
            size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            return 0
        return (size + 3) // 4

    @staticmethod
    def _usage_number(usage: dict[str, Any], names: tuple[str, ...]) -> float | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                return float(value)
        return None

    @classmethod
    def _response_output_tokens(cls, response: ModelResponse) -> int:
        observed = cls._usage_number(response.usage, ("output_tokens", "completion_tokens", "candidatesTokenCount", "eval_count"))
        if observed is not None:
            return max(0, int(observed))
        return cls._estimate_tokens({"text_segments": response.text_segments, "parts": response.parts, "tool_calls": [call.to_dict() for call in response.tool_calls], "structured_output": response.structured_output})

    @staticmethod
    def _ensure_state_defaults(state: dict[str, Any]) -> None:
        state.setdefault("input_tokens_used", 0)
        state.setdefault("output_tokens_used", 0)
        state.setdefault("cost_used", 0.0)
        state.setdefault("retries_used", 0)
        state.setdefault("active_request_id", None)

    def _provider_request(self, request: ModelRequest, deadline_epoch: float, cancel_event: Event) -> ModelResponse:
        return self._model_turn_executor.request(request, deadline_epoch, cancel_event)

    def cancel(self, task_id: str, *, reason: str = "task cancellation requested") -> Task:
        """Request cooperative cancellation and persist it when not running."""
        event = self._cancellation_events.get(task_id)
        if event is not None:
            event.set()
            self._cancellation_reasons[task_id] = reason
            task = self._running_tasks.get(task_id)
            if task is None:
                task = self.store.load_task(task_id)
            if task is None:
                raise RuntimeFailure(f"task not found: {task_id}")
            requester = getattr(self.store, "request_cancellation", None)
            if callable(requester) and task.status is TaskStatus.RUNNING:
                # The control record is written through the StateStore's
                # transaction owner, so a separate API process cannot lose
                # the request to the worker's later terminal write.
                task = requester(task_id, reason=reason)
                self._event(
                    task,
                    "task.cancellation_requested",
                    {"category": "cancelled", "message": reason, "cancellation_state": "requested"},
                )
            return task
        task = self.store.load_task(task_id)
        if task is None:
            raise RuntimeFailure(f"task not found: {task_id}")
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.WAITING_RECONCILIATION}:
            return task
        if task.status is TaskStatus.RUNNING:
            # A CLI/API process may not share the in-memory cancellation
            # Event with the WorkerRunner process.  Persist only a request;
            # the owning run loop will observe it and preserve the existing
            # unknown/unable_to_confirm semantics at the provider boundary.
            requester = getattr(self.store, "request_cancellation", None)
            if callable(requester):
                task = requester(task_id, reason=reason)
                self._event(
                    task,
                    "task.cancellation_requested",
                    {"category": "cancelled", "message": reason, "cancellation_state": "requested"},
                )
                return task
            task.metadata["cancellation_requested"] = True
            task.metadata["cancellation_reason"] = reason
            self._commit(
                task=task,
                events=[
                    self._event_record(
                        task,
                        "task.cancellation_requested",
                        {"category": "cancelled", "message": reason, "cancellation_state": "requested"},
                    )
                ],
            )
            return task
        task.status = TaskStatus.CANCELLED
        self._commit(task=task, events=[self._event_record(task, "task.cancelled", {"category": "cancelled", "message": reason, "cancellation_state": "terminated"})])
        return task

    def resume(self, task_id: str, *, approval_id: str | None = None, execution_context: ExecutionContext | None = None) -> Task:
        context = execution_context or self._current_execution_context()
        token = self._execution_context.set(context)
        try:
            return self._resume(task_id, approval_id=approval_id)
        finally:
            self._execution_context.reset(token)

    def _resume(self, task_id: str, *, approval_id: str | None = None) -> Task:
        task = self.store.load_task(task_id)
        if task is None:
            raise RuntimeFailure(f"task not found: {task_id}")
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        checkpoint = self.store.load_latest_checkpoint(task_id)
        if checkpoint and approval_id:
            checkpoint["state"]["approval_id"] = approval_id
        if checkpoint and checkpoint["phase"] == "after_model":
            task.status = TaskStatus.COMPLETED
            events = []
            if not self.store.has_event(task.task_id, "task.completed"):
                events.append(self._event_record(task, "task.completed", {"recovered": True}, step_id=checkpoint["step_id"]))
            self._commit(task=task, events=events)
            return task
        if checkpoint and checkpoint["phase"] == "failure":
            task.status = TaskStatus.FAILED
            events = []
            if not self.store.has_event(task.task_id, "task.failed"):
                events.append(self._event_record(task, "task.failed", {"recovered": True, "category": "recovered_failure"}, step_id=checkpoint["step_id"]))
            self._commit(task=task, events=events)
            return task
        if checkpoint and checkpoint["phase"] == "cancelled":
            task.status = TaskStatus.CANCELLED
            self._commit(task=task)
            return task
        if checkpoint and checkpoint["phase"] == "waiting_reconciliation" and (
            checkpoint.get("state", {}).get("provider_reconciliation")
            or checkpoint.get("state", {}).get("cancellation", {}).get("source") == "provider_request"
        ):
            # A provider request may have reached the external service even
            # though its local result was lost.  Do not spend a second
            # external request until an explicit reconciliation path clears
            # this marker.
            task.status = TaskStatus.WAITING_RECONCILIATION
            return task
        return self.run(task, state=checkpoint["state"] if checkpoint else None)

    def _execute_pending(self, task: Task, state: dict[str, Any], cancel_event: Event) -> None:
        step = Step.from_dict(state["active_step"])
        for call in [ToolCall.from_dict(item) for item in state["pending_tool_calls"]]:
            if cancel_event.is_set():
                self._cancel(task, state, step=step)
                return
            self._active_lease_guard()
            result = self.tools.execute(call, task_id=task.task_id, approval_id=state.get("approval_id"), cancel_event=cancel_event)
            result.provider_call_id = call.provider_call_id
            tool_event = self._event_record(task, "tool.completed", {"result": result.to_dict()}, step_id=step.step_id)
            if result.status != ToolResultStatus.SUCCEEDED:
                error = result.error or {"category": "tool_execution", "message": "tool failed"}
                category = error.get("category", "tool_execution")
                if category == "approval_required":
                    step.status = StepStatus.WAITING
                    state["active_step"] = step.to_dict()
                    task.status = TaskStatus.WAITING_APPROVAL
                    waiting_event = self._event_record(task, "task.waiting_approval", {"tool_call_id": call.call_id, "tool_name": call.tool_name}, step_id=step.step_id)
                    self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_approval", state), events=[tool_event, waiting_event], tool_result=result)
                    return
                if category == "reconciliation_required":
                    step.status = StepStatus.WAITING
                    state["active_step"] = step.to_dict()
                    cancellation_state = None
                    if error.get("cause") == "cancelled":
                        cancellation_state = "unable_to_confirm"
                        state["cancellation"] = {
                            "state": cancellation_state,
                            "reason": self._cancellation_reasons.get(task.task_id, "cancellation occurred after guarded dispatch"),
                            "requested": True,
                        }
                    task.status = TaskStatus.WAITING_RECONCILIATION
                    waiting_payload = {"tool_call_id": call.call_id, "tool_name": call.tool_name, "cause": error.get("cause")}
                    if cancellation_state is not None:
                        waiting_payload["cancellation_state"] = cancellation_state
                    waiting_event = self._event_record(task, "task.waiting_reconciliation", waiting_payload, step_id=step.step_id)
                    self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_reconciliation", state), events=[tool_event, waiting_event], tool_result=result)
                    return
                if category == "cancelled":
                    self._cancel(task, state, step=step, tool_result=result, extra_events=[tool_event])
                    return
                self._fail(task, state, category, error.get("message", "tool failed"), step=step, tool_result=result, extra_events=[tool_event])
            state["tool_results"].append(result.to_dict())
            state["pending_tool_calls"] = [item for item in state["pending_tool_calls"] if item["call_id"] != call.call_id]
            state["active_step"] = step.to_dict()
            self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "after_tool_result", state), events=[tool_event], tool_result=result)
        step.status = StepStatus.COMPLETED
        state["next_step_order"] += 1
        state["active_step"] = None
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "after_tools", state))

    def run(self, task: Task, *, state: dict[str, Any] | None = None, execution_context: ExecutionContext | None = None) -> Task:
        context = execution_context or self._current_execution_context()
        token = self._execution_context.set(context)
        try:
            return self._run(task, state=state)
        finally:
            self._execution_context.reset(token)

    def _run(self, task: Task, *, state: dict[str, Any] | None = None) -> Task:
        state = RuntimeState.from_checkpoint(state) if state is not None else self._initial_state(task)
        self._ensure_state_defaults(state)
        if "deadline_epoch" not in state:
            state["deadline_epoch"] = time() + task.limits.max_wall_time_seconds
        cancel_event = self._cancellation_events.setdefault(task.task_id, Event())
        self._active_tasks.add(task.task_id)
        self._running_tasks[task.task_id] = task
        try:
            persisted = self.store.load_task(task.task_id)
            if persisted is not None:
                if persisted.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    return persisted
                if persisted.metadata.get("cancellation_requested"):
                    task.metadata.update(persisted.metadata)
                    cancel_event.set()
            task.status = TaskStatus.RUNNING
            # Register the cancellation event before the first durable write so
            # an API/UI cancellation racing with startup cannot be overwritten
            # by a later RUNNING record.
            self._commit(task=task)
            while task.status == TaskStatus.RUNNING:
                persisted = self.store.load_task(task.task_id)
                if persisted is not None and persisted.metadata.get("cancellation_requested"):
                    task.metadata.update(persisted.metadata)
                    cancel_event.set()
                if cancel_event.is_set():
                    step = Step.from_dict(state["active_step"]) if state.get("active_step") else None
                    self._cancel(task, state, step=step)
                    return task
                if time() >= state["deadline_epoch"]:
                    self._fail(task, state, "timeout", "task wall-clock limit exceeded")
                if state["pending_tool_calls"]:
                    self._execute_pending(task, state, cancel_event)
                    continue
                if state["next_step_order"] >= task.limits.max_steps or state["model_calls"] >= task.limits.max_model_calls:
                    self._fail(task, state, "limits_exceeded", "execution limits exceeded")
                step = None
                if state.get("active_step"):
                    candidate = Step.from_dict(state["active_step"])
                    if candidate.task_id == task.task_id and candidate.order == state["next_step_order"] and candidate.status in {StepStatus.PENDING, StepStatus.RUNNING}:
                        step = candidate
                        step.status = StepStatus.RUNNING
                        step.attempt = max(1, step.attempt)
                if step is None:
                    step = Step(task_id=task.task_id, order=state["next_step_order"], kind="model", status=StepStatus.RUNNING, attempt=1)
                state["active_step"] = step.to_dict()
                replaying_request = bool(state.get("active_request_id"))
                try:
                    intelligence = self.intelligence_policy.decide(task)
                    request_metadata = {
                        "task_type": task.task_type.value,
                        "risk": task.risk.value,
                        "minimum_intelligence_tier": intelligence.minimum_tier.value,
                        "maximum_intelligence_tier": intelligence.maximum_tier.value,
                        "current_intelligence_tier": intelligence.current_tier.value,
                        "escalation_intelligence_tiers": [tier.value for tier in intelligence.escalation_tiers],
                        "allowed_intelligence_tiers": [tier.value for tier in intelligence.allowed_tiers],
                        "requires_human_approval": intelligence.requires_human_approval,
                        "intelligence_policy_reasons": list(intelligence.reasons),
                    }
                    if self.allow_unknown_quota:
                        request_metadata["allow_unknown_quota"] = True
                    if self.intelligence_routing:
                        request_metadata.update(IntelligenceRoutePolicy.metadata_for(intelligence))
                    request = ModelRequest(
                        request_id=state.get("active_request_id") or None,
                        task_id=task.task_id,
                        messages=state["messages"],
                        # Task competencies and policy traits (for example
                        # architecture/protected/security) influence the
                        # intelligence and authority decision, but they are
                        # not provider execution capabilities.  Only the
                        # canonical execution projection reaches the Router.
                        requested_capabilities=list(execution_capabilities(task.required_capabilities)),
                        allowed_tools=self.tools.registry.names(),
                        tool_definitions=self.tools.registry.definitions(),
                        tool_results=state["tool_results"],
                        max_output_tokens=task.limits.max_output_tokens,
                        sensitivity=task.sensitivity,
                        cost_ceiling=task.limits.max_cost,
                        metadata={
                            **request_metadata,
                            "task_context": {
                                "type": "dev_agent.task_context.v1",
                                "inputs": task.inputs,
                                "constraints": task.constraints,
                            },
                        },
                    )
                except Exception as exc:
                    self._fail(task, state, "protocol", str(exc), step=step)
                state["active_request_id"] = request.request_id
                if not replaying_request:
                    input_tokens = self._estimate_tokens({"messages": request.messages, "tool_definitions": request.tool_definitions, "tool_results": [result.to_dict() for result in request.tool_results]})
                    if state["input_tokens_used"] + input_tokens > task.limits.max_input_tokens:
                        self._fail(task, state, "limits_exceeded", "input token limit exceeded", step=step)
                    state["input_tokens_used"] += input_tokens
                    state["model_calls"] += 1
                    request_event = self._event_record(task, "model.requested", {"request": request.to_dict()}, step_id=step.step_id, request_id=request.request_id)
                    self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "before_model", state), events=[request_event])
                reservation = None
                provider_intent_key = None
                replayed_response = None
                legacy_execution = None
                if self._legacy_provider_executor.applies():
                    preparation = self._legacy_provider_executor.prepare(request)
                    reservation = preparation.reservation
                    provider_intent_key = preparation.intent_key
                    replayed_response = preparation.replayed_response
                    if preparation.status == "waiting_reconciliation":
                        self._provider_waiting_reconciliation(
                            task,
                            state,
                            step=step,
                            request_id=request.request_id,
                            cause=preparation.cause or "provider_intent_pending",
                            message=preparation.message or "provider effect intent requires reconciliation before retry",
                        )
                        return task
                    if preparation.status == "terminal":
                        self._fail(
                            task,
                            state,
                            preparation.category or "provider_effect_terminal",
                            preparation.message or "provider effect intent is already terminal",
                            step=step,
                            request_id=request.request_id,
                        )
                    if preparation.status == "denied":
                        return self._handle_dispatch_denied(
                            task,
                            state,
                            step=step,
                            error=DispatchDenied(
                                preparation.category or "dispatch_denied",
                                preparation.message or "provider dispatch was denied",
                            ),
                        )
                    if preparation.status == "blocked_budget":
                        self._block_budget(task, state, step=step, message=preparation.message or "provider budget preparation failed")
                        return task
                    legacy_execution = self._legacy_provider_executor.execute(
                        request,
                        reservation=reservation,
                        intent_key=provider_intent_key,
                        replayed_response=replayed_response,
                        deadline_epoch=state["deadline_epoch"],
                        cancel_event=cancel_event,
                    )
                    if legacy_execution.status == "waiting_reconciliation":
                        self._provider_waiting_reconciliation(
                            task,
                            state,
                            step=step,
                            request_id=request.request_id,
                            cause=legacy_execution.cause or "provider_reconciliation",
                            message=legacy_execution.message or "provider effect requires reconciliation",
                        )
                        return task
                    if legacy_execution.status == "cancelled_unable_to_confirm":
                        self._cancel_unable_to_confirm(
                            task,
                            state,
                            step=step,
                            message=legacy_execution.message or "provider request cancellation could not be confirmed",
                        )
                        return task
                    if legacy_execution.status == "cancelled":
                        self._cancel(task, state, step=step, message=legacy_execution.message or "provider request was cancelled")
                        return task
                    if legacy_execution.status == "denied":
                        return self._handle_dispatch_denied(
                            task,
                            state,
                            step=step,
                            error=DispatchDenied(
                                legacy_execution.category or "dispatch_denied",
                                legacy_execution.message or "provider dispatch was denied",
                            ),
                        )
                    if legacy_execution.status == "blocked_budget":
                        self._block_budget(task, state, step=step, message=legacy_execution.message or "provider budget dispatch was denied")
                        return task
                    if legacy_execution.status == "failed":
                        self._fail(
                            task,
                            state,
                            legacy_execution.category or "provider_decode",
                            legacy_execution.message or "provider request failed",
                            step=step,
                            request_id=request.request_id,
                        )
                    if legacy_execution.status != "succeeded" or legacy_execution.response is None:
                        self._fail(task, state, "provider_decode", "legacy provider execution returned no response", step=step, request_id=request.request_id)
                try:
                    response = legacy_execution.response if legacy_execution is not None else (replayed_response if replayed_response is not None else self._provider_request(request, state["deadline_epoch"], cancel_event))
                    if not isinstance(response, ModelResponse):
                        raise TypeError("provider must return ModelResponse")
                except DispatchDenied as exc:
                    return self._handle_dispatch_denied(task, state, step=step, error=exc)
                except _ProviderCancelled as exc:
                    if reservation is not None:
                        if exc.unable_to_confirm:
                            self.resource_policy.uncertain(reservation)
                            self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "cancelled", "cause": "unable_to_confirm"})
                            self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": "cancelled", "cause": "unable_to_confirm"})
                        else:
                            self.resource_policy.release(reservation)
                            self._provider_intent(provider_intent_key, status="confirmed_failed", result={"error_category": "cancelled", "cause": "confirmed_no_charge"})
                            self._record_provider_audit(request, reservation, "confirmed_no_charge", provider_intent_key, details={"category": "cancelled"})
                    if exc.unable_to_confirm:
                        self._cancel_unable_to_confirm(task, state, step=step, message="provider request cancellation could not be confirmed")
                    else:
                        self._cancel(task, state, step=step, message="provider request was cancelled")
                    return task
                except ProviderExecutionSaturated as exc:
                    # An arbitrary Python provider thread cannot be killed
                    # safely.  ModelTurnExecutor caps such orphaned calls; do
                    # not start another external request while the previous
                    # one is still running.
                    self._wait_for_resource(
                        task,
                        state,
                        step=step,
                        category="provider_execution_saturated",
                        message=str(exc),
                    )
                    return task
                except FutureTimeoutError:
                    # Cancellation can race with the final provider timeout
                    # check.  Once the request may still be running, an
                    # already-recorded cancellation must win over the
                    # ordinary terminal timeout path.
                    if cancel_event.is_set():
                        if reservation is not None:
                            self.resource_policy.uncertain(reservation)
                            self._provider_intent(
                                provider_intent_key,
                                status="unknown",
                                result={"error_category": "cancelled", "cause": "unable_to_confirm"},
                            )
                            self._record_provider_audit(
                                request,
                                reservation,
                                "unknown",
                                provider_intent_key,
                                details={"category": "cancelled", "cause": "unable_to_confirm"},
                            )
                        self._cancel_unable_to_confirm(
                            task,
                            state,
                            step=step,
                            message="provider request cancellation could not be confirmed",
                        )
                        return task
                    if reservation is not None:
                        self.resource_policy.uncertain(reservation)
                        self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "timeout"})
                        self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": "timeout"})
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="timeout", message="model request timed out")
                        return task
                    if getattr(self.provider, "handles_resource_policy", False):
                        # The dispatcher owns the reservation and may still be
                        # inside the concrete provider call.  Treat the
                        # timeout as an ambiguous external outcome instead
                        # of terminalizing the task before its reservation is
                        # reconciled.
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="timeout", message="model request timed out")
                        return task
                    self._fail(task, state, "timeout", "model request timed out", step=step, request_id=request.request_id)
                except ProviderError as exc:
                    if reservation is not None:
                        if exc.requires_reconciliation:
                            self.resource_policy.uncertain(reservation)
                            self._provider_intent(provider_intent_key, status="unknown", result={"error_category": exc.category, "message": str(exc)})
                            self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": exc.category})
                            cause = "budget_reconciliation" if exc.category == "reconciliation_required" else exc.category
                            self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause=cause, message=str(exc))
                            return task
                        self.resource_policy.release(reservation)
                        self._provider_intent(provider_intent_key, status="confirmed_failed", result={"error_category": exc.category, "message": str(exc)})
                        self._record_provider_audit(request, reservation, "confirmed_failed", provider_intent_key, details={"category": exc.category})
                    elif getattr(self.provider, "handles_resource_policy", False) and exc.requires_reconciliation:
                        # A dispatcher-owned reservation has already been
                        # moved to unknown by the dispatcher.  Preserve the
                        # same task-level ambiguity even though Controller has
                        # no local reservation object to mutate.
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause=exc.category, message=str(exc))
                        return task
                    elif getattr(self.provider, "handles_resource_policy", False) and exc.category == "reconciliation_required":
                        # The dispatcher has already held its reservation
                        # unknown after observing a charge that the protected
                        # budget cannot accept.  Do not terminalize the task
                        # or allow a duplicate provider request.
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="budget_reconciliation", message=str(exc))
                        return task
                    elif getattr(self.provider, "handles_resource_policy", False) and exc.category in {"quota", "rate_limit"}:
                        # A canonical dispatcher may exhaust every eligible
                        # binding after recording a provider-side quota/rate
                        # limit.  The dispatcher has already parked the
                        # resource; keep the task parked as well so the
                        # reset-aware wake path can resume it instead of
                        # turning a recoverable quota condition into FAILED.
                        self._block_quota(task, state, step=step, message=str(exc))
                        return task
                    self._fail(task, state, exc.category, str(exc), step=step, request_id=request.request_id)
                except Exception as exc:
                    if reservation is not None:
                        # Once the guarded provider call has started, an
                        # untyped exception cannot prove that no external
                        # effect occurred.  Preserve the dispatching budget
                        # reservation and require reconciliation instead of
                        # incorrectly recording a terminal local decode
                        # failure.
                        self.resource_policy.uncertain(reservation)
                        self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "provider_decode", "message": str(exc)})
                        self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": "provider_decode"})
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="provider_decode", message=str(exc))
                        return task
                    self._fail(task, state, "provider_decode", str(exc), step=step, request_id=request.request_id)
                if legacy_execution is None and reservation is not None and self.lease_guard is not None:
                    try:
                        self._active_lease_guard()
                    except Exception as exc:
                        # The provider may have completed after this worker
                        # lost ownership.  Do not accept the response as a
                        # normal success; preserve the charge-bearing
                        # reservation and require reconciliation before any
                        # retry can reuse the request.
                        self.resource_policy.uncertain(reservation)
                        self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "reconciliation_required", "cause": "lease_lost", "message": str(exc)})
                        self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": "reconciliation_required", "cause": "lease_lost"})
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="lease_lost", message="provider response arrived after lease loss")
                        return task
                if legacy_execution is None and reservation is not None:
                    try:
                        quota_observed = False
                        observer = getattr(self.resource_policy, "observe_provider_response", None)
                        if callable(observer):
                            quota_observed = bool(observer(reservation, response))
                        self.resource_policy.reconcile_response(reservation, response)
                    except BudgetExceeded as exc:
                        self.resource_policy.uncertain(reservation)
                        self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "reconciliation_required", "message": str(exc)})
                        self._record_provider_audit(request, reservation, "unknown", provider_intent_key, details={"category": "reconciliation_required"})
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="budget_reconciliation", message=str(exc))
                        return task
                    except Exception as exc:
                        self.resource_policy.uncertain(reservation)
                        self._provider_intent(provider_intent_key, status="unknown", result={"error_category": "reconciliation_required", "message": str(exc)})
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause="budget_reconciliation", message=str(exc))
                        return task
                    try:
                        self._provider_intent(provider_intent_key, status="succeeded", result={"provider_id": response.provider, "resource_id": reservation.budget.resource_id, "outcome": "succeeded", "response": response.to_dict()})
                        self._record_provider_audit(request, reservation, "succeeded", provider_intent_key, details={"quota_observed": quota_observed})
                    except Exception as exc:
                        # The provider response and budget reconciliation are
                        # already real.  If durable success/audit persistence
                        # is lost at this boundary, never let a retry issue a
                        # second paid request or classify it as provider
                        # decode failure.
                        self._provider_waiting_reconciliation(
                            task,
                            state,
                            step=step,
                            request_id=request.request_id,
                            cause="result_persistence",
                            message=f"provider result persistence requires reconciliation: {exc}",
                        )
                        return task
                if cancel_event.is_set():
                    self._cancel(task, state, step=step, message="provider request completed after cancellation")
                    return task
                output_tokens = self._response_output_tokens(response)
                if output_tokens > task.limits.max_output_tokens:
                    self._fail(task, state, "limits_exceeded", "model output token limit exceeded", step=step, request_id=request.request_id)
                state["output_tokens_used"] += output_tokens
                observed_cost = self._usage_number(response.usage, ("cost", "cost_usd", "total_cost", "total_cost_usd"))
                if observed_cost is not None:
                    state["cost_used"] += observed_cost
                    if state["cost_used"] > task.limits.max_cost:
                        self._fail(task, state, "limits_exceeded", "cost ceiling exceeded", step=step, request_id=request.request_id)
                response_event = self._event_record(task, "model.responded", {"response": response.to_dict()}, step_id=step.step_id, request_id=request.request_id)
                if response.tool_calls:
                    state["tool_calls"] += len(response.tool_calls)
                    if state["tool_calls"] > task.limits.max_tool_calls:
                        self._fail(task, state, "limits_exceeded", "tool call limit exceeded", step=step, request_id=request.request_id)
                    # Preserve the provider-facing assistant tool-call turn in
                    # the durable conversation transcript. OpenAI-compatible
                    # providers require this message immediately before the
                    # following tool results; keeping it in Kernel state also
                    # makes a resumed second request wire-compatible.
                    state["messages"].append(
                        {
                            "role": "assistant",
                            "content": "".join(response.text_segments),
                            "tool_calls": [
                                {
                                    "id": call.provider_call_id or call.call_id,
                                    "type": "function",
                                    "function": {
                                        "name": call.tool_name,
                                        "arguments": json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                                    },
                                }
                                for call in response.tool_calls
                            ],
                        }
                    )
                    state["pending_tool_calls"] = []
                    for index, call in enumerate(response.tool_calls):
                        # Provider/LLM supplied replay keys are hints only.  The
                        # Kernel owns operation identity after validation.
                        call.idempotency_key = self._kernel_operation_key(
                            task,
                            step,
                            call,
                            index,
                            effective_arguments=self.tools.effective_arguments(call),
                        )
                        state["pending_tool_calls"].append(call.to_dict())
                    state["active_request_id"] = None
                    state["active_step"] = step.to_dict()
                    self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "pending_tools", state), events=[response_event])
                    continue
                step.status = StepStatus.COMPLETED
                step.output_ref = response.response_id
                task.status = TaskStatus.COMPLETED
                state["active_request_id"] = None
                self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "after_model", state), events=[response_event, self._event_record(task, "task.completed", {"response_id": response.response_id, "text": response.text_segments}, step_id=step.step_id, request_id=request.request_id)])
                return task
            return task
        finally:
            self._active_tasks.discard(task.task_id)
            self._running_tasks.pop(task.task_id, None)
            self._cancellation_events.pop(task.task_id, None)
            self._cancellation_reasons.pop(task.task_id, None)

    def _provider_waiting_reconciliation(self, task: Task, state: dict[str, Any], *, step: Step, request_id: str, cause: str, message: str) -> None:
        step.status = StepStatus.WAITING
        state["active_step"] = step.to_dict()
        state["provider_reconciliation"] = {"cause": cause, "request_id": request_id, "status": "unknown"}
        task.status = TaskStatus.WAITING_RECONCILIATION
        event = self._event_record(task, "task.waiting_reconciliation", {"category": "reconciliation_required", "cause": cause, "message": message, "source": "provider_request"}, step_id=step.step_id, request_id=request_id)
        self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "waiting_reconciliation", state), events=[event])
