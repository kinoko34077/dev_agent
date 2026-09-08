"""Bounded, checkpoint-resumable execution controller."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
from ..state.store import StateStore
from ..tools.runtime import ToolRuntime


class RuntimeFailure(RuntimeError):
    """A terminal, normalized runtime failure."""


class _ProviderCancelled(Exception):
    def __init__(self, *, unable_to_confirm: bool) -> None:
        super().__init__("provider request cancellation")
        self.unable_to_confirm = unable_to_confirm


class Controller:
    # Keep the historic names available to callers while the implementation
    # lives in the single canonical AuditRecorder boundary.
    MAX_EVENT_STRING_CHARS = AuditRecorder.MAX_STRING_CHARS
    MAX_EVENT_PAYLOAD_BYTES = AuditRecorder.MAX_PAYLOAD_BYTES
    EVENT_ARTIFACT_RETENTION_SECONDS = AuditRecorder.RETENTION_SECONDS
    _SECRET_KEY_WORDS = tuple(AuditRecorder.SECRET_KEYS)
    _SECRET_PATTERNS = AuditRecorder.SECRET_PATTERNS

    def __init__(self, provider: ModelProvider, tools: ToolRuntime, store: StateStore, *, event_artifacts: EventArtifactStore | None = None, resource_policy: ResourcePolicy | None = None, lease_guard: Callable[[], None] | None = None, lease_proof: Any | None = None) -> None:
        self.provider = provider
        self.tools = tools.with_result_store(store)
        self.store = store
        self.event_artifacts = event_artifacts
        self.resource_policy = resource_policy
        self.lease_guard = lease_guard
        self.lease_proof = lease_proof
        self._cancellation_events: dict[str, Event] = {}
        self._cancellation_reasons: dict[str, str] = {}
        self._active_tasks: set[str] = set()
        self._running_tasks: dict[str, Task] = {}

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
        if self.lease_guard is not None:
            self.lease_guard()
        self.store.commit_transition(task=task, step=step, checkpoint=checkpoint, events=events, tool_result=tool_result, lease_proof=self.lease_proof)

    @staticmethod
    def _kernel_operation_key(task: Task, step: Step, call: ToolCall, index: int, *, effective_arguments: dict[str, Any] | None = None) -> str:
        canonical = json.dumps({"tool_name": call.tool_name, "arguments": effective_arguments if effective_arguments is not None else call.arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"op:{task.task_id}:{step.order}:{index}:{digest}"

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
        message = self._cancellation_reasons.get(task.task_id, message)
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

    @staticmethod
    def _initial_state(task: Task) -> dict[str, Any]:
        return {
            "messages": [{"role": "user", "content": task.objective}],
            "tool_results": [],
            "model_calls": 0,
            "tool_calls": 0,
            "input_tokens_used": 0,
            "output_tokens_used": 0,
            "cost_used": 0.0,
            "retries_used": 0,
            "next_step_order": 0,
            "pending_tool_calls": [],
            "active_step": None,
            "deadline_epoch": time() + task.limits.max_wall_time_seconds,
        }

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

    def _provider_request(self, request: ModelRequest, deadline_epoch: float, cancel_event: Event) -> ModelResponse:
        if self.lease_guard is not None:
            self.lease_guard()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.provider.request, request)
        try:
            while True:
                remaining = deadline_epoch - time()
                if remaining <= 0:
                    future.cancel()  # best effort; arbitrary provider threads are not killable
                    raise FutureTimeoutError()
                try:
                    response = future.result(timeout=min(0.05, remaining))
                    # A provider can complete concurrently with the deadline
                    # boundary.  Do not accept a response that arrived after
                    # the runtime lease expired: for an external provider the
                    # outcome is no longer safe to classify as an ordinary
                    # timeout or success, so the caller must reconcile it.
                    if time() >= deadline_epoch:
                        raise FutureTimeoutError()
                    return response
                except FutureTimeoutError:
                    if cancel_event.is_set():
                        if future.done():
                            return future.result(timeout=0)
                        cancelled = future.cancel()
                        raise _ProviderCancelled(unable_to_confirm=not cancelled)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def cancel(self, task_id: str, *, reason: str = "task cancellation requested") -> Task:
        """Request cooperative cancellation and persist it when not running."""
        event = self._cancellation_events.get(task_id)
        if event is not None:
            event.set()
            self._cancellation_reasons[task_id] = reason
            # Do not touch a same-thread SQLite connection from the caller
            # while the run loop is active in another thread.  The run loop
            # owns the durable terminal transition at its next boundary.
            task = self._running_tasks.get(task_id)
            if task is None:
                task = self.store.load_task(task_id)
            if task is None:
                raise RuntimeFailure(f"task not found: {task_id}")
            return task
        task = self.store.load_task(task_id)
        if task is None:
            raise RuntimeFailure(f"task not found: {task_id}")
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.WAITING_RECONCILIATION}:
            return task
        task.status = TaskStatus.CANCELLED
        self._commit(task=task, events=[self._event_record(task, "task.cancelled", {"category": "cancelled", "message": reason, "cancellation_state": "terminated"})])
        return task

    def resume(self, task_id: str, *, approval_id: str | None = None) -> Task:
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
            if self.lease_guard is not None:
                self.lease_guard()
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

    def run(self, task: Task, *, state: dict[str, Any] | None = None) -> Task:
        state = state or self._initial_state(task)
        self._ensure_state_defaults(state)
        if "deadline_epoch" not in state:
            state["deadline_epoch"] = time() + task.limits.max_wall_time_seconds
        cancel_event = self._cancellation_events.setdefault(task.task_id, Event())
        self._active_tasks.add(task.task_id)
        self._running_tasks[task.task_id] = task
        try:
            task.status = TaskStatus.RUNNING
            # Register the cancellation event before the first durable write so
            # an API/UI cancellation racing with startup cannot be overwritten
            # by a later RUNNING record.
            self._commit(task=task)
            while task.status == TaskStatus.RUNNING:
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
                try:
                    request = ModelRequest(task_id=task.task_id, messages=state["messages"], allowed_tools=self.tools.registry.names(), tool_definitions=self.tools.registry.definitions(), tool_results=state["tool_results"], max_output_tokens=task.limits.max_output_tokens, cost_ceiling=task.limits.max_cost)
                except Exception as exc:
                    self._fail(task, state, "protocol", str(exc), step=step)
                input_tokens = self._estimate_tokens({"messages": request.messages, "tool_definitions": request.tool_definitions, "tool_results": [result.to_dict() for result in request.tool_results]})
                if state["input_tokens_used"] + input_tokens > task.limits.max_input_tokens:
                    self._fail(task, state, "limits_exceeded", "input token limit exceeded", step=step)
                state["input_tokens_used"] += input_tokens
                state["model_calls"] += 1
                request_event = self._event_record(task, "model.requested", {"request": request.to_dict()}, step_id=step.step_id, request_id=request.request_id)
                self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "before_model", state), events=[request_event])
                reservation = None
                if self.resource_policy is not None and not getattr(self.provider, "handles_resource_policy", False):
                    try:
                        reservation = self.resource_policy.reserve_for_provider(task.task_id, self.provider.provider_id, request)
                    except Exception as exc:
                        self._block_budget(task, state, step=step, message=str(exc))
                        return task
                try:
                    response = self._provider_request(request, state["deadline_epoch"], cancel_event)
                    if not isinstance(response, ModelResponse):
                        raise TypeError("provider must return ModelResponse")
                except DispatchDenied as exc:
                    self._block_budget(task, state, step=step, message=f"{exc.category}: {exc}")
                    return task
                except _ProviderCancelled as exc:
                    if reservation is not None:
                        if exc.unable_to_confirm:
                            self.resource_policy.uncertain(reservation)
                        else:
                            self.resource_policy.release(reservation)
                    if exc.unable_to_confirm:
                        self._cancel_unable_to_confirm(task, state, step=step, message="provider request cancellation could not be confirmed")
                    else:
                        self._cancel(task, state, step=step, message="provider request was cancelled")
                    return task
                except FutureTimeoutError:
                    if reservation is not None:
                        self.resource_policy.uncertain(reservation)
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
                        if exc.category == "transport":
                            self.resource_policy.uncertain(reservation)
                            self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause=exc.category, message=str(exc))
                            return task
                        self.resource_policy.release(reservation)
                    elif getattr(self.provider, "handles_resource_policy", False) and exc.category == "transport":
                        # A dispatcher-owned reservation has already been
                        # moved to unknown by the dispatcher.  Preserve the
                        # same task-level ambiguity even though Controller has
                        # no local reservation object to mutate.
                        self._provider_waiting_reconciliation(task, state, step=step, request_id=request.request_id, cause=exc.category, message=str(exc))
                        return task
                    self._fail(task, state, exc.category, str(exc), step=step, request_id=request.request_id)
                except Exception as exc:
                    if reservation is not None:
                        self.resource_policy.release(reservation)
                    self._fail(task, state, "provider_decode", str(exc), step=step, request_id=request.request_id)
                if reservation is not None:
                    try:
                        self.resource_policy.reconcile_response(reservation, response)
                    except Exception as exc:
                        self._fail(task, state, "budget_reconciliation", str(exc), step=step, request_id=request.request_id)
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
                    state["active_step"] = step.to_dict()
                    self._commit(task=task, step=step, checkpoint=self._checkpoint_payload(task, step, "pending_tools", state), events=[response_event])
                    continue
                step.status = StepStatus.COMPLETED
                step.output_ref = response.response_id
                task.status = TaskStatus.COMPLETED
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
