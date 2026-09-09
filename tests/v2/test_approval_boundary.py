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

def test_tool_contract_requires_a_non_empty_version():
    with pytest.raises(ValueError, match="version"):
        ToolSpec(name="versioned", description="tool", handler=lambda args: {}, version="")

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
