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
from src.dev_agent.runtime import Controller, RuntimeFailure
from src.dev_agent.state import JsonStateStore, SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


def slow_external_handler(args):
    sleep(0.2)
    return {"ok": True}


def invalid_external_output_handler(args):
    return {"ok": "not-a-boolean"}


def isolated_slow_handler(args):
    sleep(0.4)
    __import__("pathlib").Path(args["marker"]).write_text("late", encoding="utf-8")
    return {"ok": True}


def isolated_child_handler(args):
    child_code = "import time; from pathlib import Path; time.sleep(0.4); Path(r'" + args["child_marker"] + "').write_text('child', encoding='utf-8')"
    subprocess.Popen([sys.executable, "-c", child_code])
    sleep(0.4)
    return {"ok": True}


def test_event_payload_detects_secret_values_and_total_byte_cap():
    safe = Controller._safe_event_payload({"content": "bearer abcdefghijklmnop", "large": ["x" * 4096] * 20})
    assert safe["_truncated"] is True
    assert safe["payload_ref"].startswith("event-sha256:")
    assert "abcdefghijklmnop" not in str(safe)


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


class CommitOnlyStore(SQLiteStateStore):
    """Reject legacy per-record writes so Controller integration is observable."""

    def __init__(self, path):
        super().__init__(path)
        self.transitions = []

    def commit_transition(self, **kwargs):
        checkpoint = kwargs.get("checkpoint") or {}
        self.transitions.append({"phase": checkpoint.get("phase"), "events": [event.event_type for event in kwargs.get("events") or []], "tool_result": kwargs.get("tool_result") is not None})
        return super().commit_transition(**kwargs)

    def save_task(self, task):
        raise AssertionError("Controller must use commit_transition")

    def save_step(self, step):
        raise AssertionError("Controller must use commit_transition")

    def save_tool_result(self, result):
        raise AssertionError("Controller must use commit_transition")

    def append_event(self, event):
        raise AssertionError("Controller must use commit_transition")


class CrashAfterCommitStore(SQLiteStateStore):
    def __init__(self, path, phase):
        super().__init__(path)
        self.phase = phase
        self.crashed = False

    def commit_transition(self, **kwargs):
        result = super().commit_transition(**kwargs)
        checkpoint = kwargs.get("checkpoint") or {}
        if checkpoint.get("phase") == self.phase and not self.crashed:
            self.crashed = True
            raise SystemExit(f"simulated process death after {self.phase}")
        return result


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
    assert resumed.status == TaskStatus.COMPLETED
    assert (len(provider.requests) == 1) if phase == "before_model" else (len(provider.requests) == 2)
    assert len(steps) == (1 if phase in {"before_model", "after_tool_result"} else 2)
    if phase != "before_model":
        assert effects == [{"value": "x"}]


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


def test_event_payload_redacts_secrets_and_caps_large_strings():
    safe = Controller._safe_event_payload({"api_key": "fake-secret", "nested": {"password": "pw"}, "blob": "x" * 5000})
    assert safe["api_key"] == "[REDACTED]"
    assert safe["nested"]["password"] == "[REDACTED]"
    assert safe["blob"].endswith("...[TRUNCATED]")
    assert len(safe["blob"]) == 4096 + len("...[TRUNCATED]")


def test_approval_binds_to_effective_canonical_path_arguments(tmp_path):
    workspace = tmp_path / "workspace"
    sandbox = workspace / "sandbox"
    sandbox.mkdir(parents=True)
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish_file", description="external", side_effect_level="external_write", path_argument="path", path_operation="write", handler=lambda args: calls.append(args) or {"ok": True}))
    call = ToolCall(tool_name="publish_file", arguments={"path": "sandbox/out.txt"}, idempotency_key="effective-path")
    with SQLiteStateStore(tmp_path / "effective.sqlite3") as store:
        effective = str((sandbox / "out.txt").resolve())
        store.save_approval("a", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash({"path": effective}))
        result = ToolRuntime(registry, paths=PathPolicy(workspace, {"sandbox": {"write"}})).with_result_store(store).execute(call, task_id="t", approval_id="a")
        assert result.status == ToolResultStatus.SUCCEEDED
        assert calls == [{"path": effective}]


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


def test_resume_after_waiting_approval_commit_crash_does_not_duplicate_tool_decision(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: {"ok": True}))
    provider = ApprovalCallProvider()
    task = Task(objective="approval crash boundary")
    path = tmp_path / "approval-crash.sqlite3"
    with CrashAfterCommitStore(path, "waiting_approval") as store:
        with pytest.raises(SystemExit, match="waiting_approval"):
            Controller(provider, ToolRuntime(registry), store).run(task)
    with SQLiteStateStore(path) as reopened:
        pending = reopened.load_latest_checkpoint(task.task_id)["state"]["pending_tool_calls"][0]
        reopened.save_approval(
            "approval-after-crash",
            task_id=task.task_id,
            side_effect_level="external_write",
            actor="human",
            call_id=pending["call_id"],
            arguments_hash=canonical_arguments_hash(pending["arguments"]),
        )
        result = Controller(provider, ToolRuntime(registry), reopened).resume(task.task_id, approval_id="approval-after-crash")
    assert result.status == TaskStatus.COMPLETED
    # The second request is the legitimate final-response request after the
    # approved ToolCall; the model's original ToolCall is not regenerated.
    assert len(provider.requests) == 2
    assert len(provider.requests[1].tool_results) == 1


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
        conflict = runtime.execute(ToolCall(tool_name="publish", arguments={"value": "different"}, idempotency_key="publish-1"), task_id=task.task_id)
        assert conflict.error["category"] == "idempotency_conflict"
        assert calls == [{}]
        assert not store.has_approval("approval-1", task_id=str(Task(objective="other").task_id), side_effect_level="external_write", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))


def test_approval_expiry_and_revoke_are_enforced_without_creating_effect_intent(tmp_path):
    task_id = "11111111-1111-4111-8111-111111111111"
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="approval-expiry")
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: {"ok": True}))
    with SQLiteStateStore(tmp_path / "approval-expiry.sqlite3") as store:
        store.save_approval("expired", task_id=task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments), expires_at=time() - 1)
        expired = ToolRuntime(registry).with_result_store(store).execute(call, task_id=task_id, approval_id="expired")
        assert expired.error["category"] == "approval_required"
        assert store.get_effect_intent(call.idempotency_key) is None
        store.save_approval("revoked", task_id=task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments), expires_at=time() + 60)
        store.revoke_approval("revoked")
        revoked = ToolRuntime(registry).with_result_store(store).execute(ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="approval-revoked", call_id=call.call_id), task_id=task_id, approval_id="revoked")
        assert revoked.error["category"] == "approval_required"
        assert store.get_effect_intent("approval-revoked") is None


def test_approval_records_are_immutable_and_one_shot(tmp_path):
    task_id = "11111111-1111-4111-8111-111111111111"
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="approval-once")
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: {"ok": True}))
    with SQLiteStateStore(tmp_path / "approval-immutable.sqlite3") as store:
        kwargs = dict(task_id=task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        store.save_approval("approval-1", **kwargs)
        with pytest.raises(Exception):
            store.save_approval("approval-1", **kwargs)
        runtime = ToolRuntime(registry).with_result_store(store)
        assert runtime.execute(call, task_id=task_id, approval_id="approval-1").status.value == "succeeded"
        replay = runtime.execute(ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="approval-once-2", call_id=call.call_id), task_id=task_id, approval_id="approval-1")
        assert replay.error["category"] == "approval_required"


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
        conflict = ToolRuntime(registry).with_result_store(reopened).execute(
            ToolCall(tool_name="publish", arguments={"value": "different"}, idempotency_key="publish-1"),
            task_id="11111111-1111-4111-8111-111111111111",
            approval_id="approval-1",
        )
        assert conflict.error["category"] == "idempotency_conflict"
    assert len(effects) == 1


def test_external_handler_exception_is_unknown_not_terminal_failure(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: (_ for _ in ()).throw(ConnectionError("response lost"))))
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="unknown-1")
    with SQLiteStateStore(tmp_path / "unknown.sqlite3") as store:
        store.save_approval("a", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="t", approval_id="a")
        assert result.error["category"] == "reconciliation_required"
        assert store.get_effect_intent("unknown-1")["status"] == "unknown"


def test_external_timeout_is_reconciliation_required_and_marks_unknown(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", timeout_seconds=0.01, handler=slow_external_handler))
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="timeout-unknown")
    with SQLiteStateStore(tmp_path / "external-timeout.sqlite3") as store:
        store.save_approval("approval", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="t", approval_id="approval")
        assert result.status == ToolResultStatus.TIMEOUT
        assert result.error == {"category": "reconciliation_required", "cause": "timeout", "message": "tool execution timed out"}
        assert store.get_effect_intent(call.idempotency_key)["status"] == "unknown"


def test_external_output_validation_failure_is_reconciliation_required(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", output_schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}, handler=invalid_external_output_handler))
    call = ToolCall(tool_name="publish", arguments={}, idempotency_key="schema-unknown")
    with SQLiteStateStore(tmp_path / "external-schema.sqlite3") as store:
        store.save_approval("approval", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="t", approval_id="approval")
        assert result.status == ToolResultStatus.FAILED
        assert result.error["category"] == "reconciliation_required"
        assert result.error["cause"] == "schema_validation"
        assert store.get_effect_intent(call.idempotency_key)["status"] == "unknown"


def test_controller_maps_external_timeout_to_waiting_reconciliation(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", timeout_seconds=0.01, handler=slow_external_handler))
    provider = ApprovalCallProvider()
    with SQLiteStateStore(tmp_path / "controller-reconcile.sqlite3") as store:
        controller = Controller(provider, ToolRuntime(registry), store)
        task = Task(objective="publish")
        assert controller.run(task).status == TaskStatus.WAITING_APPROVAL
        pending = store.load_latest_checkpoint(task.task_id)["state"]["pending_tool_calls"][0]
        call = ToolCall.from_dict(pending)
        store.save_approval("approval", task_id=task.task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        assert controller.resume(task.task_id, approval_id="approval").status == TaskStatus.WAITING_RECONCILIATION
        intent = store.get_effect_intent(call.idempotency_key)
        assert intent["status"] == "unknown"
        confirmed = ToolResult(call_id=call.call_id, tool_name="publish", status=ToolResultStatus.SUCCEEDED, structured_result={"ok": True})
        store.transition_effect_intent(call.idempotency_key, to_status="reconciling")
        store.transition_effect_intent(call.idempotency_key, to_status="succeeded", result=confirmed.to_dict())
        assert controller.resume(task.task_id).status == TaskStatus.COMPLETED


def test_process_isolated_tool_is_killed_at_timeout(tmp_path):
    marker = tmp_path / "late-marker.txt"
    registry = ToolRegistry()
    registry.register(ToolSpec(name="process", description="isolated", side_effect_level="process", isolation="subprocess", timeout_seconds=0.05, handler=isolated_slow_handler))
    call = ToolCall(tool_name="process", arguments={"marker": str(marker)}, idempotency_key="process-timeout")
    with SQLiteStateStore(tmp_path / "process-timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(call)
    assert result.status == ToolResultStatus.TIMEOUT
    assert result.error["category"] == "reconciliation_required"
    assert result.error["cause"] == "timeout"
    sleep(0.1)
    assert not marker.exists()


def test_process_timeout_terminates_descendant_processes(tmp_path):
    marker = tmp_path / "child-marker.txt"
    registry = ToolRegistry()
    registry.register(ToolSpec(name="process_tree", description="isolated", side_effect_level="process", isolation="subprocess", timeout_seconds=0.2, handler=isolated_child_handler))
    call = ToolCall(tool_name="process_tree", arguments={"child_marker": str(marker)}, idempotency_key="process-tree-timeout")
    with SQLiteStateStore(tmp_path / "process-tree-timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(call)
    assert result.error["category"] == "reconciliation_required"
    sleep(0.5)
    assert not marker.exists()


def test_untrusted_and_generated_tools_cannot_opt_into_in_process_execution():
    with pytest.raises(ValueError, match="subprocess isolation"):
        ToolSpec(name="untrusted", description="", trust_level="untrusted", handler=lambda args: {})
    with pytest.raises(ValueError, match="subprocess isolation"):
        ToolSpec(name="generated", description="", trust_level="generated", handler=lambda args: {})


def test_controller_cancellation_is_cooperative_and_durable(tmp_path):
    started = ThreadEvent()

    class SlowProvider(ModelProvider):
        provider_id = "cancellable"

        def request(self, request):
            started.set()
            sleep(0.2)
            return ModelResponse(provider=self.provider_id, model="test", text_segments=["late"])

    from src.dev_agent.state import JsonStateStore

    store = JsonStateStore(tmp_path / "cancel.json")
    controller = Controller(SlowProvider(), ToolRuntime(ToolRegistry()), store)
    task = Task(objective="cancel me")
    runner = Thread(target=lambda: controller.run(task), daemon=True)
    runner.start()
    assert started.wait(2)
    controller.cancel(task.task_id)
    runner.join(2)
    assert not runner.is_alive()
    assert task.status == TaskStatus.CANCELLED
    assert store.load_task(task.task_id).status == TaskStatus.CANCELLED
    assert any(event["event_type"] == "task.cancelled" for event in store.snapshot()["events"])


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


def test_effect_intent_state_machine_rejects_terminal_reopen(tmp_path):
    with SQLiteStateStore(tmp_path / "state-machine.sqlite3") as store:
        assert store.create_effect_intent("k", task_id="t", tool_name="publish", arguments={})
        store.transition_effect_intent("k", to_status="dispatching")
        store.transition_effect_intent("k", to_status="unknown", result={"reason": "timeout"})
        store.transition_effect_intent("k", to_status="reconciling")
        store.transition_effect_intent("k", to_status="succeeded", result={"ok": True})
        with pytest.raises(ValueError, match="invalid effect intent transition"):
            store.transition_effect_intent("k", to_status="dispatching")


def test_reconciliation_api_records_actor_source_and_evidence(tmp_path):
    with SQLiteStateStore(tmp_path / "reconcile-api.sqlite3") as store:
        assert store.create_effect_intent("k", task_id="t", tool_name="publish", arguments={})
        store.reconcile_effect_intent("k", status="succeeded", actor="operator", source="mock-service", external_id="ext-1", evidence={"matched": True})
        intent = store.get_effect_intent("k")
        assert intent["status"] == "succeeded"
        row = store.connection.execute("SELECT actor, source, external_id, evidence_payload FROM effect_reconciliations WHERE idempotency_key = 'k'").fetchone()
        assert (row["actor"], row["source"], row["external_id"]) == ("operator", "mock-service", "ext-1")


def test_confirmed_failed_reconciliation_terminalizes_task_without_retry(tmp_path):
    task = Task(objective="confirmed failure")
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="confirmed-failure")
    step = Step(task_id=task.task_id, order=0, kind="model", status=StepStatus.WAITING)
    state = {
        "messages": [{"role": "user", "content": task.objective}],
        "tool_results": [],
        "model_calls": 1,
        "tool_calls": 1,
        "next_step_order": 0,
        "pending_tool_calls": [call.to_dict()],
        "active_step": step.to_dict(),
        "deadline_epoch": time() + 30,
    }
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="side effect", side_effect_level="local_write", handler=lambda args: calls.append(args) or {"ok": True}))
    with SQLiteStateStore(tmp_path / "confirmed-failed.sqlite3") as store:
        task.status = TaskStatus.WAITING_RECONCILIATION
        store.save_task(task)
        store.save_step(step)
        store.checkpoint(task_id=task.task_id, step_id=step.step_id, phase="waiting_reconciliation", state=state)
        store.create_effect_intent(call.idempotency_key, task_id=task.task_id, tool_name=call.tool_name, arguments=call.arguments)
        store.transition_effect_intent(call.idempotency_key, to_status="dispatching")
        store.mark_effect_unknown(call.idempotency_key, reason="remote rejected request")
        store.reconcile_effect_intent(call.idempotency_key, status="confirmed_failed", actor="operator", source="remote-api", evidence={"rejected": True})
        with pytest.raises(RuntimeFailure, match="effect_confirmed_failed"):
            Controller(FinalProvider(), ToolRuntime(registry), store).resume(task.task_id)
        resumed = store.load_task(task.task_id)

    assert resumed is not None and resumed.status == TaskStatus.FAILED
    assert calls == []


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


def test_controller_pauses_for_reconciliation_and_resumes_after_recorded_success(tmp_path):
    path = tmp_path / "reconcile.sqlite3"
    task_id = "11111111-1111-4111-8111-111111111111"
    effects = []
    def external_handler(args):
        effects.append(args)
        raise SystemExit("accepted then local crash")
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=external_handler))
    provider = ApprovalCallProvider()
    with SQLiteStateStore(path) as store:
        controller = Controller(provider, ToolRuntime(registry), store)
        task = Task(objective="reconcile")
        assert controller.run(task).status == TaskStatus.WAITING_APPROVAL
        pending = store.load_latest_checkpoint(task.task_id)["state"]["pending_tool_calls"][0]
        call = ToolCall.from_dict(pending)
        store.save_approval("approval-1", task_id=task.task_id, side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        with pytest.raises(SystemExit):
            controller.resume(task.task_id, approval_id="approval-1")
    with SQLiteStateStore(path) as store:
        task = store.load_task(task.task_id)
        assert task is not None
        # The crash leaves the durable intent pending; reconciliation confirms it.
        intent = store.snapshot()
        keys = [row["idempotency_key"] for row in store.connection.execute("SELECT idempotency_key FROM effect_intents").fetchall()]
        assert keys
        store.complete_effect_intent(keys[0], ToolResult(call_id=call.call_id, tool_name="publish", status=ToolResultStatus.SUCCEEDED, structured_result={"ok": True}))
        resumed = Controller(FinalProvider(), ToolRuntime(registry), store).resume(task.task_id)
        assert resumed.status == TaskStatus.COMPLETED
    assert effects == [{"value": "x"}]


def test_invalid_external_call_creates_no_effect_intent(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", required_arguments=frozenset({"value"}), handler=lambda args: {"ok": True}))
    call = ToolCall(tool_name="publish", arguments={}, idempotency_key="invalid-1")
    with SQLiteStateStore(tmp_path / "invalid-intent.sqlite3") as store:
        store.save_approval("approval-1", task_id="11111111-1111-4111-8111-111111111111", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="11111111-1111-4111-8111-111111111111", approval_id="approval-1")
        assert result.error["category"] == "schema_validation"
        assert store.get_effect_intent("invalid-1") is None


def test_tool_input_schema_rejects_nested_type_enum_and_extra_before_handler(tmp_path):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="typed", description="typed", side_effect_level="local_write", input_schema={"type": "object", "properties": {"amount": {"type": "integer", "minimum": 1}, "mode": {"type": "string", "enum": ["safe"]}}, "required": ["amount", "mode"], "additionalProperties": False}, handler=lambda args: calls.append(args) or {"ok": True}))
    with SQLiteStateStore(tmp_path / "schema.sqlite3") as store:
        runtime = ToolRuntime(registry).with_result_store(store)
        result = runtime.execute(ToolCall(tool_name="typed", arguments={"amount": "one", "mode": "unsafe", "extra": True}, idempotency_key="typed-1"))
    assert result.error["category"] == "schema_validation"
    assert calls == []


def test_tool_output_schema_rejects_untrusted_handler_output(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="bad_output", description="bad", output_schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}, handler=lambda args: {"ok": "yes"}))
    with SQLiteStateStore(tmp_path / "output-schema.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(ToolCall(tool_name="bad_output"))
    assert result.status.value == "failed"
    assert result.error["category"] == "schema_validation"


def test_tool_argument_and_result_byte_limits_are_enforced(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="bounded", description="bounded", max_argument_bytes=20, handler=lambda args: {"blob": "x" * 100}, max_result_bytes=20))
    with SQLiteStateStore(tmp_path / "limits.sqlite3") as store:
        runtime = ToolRuntime(registry).with_result_store(store)
        too_large = runtime.execute(ToolCall(tool_name="bounded", arguments={"blob": "x" * 100}, idempotency_key="arg-limit"))
        assert too_large.error["category"] == "limits_exceeded"
        small = runtime.execute(ToolCall(tool_name="bounded", arguments={}, idempotency_key="result-limit"))
        assert small.error["category"] == "limits_exceeded"


def test_tool_registry_rejects_schema_keywords_runtime_cannot_enforce():
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        ToolSpec(name="unsafe_schema", description="", input_schema={"type": "object", "oneOf": []}, handler=lambda args: {})


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


def test_kernel_operation_key_binds_to_canonical_effective_path(tmp_path):
    workspace = tmp_path / "workspace"
    sandbox = workspace / "sandbox"
    sandbox.mkdir(parents=True)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read_file", description="read", path_argument="path", path_operation="read", handler=lambda args: {"ok": True}))
    runtime = ToolRuntime(
        registry,
        paths=PathPolicy(workspace, {"sandbox": {"read"}}),
    )
    task = Task(objective="canonical identity")
    step = Step(task_id=task.task_id, order=0, kind="model")
    relative = ToolCall(tool_name="read_file", arguments={"path": "sandbox/data.txt"})
    absolute = ToolCall(tool_name="read_file", arguments={"path": str((sandbox / "data.txt").resolve())})
    first = Controller._kernel_operation_key(task, step, relative, 0, effective_arguments=runtime.effective_arguments(relative))
    second = Controller._kernel_operation_key(task, step, absolute, 0, effective_arguments=runtime.effective_arguments(absolute))
    assert first == second


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
