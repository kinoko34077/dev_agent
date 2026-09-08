import pytest
from time import sleep
import os
import subprocess
import sys

from src.dev_agent.domain.protocol import ExecutionLimits, ModelResponse, Step, Task, TaskStatus, ToolCall
from src.dev_agent.policy import PathPolicy
from src.dev_agent.policy.approvals import canonical_arguments_hash
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


class ApprovalCallProvider(ModelProvider):
    provider_id = "approval"

    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(provider="approval", model="test", tool_calls=[ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="publish-once")], finish_reason="tool_call")
        return ModelResponse(provider="approval", model="test", text_segments=["done"])


class MultiCallThenFinalProvider(ModelProvider):
    provider_id = "multi"

    def __init__(self, calls):
        self.calls = calls
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(provider="multi", model="test", tool_calls=self.calls, finish_reason="tool_call")
        return ModelResponse(provider="multi", model="test", text_segments=["done"])


class CrashAfterFirstToolCheckpointStore(SQLiteStateStore):
    """Simulate process death after durable tool state, exactly once."""

    def __init__(self, path):
        super().__init__(path)
        self.crashed = False

    def checkpoint(self, **kwargs):
        super().checkpoint(**kwargs)
        if kwargs["phase"] == "after_tool_result" and not self.crashed:
            self.crashed = True
            raise SystemExit("simulated process death")


class CrashAtCheckpointStore(SQLiteStateStore):
    def __init__(self, path, phase):
        super().__init__(path)
        self.phase = phase
        self.crashed = False

    def checkpoint(self, **kwargs):
        super().checkpoint(**kwargs)
        if kwargs["phase"] == self.phase and not self.crashed:
            self.crashed = True
            raise SystemExit(f"simulated process death at {self.phase}")


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
    task = Task(objective="charge")
    assert controller.run(task).status == TaskStatus.WAITING_APPROVAL
    assert calls == []
    assert any(event["event_type"] == "task.waiting_approval" for event in controller.store.snapshot()["events"])


def test_controller_waiting_approval_can_resume_with_persisted_record(tmp_path):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: calls.append(args) or {"ok": True}))
    provider = ApprovalCallProvider()
    store = SQLiteStateStore(tmp_path / "approval-resume.sqlite3")
    controller = Controller(provider, ToolRuntime(registry), store)
    task = Task(objective="publish")
    assert controller.run(task).status == TaskStatus.WAITING_APPROVAL
    pending = store.load_latest_checkpoint(task.task_id)["state"]["pending_tool_calls"][0]
    store.save_approval("approval-1", task_id=task.task_id, side_effect_level="external_write", actor="human", call_id=pending["call_id"], arguments_hash=canonical_arguments_hash(pending["arguments"]))
    assert controller.resume(task.task_id, approval_id="approval-1").status == TaskStatus.COMPLETED
    assert calls == [{"value": "x"}]
    store.close()


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


def test_persisted_approval_is_task_and_level_scoped(tmp_path):
    task = Task(objective="approved write")
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: calls.append(args) or {"ok": True}))
    call = ToolCall(tool_name="publish", idempotency_key="publish-1")
    with SQLiteStateStore(tmp_path / "approval.sqlite3") as store:
        store.save_approval("approval-1", task_id=task.task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        runtime = ToolRuntime(registry).with_result_store(store)
        denied = runtime.execute(call, task_id=task.task_id, approval_id="wrong-id")
        assert denied.status.value == "denied"
        allowed = runtime.execute(call, task_id=task.task_id, approval_id="approval-1")
        assert allowed.status.value == "succeeded"
        assert calls == [{}]
        assert not store.has_approval("approval-1", task_id=str(Task(objective="other").task_id), side_effect_level="external_write", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))


def test_external_effect_pending_intent_blocks_unsafe_retry(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    effects = []

    def external_handler(args):
        effects.append(args)
        raise SystemExit("crash after external acceptance")

    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=external_handler))
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="publish-1")
    with SQLiteStateStore(path) as store:
        runtime = ToolRuntime(registry).with_result_store(store)
        store.save_approval("approval-1", task_id="11111111-1111-4111-8111-111111111111", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        with pytest.raises(SystemExit, match="external acceptance"):
            runtime.execute(call, task_id="11111111-1111-4111-8111-111111111111", approval_id="approval-1")
    with SQLiteStateStore(path) as reopened:
        result = ToolRuntime(registry).with_result_store(reopened).execute(call, task_id="11111111-1111-4111-8111-111111111111", approval_id="approval-1")
        assert result.error["category"] == "reconciliation_required"
    assert len(effects) == 1


def test_invalid_external_call_creates_no_effect_intent(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", required_arguments=frozenset({"value"}), handler=lambda args: {"ok": True}))
    call = ToolCall(tool_name="publish", arguments={}, idempotency_key="invalid-1")
    with SQLiteStateStore(tmp_path / "invalid-intent.sqlite3") as store:
        store.save_approval("approval-1", task_id="11111111-1111-4111-8111-111111111111", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="11111111-1111-4111-8111-111111111111", approval_id="approval-1")
        assert result.error["category"] == "schema_validation"
        assert store.get_effect_intent("invalid-1") is None


def test_tool_timeout_returns_timeout_without_waiting_for_handler(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="slow", description="slow", side_effect_level="none", timeout_seconds=0.01, handler=lambda args: sleep(0.2) or {"ok": True}))
    with SQLiteStateStore(tmp_path / "timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(ToolCall(tool_name="slow"))
    assert result.status.value == "timeout"
    assert result.error["category"] == "timeout"


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


def test_resume_honors_persisted_wall_clock_deadline(tmp_path):
    task = Task(objective="expired", limits=ExecutionLimits(max_wall_time_seconds=30))
    with SQLiteStateStore(tmp_path / "expired.sqlite3") as store:
        store.save_task(task)
        step = Step(task_id=task.task_id, order=0, kind="model")
        store.save_step(step)
        store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="before_model", state={"messages": [], "tool_results": [], "model_calls": 0, "tool_calls": 0, "next_step_order": 0, "pending_tool_calls": [], "active_step": step.to_dict(), "deadline_epoch": 1.0})
        with pytest.raises(RuntimeFailure, match="timeout"):
            Controller(FinalProvider(), ToolRuntime(ToolRegistry()), store).resume(task.task_id)


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


def test_controller_replaces_provider_idempotency_hint_with_kernel_operation_key(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="write", description="write", side_effect_level="local_write", handler=lambda args: {"ok": True}))
    provider = SingleCallProvider(ToolCall(tool_name="write", arguments={"value": "x"}, idempotency_key="model-chosen"))
    task = Task(objective="kernel operation")
    step = Step(task_id=task.task_id, order=0, kind="model")
    key = Controller._kernel_operation_key(task, step, provider.call, 0)
    assert key.startswith(f"op:{task.task_id}:0:0:")
    assert key != "model-chosen"


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


class FailingProvider(ModelProvider):
    provider_id = "failing"

    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        raise RuntimeError("provider unavailable")
