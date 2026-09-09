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

def test_cancel_does_not_terminalize_task_already_waiting_reconciliation(tmp_path):
    from src.dev_agent.state import JsonStateStore

    store = JsonStateStore(tmp_path / "cancel-waiting.json")
    task = Task(objective="already waiting", status=TaskStatus.WAITING_RECONCILIATION)
    store.save_task(task)
    controller = Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store)

    result = controller.cancel(task.task_id, reason="operator cancelled after ambiguous effect")

    assert result.status == TaskStatus.WAITING_RECONCILIATION
    assert store.load_task(task.task_id).status == TaskStatus.WAITING_RECONCILIATION

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
    assert task.status == TaskStatus.WAITING_RECONCILIATION
    assert store.load_task(task.task_id).status == TaskStatus.WAITING_RECONCILIATION
    waiting = [event for event in store.snapshot()["events"] if event["event_type"] == "task.waiting_reconciliation"]
    assert waiting and waiting[-1]["payload"]["cancellation_state"] == "unable_to_confirm"
    checkpoint = store.load_latest_checkpoint(task.task_id)
    assert checkpoint["state"]["cancellation"]["state"] == "unable_to_confirm"

def test_cancellation_during_guarded_effect_requires_reconciliation(tmp_path):
    started = ThreadEvent()
    calls = []

    def guarded_handler(args):
        started.set()
        sleep(0.2)
        calls.append(args)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(ToolSpec(name="publish", description="external", side_effect_level="external_write", timeout_seconds=1.0, handler=guarded_handler))
    call = ToolCall(tool_name="publish", arguments={"value": "x"}, idempotency_key="cancelled-effect")
    cancel_event = ThreadEvent()
    result_box = []
    with SQLiteStateStore(tmp_path / "cancelled-effect.sqlite3") as store:
        store.save_approval("approval", task_id="t", side_effect_level="external_write", actor="human", call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments))
        runtime = ToolRuntime(registry).with_result_store(store)
        runner = Thread(target=lambda: result_box.append(runtime.execute(call, task_id="t", approval_id="approval", cancel_event=cancel_event)), daemon=True)
        runner.start()
        assert started.wait(2)
        cancel_event.set()
        runner.join(2)
        assert result_box[0].error["category"] == "reconciliation_required"
        assert store.get_effect_intent(call.idempotency_key)["status"] == "unknown"
    # A trusted in-process handler cannot be killed by thread cancellation;
    # the unknown effect state is what makes this late side effect safe to
    # reason about and prevents an unsafe automatic retry.
    sleep(0.25)
    assert calls == [{"value": "x"}]

def test_resume_after_cancelled_commit_crash_does_not_duplicate_cancel_event(tmp_path):
    path = tmp_path / "cancelled-crash.sqlite3"
    task = Task(objective="cancel crash")
    step = Step(task_id=task.task_id, order=0, kind="model")
    state = {"messages": [], "active_step": step.to_dict(), "pending_tool_calls": [], "cancellation": {"state": "requested"}}
    controller = Controller(FinalProvider(), ToolRuntime(ToolRegistry()), CrashAfterCommitStore(path, "cancelled"))
    with controller.store as store:
        with pytest.raises(SystemExit, match="cancelled"):
            controller._cancel(task, state, step=step, message="operator requested cancellation")
    with SQLiteStateStore(path) as reopened:
        resumed = Controller(FinalProvider(), ToolRuntime(ToolRegistry()), reopened).resume(task.task_id)
        events = [event for event in reopened.snapshot()["events"] if event["task_id"] == task.task_id and event["event_type"] == "task.cancelled"]
        checkpoint = reopened.load_latest_checkpoint(task.task_id)
    assert resumed.status == TaskStatus.CANCELLED
    assert len(events) == 1
    assert checkpoint["state"]["cancellation"]["state"] == "terminated"

def test_controller_marks_guarded_cancellation_unable_to_confirm(tmp_path):
    started = ThreadEvent()

    def guarded_handler(args):
        started.set()
        sleep(0.2)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(ToolSpec(name="write", description="local effect", side_effect_level="local_write", handler=guarded_handler))
    call = ToolCall(tool_name="write", arguments={"value": "x"})
    store = JsonStateStore(tmp_path / "controller-cancel-uncertain.json")
    controller = Controller(SingleCallProvider(call), ToolRuntime(registry), store)
    task = Task(objective="cancel guarded effect")
    result_box = []
    runner = Thread(target=lambda: result_box.append(controller.run(task)), daemon=True)
    runner.start()
    assert started.wait(2)
    controller.cancel(task.task_id, reason="operator cancelled after dispatch")
    runner.join(2)
    assert not runner.is_alive()
    assert result_box[0].status == TaskStatus.WAITING_RECONCILIATION
    checkpoint = store.load_latest_checkpoint(task.task_id)
    assert checkpoint["state"]["cancellation"]["state"] == "unable_to_confirm"
    waiting = [event for event in store.snapshot()["events"] if event["event_type"] == "task.waiting_reconciliation"]
    assert waiting[-1]["payload"]["cancellation_state"] == "unable_to_confirm"

def test_cancellation_after_provider_deadline_preserves_unable_to_confirm(tmp_path):
    started = ThreadEvent()

    class SlowProvider(ModelProvider):
        provider_id = "deadline-cancellable"

        def request(self, request):
            started.set()
            sleep(0.2)
            return ModelResponse(provider=self.provider_id, model="test", text_segments=["late"])

    store = JsonStateStore(tmp_path / "deadline-cancel.json")
    controller = Controller(SlowProvider(), ToolRuntime(ToolRegistry()), store)
    task = Task(objective="cancel at deadline", limits=ExecutionLimits(max_wall_time_seconds=0.1))
    result_box = []
    runner = Thread(target=lambda: result_box.append(controller.run(task)), daemon=True)
    runner.start()
    assert started.wait(2)
    controller.cancel(task.task_id, reason="operator cancelled at deadline")
    runner.join(2)

    assert not runner.is_alive()
    assert result_box[0].status == TaskStatus.WAITING_RECONCILIATION
    checkpoint = store.load_latest_checkpoint(task.task_id)
    assert checkpoint["state"]["cancellation"]["state"] == "unable_to_confirm"
    waiting = [event for event in store.snapshot()["events"] if event["event_type"] == "task.waiting_reconciliation"]
    assert waiting[-1]["payload"]["cancellation_state"] == "unable_to_confirm"

def test_provider_cancellation_during_request_requires_reconciliation(tmp_path):
    started = ThreadEvent()
    calls = []

    class SlowProvider(ModelProvider):
        provider_id = "provider-cancellable"

        def request(self, request):
            started.set()
            calls.append(request.request_id)
            sleep(0.2)
            return ModelResponse(provider=self.provider_id, model="test", text_segments=["late"])

    store = JsonStateStore(tmp_path / "provider-cancel-uncertain.json")
    controller = Controller(SlowProvider(), ToolRuntime(ToolRegistry()), store)
    task = Task(objective="cancel provider request")
    result_box = []
    runner = Thread(target=lambda: result_box.append(controller.run(task)), daemon=True)
    runner.start()
    assert started.wait(2)
    controller.cancel(task.task_id, reason="operator cancelled during provider request")
    runner.join(2)
    assert not runner.is_alive()
    assert result_box[0].status == TaskStatus.WAITING_RECONCILIATION
    checkpoint = store.load_latest_checkpoint(task.task_id)
    assert checkpoint["state"]["cancellation"]["state"] == "unable_to_confirm"
    assert controller.resume(task.task_id).status == TaskStatus.WAITING_RECONCILIATION
    assert len(calls) == 1
    waiting = [event for event in store.snapshot()["events"] if event["event_type"] == "task.waiting_reconciliation"]
    assert waiting[-1]["payload"]["cancellation_state"] == "unable_to_confirm"
