"""Bounded, checkpoint-resumable execution controller."""

from __future__ import annotations

from typing import Any

from ..domain.protocol import Event, ModelRequest, ModelResponse, Step, StepStatus, Task, TaskStatus, ToolCall, ToolResultStatus
from ..providers.base import ModelProvider
from ..state.store import StateStore
from ..tools.runtime import ToolRuntime


class RuntimeFailure(RuntimeError):
    """A terminal, normalized runtime failure."""


class Controller:
    def __init__(self, provider: ModelProvider, tools: ToolRuntime, store: StateStore) -> None:
        self.provider = provider
        self.tools = tools.with_result_store(store)
        self.store = store

    def _event(self, task: Task, event_type: str, payload: dict[str, Any], *, step_id: str | None = None, request_id: str | None = None) -> None:
        self.store.append_event(Event(event_type=event_type, task_id=task.task_id, step_id=step_id, request_id=request_id, provider=self.provider.provider_id, payload=payload))

    def _checkpoint(self, task: Task, step: Step, phase: str, state: dict[str, Any]) -> None:
        self.store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase=phase, state=state)

    def _fail(self, task: Task, state: dict[str, Any], category: str, message: str, *, step: Step | None = None, request_id: str | None = None) -> None:
        if step is not None:
            step.status = StepStatus.FAILED
            self.store.save_step(step)
            self._checkpoint(task, step, "failure", state)
        task.status = TaskStatus.FAILED
        self.store.save_task(task)
        self._event(task, "task.failed", {"category": category, "message": message}, step_id=step.step_id if step else None, request_id=request_id)
        raise RuntimeFailure(f"{category}: {message}")

    @staticmethod
    def _initial_state(task: Task) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": task.objective}], "tool_results": [], "model_calls": 0, "tool_calls": 0, "next_step_order": 0, "pending_tool_calls": [], "active_step": None}

    def resume(self, task_id: str) -> Task:
        task = self.store.load_task(task_id)
        if task is None:
            raise RuntimeFailure(f"task not found: {task_id}")
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        checkpoint = self.store.load_latest_checkpoint(task_id)
        return self.run(task, state=checkpoint["state"] if checkpoint else None)

    def _execute_pending(self, task: Task, state: dict[str, Any]) -> None:
        step = Step.from_dict(state["active_step"])
        for call in [ToolCall.from_dict(item) for item in state["pending_tool_calls"]]:
            result = self.tools.execute(call, task_id=task.task_id)
            self.store.save_tool_result(result)
            self._event(task, "tool.completed", {"result": result.to_dict()}, step_id=step.step_id)
            if result.status != ToolResultStatus.SUCCEEDED:
                error = result.error or {"category": "tool_execution", "message": "tool failed"}
                self._fail(task, state, error["category"], error["message"], step=step)
            state["tool_results"].append(result.to_dict())
            state["pending_tool_calls"] = [item for item in state["pending_tool_calls"] if item["call_id"] != call.call_id]
            self._checkpoint(task, step, "after_tool_result", state)
        step.status = StepStatus.COMPLETED
        self.store.save_step(step)
        state["next_step_order"] += 1
        state["active_step"] = None
        self._checkpoint(task, step, "after_tools", state)

    def run(self, task: Task, *, state: dict[str, Any] | None = None) -> Task:
        task.status = TaskStatus.RUNNING
        self.store.save_task(task)
        state = state or self._initial_state(task)
        while task.status == TaskStatus.RUNNING:
            if state["pending_tool_calls"]:
                self._execute_pending(task, state)
                continue
            if state["next_step_order"] >= task.limits.max_steps or state["model_calls"] >= task.limits.max_model_calls:
                self._fail(task, state, "limits_exceeded", "execution limits exceeded")
            step = Step(task_id=task.task_id, order=state["next_step_order"], kind="model", status=StepStatus.RUNNING, attempt=1)
            state["active_step"] = step.to_dict()
            self.store.save_step(step)
            self._checkpoint(task, step, "before_model", state)
            request = ModelRequest(task_id=task.task_id, messages=state["messages"], allowed_tools=self.tools.registry.names(), tool_results=state["tool_results"], max_output_tokens=task.limits.max_output_tokens, cost_ceiling=task.limits.max_cost)
            self._event(task, "model.requested", {"request": request.to_dict()}, step_id=step.step_id, request_id=request.request_id)
            state["model_calls"] += 1
            try:
                response = self.provider.request(request)
                if not isinstance(response, ModelResponse):
                    raise TypeError("provider must return ModelResponse")
            except Exception as exc:
                self._fail(task, state, "provider_decode", str(exc), step=step, request_id=request.request_id)
            self._event(task, "model.responded", {"response": response.to_dict()}, step_id=step.step_id, request_id=request.request_id)
            if response.tool_calls:
                state["tool_calls"] += len(response.tool_calls)
                if state["tool_calls"] > task.limits.max_tool_calls:
                    self._fail(task, state, "limits_exceeded", "tool call limit exceeded", step=step, request_id=request.request_id)
                state["pending_tool_calls"] = [call.to_dict() for call in response.tool_calls]
                self._checkpoint(task, step, "pending_tools", state)
                continue
            step.status = StepStatus.COMPLETED
            step.output_ref = response.response_id
            self.store.save_step(step)
            self._checkpoint(task, step, "after_model", state)
            task.status = TaskStatus.COMPLETED
            self.store.save_task(task)
            self._event(task, "task.completed", {"response_id": response.response_id, "text": response.text_segments}, step_id=step.step_id, request_id=request.request_id)
            return task
        return task
