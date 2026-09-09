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

def test_real_subprocess_death_resumes_pending_tool_without_duplicate_handler(tmp_path):
    database = tmp_path / "process.sqlite3"
    marker = tmp_path / "effects.log"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(__import__("pathlib").Path.cwd())
    worker = str(__import__("pathlib").Path.cwd() / "tests" / "v2" / "process_crash_worker.py")
    completed = subprocess.run([sys.executable, worker, str(database), str(marker)], env=environment, timeout=20)
    assert completed.returncode == 17
    registry = ToolRegistry()
    effects = []
    registry.register(ToolSpec(name="write", description="write", side_effect_level="local_write", handler=lambda args: effects.append(args["ordinal"]) or {"ordinal": args["ordinal"]}))
    class FinalProvider(ModelProvider):
        provider_id = "process-crash"

        def request(self, request):
            return ModelResponse(provider=self.provider_id, model="test", text_segments=["done"])

    with SQLiteStateStore(database) as store:
        task_id = next(iter(store.snapshot()["tasks"]))
        task = Controller(FinalProvider(), ToolRuntime(registry), store).resume(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert effects == [2]
    assert marker.read_text(encoding="utf-8").splitlines() == ["1"]

def test_resume_after_crash_between_multiple_side_effect_tools_runs_each_handler_once(tmp_path):
    path = tmp_path / "crash.sqlite3"
    effects = []
    calls = [
        ToolCall(tool_name="write", arguments={"ordinal": 1}, idempotency_key="write-1"),
        ToolCall(tool_name="write", arguments={"ordinal": 2}, idempotency_key="write-2"),
    ]
    registry = ToolRegistry()
    registry.register(ToolSpec(name="write", description="side effect", side_effect_level="local_write", handler=lambda args: effects.append(args["ordinal"]) or {"ordinal": args["ordinal"]}))
    task = Task(objective="resume multi tool")
    provider = MultiCallThenFinalProvider(calls)
    with CrashAfterFirstToolCheckpointStore(path) as crash_store:
        with pytest.raises(SystemExit, match="simulated process death"):
            Controller(provider, ToolRuntime(registry), crash_store).run(task)
    with SQLiteStateStore(path) as reopened:
        result = Controller(provider, ToolRuntime(registry), reopened).resume(task.task_id)
    assert result.status == TaskStatus.COMPLETED
    assert effects == [1, 2]
    assert len(provider.requests) == 2
    assert {item.call_id for item in provider.requests[1].tool_results} == {call.call_id for call in calls}

def test_commit_transition_persists_related_records_atomically(tmp_path):
    task = Task(objective="atomic")
    step = Step(task_id=task.task_id, order=0, kind="model")
    event = Event(event_type="transition", task_id=task.task_id, step_id=step.step_id, payload={"ok": True})
    result = ToolResult(call_id=str(__import__("uuid").uuid4()), tool_name="echo", status=ToolResultStatus.SUCCEEDED, structured_result={"ok": True})
    with SQLiteStateStore(tmp_path / "atomic.sqlite3") as store:
        store.commit_transition(task=task, step=step, checkpoint={"task_id": task.task_id, "step_id": step.step_id, "phase": "after_tools", "state": {}}, event=event, tool_result=result)
        assert store.load_task(task.task_id) is not None
        assert store.load_latest_checkpoint(task.task_id)["phase"] == "after_tools"
        assert any(item["event_id"] == event.event_id for item in store.snapshot()["events"])
        assert result.call_id in store.snapshot()["tool_results"]

@pytest.mark.parametrize("phase", ["before_model", "pending_tools", "after_tool_result", "after_tools"])
def test_resume_after_each_nonterminal_commit_boundary(tmp_path, phase):
    path = tmp_path / f"{phase}.sqlite3"
    task = Task(objective=f"crash boundary {phase}")
    effects = []
    if phase == "before_model":
        provider = FinalProvider()
        registry = ToolRegistry()
    else:
        from src.dev_agent.providers.fake import FakeProvider

        provider = FakeProvider(tool_arguments={"value": "x"})
        registry = ToolRegistry()
        registry.register(ToolSpec(name="echo", description="echo", required_arguments=frozenset({"value"}), handler=lambda args: effects.append(args) or {"echo": args["value"]}))
    with CrashAfterCommitStore(path, phase) as store:
        with pytest.raises(SystemExit, match=phase):
            Controller(provider, ToolRuntime(registry), store).run(task)
        with SQLiteStateStore(path) as reopened:
            resumed = Controller(provider, ToolRuntime(registry), reopened).resume(task.task_id)
            steps = [item for item in reopened.snapshot()["steps"].values() if item["task_id"] == task.task_id]
            if phase == "before_model":
                checkpoint = reopened.load_latest_checkpoint(task.task_id)
                assert checkpoint["state"]["model_calls"] == 1
                assert len([event for event in reopened.snapshot()["events"] if event["event_type"] == "model.requested"]) == 1
    assert resumed.status == TaskStatus.COMPLETED
    assert (len(provider.requests) == 1) if phase == "before_model" else (len(provider.requests) == 2)
    assert len(steps) == (1 if phase in {"before_model", "after_tool_result"} else 2)
    if phase != "before_model":
        assert effects == [{"value": "x"}]

def test_json_commit_transition_restores_memory_when_flush_fails(tmp_path, monkeypatch):
    store = JsonStateStore(tmp_path / "atomic.json")
    before = store.snapshot()
    task = Task(objective="flush failure")

    def fail_flush():
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(store, "_flush", fail_flush)
    with pytest.raises(OSError, match="simulated persistence failure"):
        store.commit_transition(task=task)

    assert store.snapshot() == before
    assert not (tmp_path / "atomic.json").exists()

@pytest.mark.parametrize("phase", ["after_model", "failure"])
def test_resume_does_not_repeat_terminal_transition_after_checkpoint_crash(tmp_path, phase):
    path = tmp_path / f"{phase}.sqlite3"
    task = Task(objective=f"crash {phase}")
    provider = FinalProvider() if phase == "after_model" else FailingProvider()
    with CrashAtCheckpointStore(path, phase) as store:
        with pytest.raises((SystemExit, RuntimeFailure)):
            Controller(provider, ToolRuntime(ToolRegistry()), store).run(task)
    with SQLiteStateStore(path) as reopened:
        resumed = Controller(provider, ToolRuntime(ToolRegistry()), reopened).resume(task.task_id)
        events = [event for event in reopened.snapshot()["events"] if event["task_id"] == task.task_id]
    expected = TaskStatus.COMPLETED if phase == "after_model" else TaskStatus.FAILED
    assert resumed.status == expected
    assert len(provider.requests) <= 1
    terminal_type = "task.completed" if phase == "after_model" else "task.failed"
    assert sum(event["event_type"] == terminal_type for event in events) == 1

def test_controller_critical_trace_uses_commit_transition_only(tmp_path):
    from src.dev_agent.providers.fake import FakeProvider

    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo", required_arguments=frozenset({"value"}), handler=lambda args: {"echo": args["value"]}))
    with CommitOnlyStore(tmp_path / "commit-only.sqlite3") as store:
        task = Controller(FakeProvider(tool_arguments={"value": "x"}), ToolRuntime(registry), store).run(Task(objective="atomic trace"))
        assert task.status == TaskStatus.COMPLETED
        phases = [item["phase"] for item in store.transitions]
        assert {"before_model", "pending_tools", "after_tool_result", "after_tools", "after_model"} <= set(phases)
        assert any(item["tool_result"] and "tool.completed" in item["events"] for item in store.transitions)
        assert any("task.completed" in item["events"] for item in store.transitions)
