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

def test_effect_intent_state_machine_rejects_terminal_reopen(tmp_path):
    with SQLiteStateStore(tmp_path / "state-machine.sqlite3") as store:
        assert store.create_effect_intent("k", task_id="t", tool_name="publish", arguments={})
        store.transition_effect_intent("k", to_status="dispatching")
        store.transition_effect_intent("k", to_status="unknown", result={"reason": "timeout"})
        store.transition_effect_intent("k", to_status="reconciling")
        store.transition_effect_intent("k", to_status="succeeded", result={"ok": True})
        with pytest.raises(ValueError, match="invalid effect intent transition"):
            store.transition_effect_intent("k", to_status="dispatching")

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

def test_external_handler_exception_is_unknown_not_terminal_failure(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", handler=lambda args: (_ for _ in ()).throw(ConnectionError("response lost"))))
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="unknown-1")
    with SQLiteStateStore(tmp_path / "unknown.sqlite3") as store:
        store.save_approval("a", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="t", approval_id="a")
        assert result.error["category"] == "reconciliation_required"
        assert store.get_effect_intent("unknown-1")["status"] == "unknown"

def test_reconciliation_api_records_actor_source_and_evidence(tmp_path):
    with SQLiteStateStore(tmp_path / "reconcile-api.sqlite3") as store:
        assert store.create_effect_intent("k", task_id="t", tool_name="publish", arguments={})
        store.reconcile_effect_intent("k", status="succeeded", actor="operator", source="mock-service", external_id="ext-1", evidence={"matched": True})
        intent = store.get_effect_intent("k")
        assert intent["status"] == "succeeded"
        row = store.connection.execute("SELECT actor, source, external_id, evidence_payload FROM effect_reconciliations WHERE idempotency_key = 'k'").fetchone()
        assert (row["actor"], row["source"], row["external_id"]) == ("operator", "mock-service", "ext-1")

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

def test_resume_after_reconciliation_wait_commit_crash_reconciles_without_duplicate_toolcall(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", timeout_seconds=0.01, handler=slow_external_handler))
    provider = ApprovalCallProvider()
    task = Task(objective="reconciliation crash boundary")
    path = tmp_path / "reconciliation-crash.sqlite3"
    with CrashAfterCommitStore(path, "waiting_reconciliation") as store:
        controller = Controller(provider, ToolRuntime(registry), store)
        assert controller.run(task).status == TaskStatus.WAITING_APPROVAL
        pending = store.load_latest_checkpoint(task.task_id)["state"]["pending_tool_calls"][0]
        store.save_approval(
            "approval-before-reconciliation-crash",
            task_id=task.task_id,
            side_effect_level="external_write",
            actor="human",
            call_id=pending["call_id"],
            arguments_hash=canonical_arguments_hash(pending["arguments"]),
        )
        with pytest.raises(SystemExit, match="waiting_reconciliation"):
            controller.resume(task.task_id, approval_id="approval-before-reconciliation-crash")
    with SQLiteStateStore(path) as reopened:
        call = ToolCall.from_dict(pending)
        confirmed = ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.SUCCEEDED, structured_result={"ok": True})
        reopened.reconcile_effect_intent(
            call.idempotency_key,
            status="succeeded",
            actor="operator",
            source="provider-confirmation",
            result=confirmed,
        )
        result = Controller(provider, ToolRuntime(registry), reopened).resume(task.task_id)
    assert result.status == TaskStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(provider.requests[1].tool_results) == 1

def test_invalid_external_call_creates_no_effect_intent(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", required_arguments=frozenset({"value"}), handler=lambda args: {"ok": True}))
    call = ToolCall(tool_name="publish", arguments={}, idempotency_key="invalid-1")
    with SQLiteStateStore(tmp_path / "invalid-intent.sqlite3") as store:
        store.save_approval("approval-1", task_id="11111111-1111-4111-8111-111111111111", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        result = ToolRuntime(registry).with_result_store(store).execute(call, task_id="11111111-1111-4111-8111-111111111111", approval_id="approval-1")
        assert result.error["category"] == "schema_validation"
        assert store.get_effect_intent("invalid-1") is None

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
