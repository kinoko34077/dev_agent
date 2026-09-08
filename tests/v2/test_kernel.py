import pytest

from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.providers.fake import FakeProvider
from src.dev_agent.runtime import Controller, RuntimeFailure
from src.dev_agent.state import JsonStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


def test_tool_runtime_binding_returns_independent_instances():
    registry = ToolRegistry()
    runtime = ToolRuntime(registry)
    first_store = object()
    second_store = object()
    first = runtime.bound_to(first_store)
    second = runtime.bound_to(second_store)
    assert first is not runtime
    assert second is not runtime and second is not first
    assert runtime.result_store is None
    assert first.result_store is first_store
    assert second.result_store is second_store


def make_controller(tmp_path, provider=None, *, max_steps=20):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo a value", required_arguments=frozenset({"value"}), handler=lambda args: {"echo": args["value"]}))
    return Controller(provider or FakeProvider(tool_arguments={"value": "hello"}), ToolRuntime(registry), JsonStateStore(tmp_path / "state.json"))


def test_fake_provider_completes_one_tool_task_with_full_trace(tmp_path):
    provider = FakeProvider(tool_arguments={"value": "hello"})
    controller = make_controller(tmp_path, provider)
    task = Task(objective="echo hello")

    completed = controller.run(task)

    assert completed.status == TaskStatus.COMPLETED
    snapshot = controller.store.snapshot()
    assert len(provider.requests) == 2
    assert snapshot["tasks"][task.task_id]["status"] == "completed"
    assert len(snapshot["tool_results"]) == 1
    assert {event["event_type"] for event in snapshot["events"]} >= {"model.requested", "model.responded", "tool.completed", "task.completed"}
    assert {checkpoint["phase"] for checkpoint in snapshot["checkpoints"]} >= {"before_model", "pending_tools", "after_tool_result", "after_tools", "after_model"}


def test_step_limit_stops_before_unbounded_provider_calls(tmp_path):
    controller = make_controller(tmp_path, max_steps=1)
    task = Task(objective="must stop", limits={"max_steps": 1})

    with pytest.raises(RuntimeFailure, match="limits"):
        controller.run(task)
    assert task.status == TaskStatus.FAILED
    assert len(controller.store.snapshot()["events"]) >= 1


def test_unregistered_tool_is_denied_and_does_not_run(tmp_path):
    controller = make_controller(tmp_path, FakeProvider(tool_name="not_registered"))
    with pytest.raises(RuntimeFailure):
        controller.run(Task(objective="denied"))
    assert controller.store.snapshot()["tool_results"]
