"""Bounded, iterative alpha0 controller."""

from __future__ import annotations

import json

from ..domain.protocol import Event, ModelRequest, ModelResponse, Step, StepStatus, Task, TaskStatus, ToolResultStatus
from ..providers.base import ModelProvider
from ..state.json_store import JsonStateStore
from ..tools.runtime import ToolRuntime


class RuntimeFailure(RuntimeError):
    """A terminal, normalized runtime failure."""


class Controller:
    def __init__(self, provider: ModelProvider, tools: ToolRuntime, store: JsonStateStore) -> None:
        self.provider = provider
        self.tools = tools
        self.store = store

    def _event(self, task: Task, event_type: str, payload: dict, *, step_id: str | None = None, request_id: str | None = None) -> None:
        self.store.append_event(Event(event_type=event_type, task_id=task.task_id, step_id=step_id, request_id=request_id, provider=self.provider.provider_id, payload=payload))

    def resume(self, task_id: str) -> Task:
        """Load a non-terminal task from a durable store and continue it."""
        task = self.store.load_task(task_id)
        if task is None:
            raise RuntimeFailure(f"task not found: {task_id}")
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        return self.run(task)

    def run(self, task: Task) -> Task:
        task.status = TaskStatus.RUNNING
        self.store.save_task(task)
        messages = [{"role": "user", "content": task.objective}]
        model_calls = 0
        tool_calls = 0
        step_order = 0
        while task.status == TaskStatus.RUNNING:
            if step_order >= task.limits.max_steps or model_calls >= task.limits.max_model_calls:
                task.status = TaskStatus.FAILED
                self.store.save_task(task)
                self._event(task, "task.failed", {"category": "limits_exceeded"})
                raise RuntimeFailure("execution limits exceeded")

            step = Step(task_id=task.task_id, order=step_order, kind="model", status=StepStatus.RUNNING, attempt=1)
            self.store.save_step(step)
            self.store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="before_model")
            request = ModelRequest(task_id=task.task_id, messages=messages, allowed_tools=self.tools.registry.names(), max_output_tokens=task.limits.max_output_tokens, cost_ceiling=task.limits.max_cost)
            self._event(task, "model.requested", {"request": request.to_dict()}, step_id=step.step_id, request_id=request.request_id)
            model_calls += 1
            try:
                response = self.provider.request(request)
                if not isinstance(response, ModelResponse):
                    raise TypeError("provider must return ModelResponse")
            except Exception as exc:
                step.status = StepStatus.FAILED
                self.store.save_step(step)
                self.store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="after_model_error")
                task.status = TaskStatus.FAILED
                self.store.save_task(task)
                self._event(task, "task.failed", {"category": "provider_decode", "message": str(exc)}, step_id=step.step_id, request_id=request.request_id)
                raise RuntimeFailure(str(exc)) from exc

            self._event(task, "model.responded", {"response": response.to_dict()}, step_id=step.step_id, request_id=request.request_id)
            if response.tool_calls:
                tool_calls += len(response.tool_calls)
                if tool_calls > task.limits.max_tool_calls:
                    task.status = TaskStatus.FAILED
                    self.store.save_task(task)
                    raise RuntimeFailure("tool call limit exceeded")
                for call in response.tool_calls:
                    result = self.tools.execute(call)
                    self.store.save_tool_result(result)
                    self._event(task, "tool.completed", {"result": result.to_dict()}, step_id=step.step_id, request_id=request.request_id)
                    if result.status != ToolResultStatus.SUCCEEDED:
                        step.status = StepStatus.FAILED
                        self.store.save_step(step)
                        task.status = TaskStatus.FAILED
                        self.store.save_task(task)
                        raise RuntimeFailure(result.error or {"category": "tool_execution"})
                    messages.append({"role": "tool", "content": json.dumps(result.structured_result, ensure_ascii=False, sort_keys=True)})
                step.status = StepStatus.COMPLETED
                self.store.save_step(step)
                self.store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="after_tool")
                step_order += 1
                continue

            step.status = StepStatus.COMPLETED
            step.output_ref = response.response_id
            self.store.save_step(step)
            self.store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="after_model")
            task.status = TaskStatus.COMPLETED
            self.store.save_task(task)
            self._event(task, "task.completed", {"response_id": response.response_id, "text": response.text_segments}, step_id=step.step_id, request_id=request.request_id)
            return task
        return task
