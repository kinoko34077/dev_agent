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



__all__ = ['slow_external_handler', 'invalid_external_output_handler', 'isolated_slow_handler', 'isolated_child_handler', 'isolated_detached_child_handler', 'isolated_generated_echo_handler', 'FinalProvider', 'SingleCallProvider', 'ApprovalCallProvider', 'MultiCallThenFinalProvider', 'CrashAfterFirstToolCheckpointStore', 'CrashAtCheckpointStore', 'CommitOnlyStore', 'CrashAfterCommitStore', 'FailingProvider']

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

def isolated_detached_child_handler(args):
    child_code = "import time; from pathlib import Path; time.sleep(0.5); Path(r'" + args["marker"] + "').write_text('late', encoding='utf-8')"
    subprocess.Popen([sys.executable, "-c", child_code], close_fds=False)
    return {"ok": True}

def isolated_generated_echo_handler(args):
    return {"echo": args["value"]}

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

class FailingProvider(ModelProvider):
    provider_id = "failing"

    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        raise RuntimeError("provider unavailable")
