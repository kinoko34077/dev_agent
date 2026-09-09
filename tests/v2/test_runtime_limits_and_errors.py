import pytest
from threading import Event as ThreadEvent, Thread
from time import sleep, time
import os
import subprocess
import sys

from src.dev_agent.domain.protocol import Event, ExecutionLimits, ModelResponse, Step, StepStatus, Task, TaskStatus, ToolCall, ToolResult, ToolResultStatus
from src.dev_agent.policy import PathPolicy
from src.dev_agent.policy.approvals import canonical_arguments_hash
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.runtime import Controller, RuntimeFailure
from src.dev_agent.state import JsonStateStore, SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec



from .integration_hardening_support import *

def test_controller_enforces_input_output_and_observed_cost_limits(tmp_path):
    class BudgetProvider(ModelProvider):
        provider_id = "budget"

        def __init__(self, usage):
            self.usage = usage
            self.requests = 0

        def request(self, request):
            self.requests += 1
            return ModelResponse(provider=self.provider_id, model="test", text_segments=["done"], usage=self.usage)

    input_provider = BudgetProvider({})
    input_task = Task(objective="x" * 100, limits=ExecutionLimits(max_input_tokens=1))
    with SQLiteStateStore(tmp_path / "input-limit.sqlite3") as store:
        with pytest.raises(RuntimeFailure, match="input token"):
            Controller(input_provider, ToolRuntime(ToolRegistry()), store).run(input_task)
    assert input_provider.requests == 0

    output_provider = BudgetProvider({"output_tokens": 5})
    with SQLiteStateStore(tmp_path / "output-limit.sqlite3") as store:
        with pytest.raises(RuntimeFailure, match="output token"):
            Controller(output_provider, ToolRuntime(ToolRegistry()), store).run(Task(objective="output", limits=ExecutionLimits(max_output_tokens=2)))

    cost_provider = BudgetProvider({"cost": 0.2})
    with SQLiteStateStore(tmp_path / "cost-limit.sqlite3") as store:
        with pytest.raises(RuntimeFailure, match="cost"):
            Controller(cost_provider, ToolRuntime(ToolRegistry()), store).run(Task(objective="cost", limits=ExecutionLimits(max_cost=0.1)))

def test_controller_enforces_whole_task_wall_clock_limit(tmp_path):
    class SlowProvider(ModelProvider):
        provider_id = "slow"

        def request(self, request):
            sleep(0.2)
            return ModelResponse(provider="slow", model="test", text_segments=["late"])

    task = Task(objective="deadline", limits=ExecutionLimits(max_wall_time_seconds=0.01, max_model_calls=1))
    with SQLiteStateStore(tmp_path / "deadline.sqlite3") as store:
        with pytest.raises(RuntimeFailure, match="timeout"):
            Controller(SlowProvider(), ToolRuntime(ToolRegistry()), store).run(task)

def test_controller_replaces_provider_idempotency_hint_with_kernel_operation_key(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="write", description="write", side_effect_level="local_write", handler=lambda args: {"ok": True}))
    provider = SingleCallProvider(ToolCall(tool_name="write", arguments={"value": "x"}, idempotency_key="model-chosen"))
    task = Task(objective="kernel operation")
    step = Step(task_id=task.task_id, order=0, kind="model")
    key = Controller._kernel_operation_key(task, step, provider.call, 0)
    assert key.startswith(f"op:{task.task_id}:0:0:")
    assert key != "model-chosen"

def test_resume_honors_persisted_wall_clock_deadline(tmp_path):
    task = Task(objective="expired", limits=ExecutionLimits(max_wall_time_seconds=30))
    with SQLiteStateStore(tmp_path / "expired.sqlite3") as store:
        store.save_task(task)
        step = Step(task_id=task.task_id, order=0, kind="model")
        store.save_step(step)
        store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="before_model", state={"messages": [], "tool_results": [], "model_calls": 0, "tool_calls": 0, "next_step_order": 0, "pending_tool_calls": [], "active_step": step.to_dict(), "deadline_epoch": 1.0})
        with pytest.raises(RuntimeFailure, match="timeout"):
            Controller(FinalProvider(), ToolRuntime(ToolRegistry()), store).resume(task.task_id)

@pytest.mark.parametrize("category", ["authentication", "rate_limit", "transport", "provider_decode"])
def test_controller_preserves_provider_error_category(tmp_path, category):
    class ErrorProvider(ModelProvider):
        provider_id = "error"

        def request(self, request):
            raise ProviderError(f"{category}: simulated", category=category)

    task = Task(objective="provider error")
    with SQLiteStateStore(tmp_path / f"{category}.sqlite3") as store:
        with pytest.raises(RuntimeFailure, match=category):
            Controller(ErrorProvider(), ToolRuntime(ToolRegistry()), store).run(task)
        failures = [event for event in store.snapshot()["events"] if event["event_type"] == "task.failed"]
    assert failures[-1]["payload"]["category"] == category
