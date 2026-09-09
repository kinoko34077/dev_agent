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

def test_process_timeout_terminates_descendant_after_worker_exit(tmp_path):
    marker = tmp_path / "detached-child-marker"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="detached",
            description="detached child",
            handler=isolated_detached_child_handler,
            isolation="subprocess",
            timeout_seconds=0.05,
            handler_ref="tests.v2.integration_hardening_support:isolated_detached_child_handler",
        )
    )
    call = ToolCall(tool_name="detached", arguments={"marker": str(marker)})
    with SQLiteStateStore(tmp_path / "detached-timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(call)

    assert result.status is ToolResultStatus.TIMEOUT
    sleep(0.1)
    assert not marker.exists()

def test_tool_registry_rejects_schema_keywords_runtime_cannot_enforce():
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        ToolSpec(name="unsafe_schema", description="", input_schema={"type": "object", "oneOf": []}, handler=lambda args: {})

def test_process_isolated_tool_is_killed_at_timeout(tmp_path):
    marker = tmp_path / "late-marker.txt"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="process",
            description="isolated",
            side_effect_level="process",
            isolation="subprocess",
            timeout_seconds=0.05,
            handler=isolated_slow_handler,
            handler_ref="tests.v2.integration_hardening_support:isolated_slow_handler",
        )
    )
    call = ToolCall(tool_name="process", arguments={"marker": str(marker)}, idempotency_key="process-timeout")
    with SQLiteStateStore(tmp_path / "process-timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(call)
    assert result.status == ToolResultStatus.TIMEOUT
    assert result.error["category"] == "reconciliation_required"
    assert result.error["cause"] == "timeout"
    sleep(0.1)
    assert not marker.exists()

def test_tool_argument_and_result_byte_limits_are_enforced(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="bounded", description="bounded", max_argument_bytes=20, handler=lambda args: {"blob": "x" * 100}, max_result_bytes=20))
    with SQLiteStateStore(tmp_path / "limits.sqlite3") as store:
        runtime = ToolRuntime(registry).with_result_store(store)
        too_large = runtime.execute(ToolCall(tool_name="bounded", arguments={"blob": "x" * 100}, idempotency_key="arg-limit"))
        assert too_large.error["category"] == "limits_exceeded"
        small = runtime.execute(ToolCall(tool_name="bounded", arguments={}, idempotency_key="result-limit"))
        assert small.error["category"] == "limits_exceeded"

def test_tool_timeout_returns_timeout_without_waiting_for_handler(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolSpec(name="slow", description="slow", side_effect_level="none", timeout_seconds=0.01, handler=lambda args: sleep(0.2) or {"ok": True}))
    with SQLiteStateStore(tmp_path / "timeout.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(ToolCall(tool_name="slow"))
    assert result.status.value == "timeout"
    assert result.error["category"] == "timeout"

def test_process_timeout_terminates_descendant_processes(tmp_path):
    marker = tmp_path / "child-marker.txt"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="process_tree",
            description="isolated",
            side_effect_level="process",
            isolation="subprocess",
            timeout_seconds=0.2,
            handler=isolated_child_handler,
            handler_ref="tests.v2.integration_hardening_support:isolated_child_handler",
        )
    )
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

def test_generated_tool_uses_subprocess_boundary_for_normal_execution(tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="generated_echo",
            description="generated",
            trust_level="generated",
            isolation="subprocess",
            input_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}},
            output_schema={"type": "object", "required": ["echo"], "properties": {"echo": {"type": "string"}}},
            handler=isolated_generated_echo_handler,
            handler_ref="tests.v2.integration_hardening_support:isolated_generated_echo_handler",
        )
    )
    call = ToolCall(tool_name="generated_echo", arguments={"value": "ok"})
    with SQLiteStateStore(tmp_path / "generated.sqlite3") as store:
        result = ToolRuntime(registry).with_result_store(store).execute(call)
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.structured_result == {"echo": "ok"}

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
