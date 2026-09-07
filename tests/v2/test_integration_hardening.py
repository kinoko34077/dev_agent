import pytest

from src.dev_agent.domain.protocol import ModelResponse, Step, Task, TaskStatus, ToolCall
from src.dev_agent.policy import PathPolicy
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.runtime import Controller, RuntimeFailure
from src.dev_agent.state import SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


class FinalProvider(ModelProvider):
    provider_id = "final"

    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return ModelResponse(provider="final", model="test", text_segments=["done"])


class SingleCallProvider(ModelProvider):
    provider_id = "single"

    def __init__(self, call):
        self.call = call

    def request(self, request):
        return ModelResponse(provider="single", model="test", tool_calls=[self.call], finish_reason="tool_call")


def test_resume_executes_checkpointed_pending_tool_once_and_preserves_tool_identity(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    task = Task(objective="resume after tool")
    call = ToolCall(tool_name="write", arguments={"value": "x"}, idempotency_key="write-once")
    step = Step(task_id=task.task_id, order=0, kind="model")
    state = {"messages": [{"role": "user", "content": task.objective}], "tool_results": [], "model_calls": 1, "tool_calls": 1, "next_step_order": 0, "pending_tool_calls": [call.to_dict()], "active_step": step.to_dict()}
    writes = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="write", description="side effect", side_effect_level="local_write", handler=lambda args: writes.append(args) or {"written": True}))
    with SQLiteStateStore(path) as store:
        task.status = TaskStatus.RUNNING
        store.save_task(task)
        store.save_step(step)
        store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="pending_tools", state=state)
    provider = FinalProvider()
    with SQLiteStateStore(path) as reopened:
        result = Controller(provider, ToolRuntime(registry), reopened).resume(task.task_id)
        assert result.status == TaskStatus.COMPLETED
    assert writes == [{"value": "x"}]
    assert len(provider.requests) == 1
    tool_result = provider.requests[0].tool_results[0]
    assert (tool_result.call_id, tool_result.tool_name, tool_result.status.value) == (call.call_id, "write", "succeeded")


def test_controller_denies_financial_tool_without_human_approval(tmp_path):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="charge", description="charge", side_effect_level="financial", handler=lambda args: calls.append(args) or {"charged": True}))
    from src.dev_agent.providers.fake import FakeProvider

    controller = Controller(FakeProvider(tool_name="charge", tool_arguments={"amount": 1}), ToolRuntime(registry), SQLiteStateStore(tmp_path / "state.sqlite3"))
    with pytest.raises(RuntimeFailure, match="approval_required"):
        controller.run(Task(objective="charge"))
    assert calls == []
    assert any(event["payload"].get("category") == "approval_required" for event in controller.store.snapshot()["events"] if event["event_type"] == "task.failed")


def test_controller_enforces_path_policy_before_handler(tmp_path):
    calls = []
    workspace = tmp_path / "workspace"
    (workspace / "sandbox").mkdir(parents=True)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="write_file", description="write", side_effect_level="local_write", path_argument="path", path_operation="write", handler=lambda args: calls.append(args) or {"ok": True}))
    call = ToolCall(tool_name="write_file", arguments={"path": "../escape.txt"}, idempotency_key="path-check")
    controller = Controller(SingleCallProvider(call), ToolRuntime(registry, paths=PathPolicy(workspace, {"sandbox": {"write"}})), SQLiteStateStore(tmp_path / "state.sqlite3"))
    with pytest.raises(RuntimeFailure, match="policy_denied"):
        controller.run(Task(objective="escape"))
    assert calls == []
