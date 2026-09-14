from __future__ import annotations

import time
from pathlib import Path

from scripts.devfarm_commander import create_plan
from scripts.devfarm_mcp import SupervisorMcpBinding
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun

from src.dev_agent.mcp import (
    McpResultStatus,
    McpRuntimeAdapter,
    McpToolName,
    McpToolRequest,
    McpUnknownOutcome,
)


def test_runtime_adapter_delegates_and_redacts_handler_data():
    secret = "Bearer secret-value-123456"
    captured = []

    def status(arguments):
        captured.append(dict(arguments))
        return {"data": {"state": "READY", "authorization": secret}}

    adapter = McpRuntimeAdapter({McpToolName.STATUS: status})
    result = adapter.invoke(
        McpToolRequest(request_id="req-001", tool="status", arguments={"run_id": "run-001"})
    )

    assert adapter.connected_tools == (McpToolName.STATUS,)
    assert captured == [{"run_id": "run-001"}]
    assert result.status is McpResultStatus.OK
    assert result.data["authorization"] == "[REDACTED]"
    assert secret not in str(result.to_dict())


def test_runtime_adapter_rejects_unconnected_tools_without_fallback():
    adapter = McpRuntimeAdapter({})

    result = adapter.invoke({"request_id": "req-002", "tool": "artifact_summary", "arguments": {}})

    assert result.status is McpResultStatus.REJECTED
    assert result.error_code == "ADAPTER_NOT_CONNECTED"


def test_runtime_adapter_requires_existing_authority_for_mutation():
    called = []

    def review(arguments):
        called.append(arguments)
        return {"data": {"accepted": True}}

    adapter = McpRuntimeAdapter({"review": review})
    denied = adapter.invoke({"request_id": "req-003", "tool": "review", "arguments": {}})

    assert denied.status is McpResultStatus.REJECTED
    assert denied.error_code == "APPROVAL_REQUIRED"
    assert called == []

    approved_adapter = McpRuntimeAdapter(
        {"review": review},
        authorize=lambda request: request.arguments.get("approved") is True,
    )
    approved = approved_adapter.invoke(
        {"request_id": "req-004", "tool": "review", "arguments": {"approved": True}}
    )

    assert approved.status is McpResultStatus.OK
    assert called == [{"approved": True}]


def test_runtime_adapter_preserves_unknown_external_outcome():
    def run(arguments):
        raise McpUnknownOutcome("provider_effect_unknown")

    adapter = McpRuntimeAdapter({"run": run}, authorize=lambda request: True)
    result = adapter.invoke({"request_id": "req-005", "tool": "run", "arguments": {}})

    assert result.status is McpResultStatus.UNKNOWN
    assert result.error_code == "provider_effect_unknown"


def test_runtime_adapter_turns_overdue_reconcilable_handler_into_unknown():
    def run(arguments):
        time.sleep(0.02)
        return {"data": {"finished": True}}

    adapter = McpRuntimeAdapter({"run": run}, authorize=lambda request: True)
    result = adapter.invoke(
        McpToolRequest(request_id="req-006", tool="run", arguments={}, timeout_seconds=0.001)
    )

    assert result.status is McpResultStatus.UNKNOWN
    assert result.error_code == "MCP_TIMEOUT"


def test_runtime_adapter_can_bind_an_existing_supervisor_authority(tmp_path: Path):
    create_plan(
        tmp_path,
        {
            "run_id": "mcp-supervisor-001",
            "objective": "exercise the existing supervisor boundary",
            "base_revision": "abc123",
            "tasks": [
                {
                    "task_id": "mcp-task-001",
                    "owner": "codex",
                    "ownership": [],
                    "dependencies": [],
                    "assignment": {"owner": "codex"},
                }
            ],
            "ownership": [{"task_id": "mcp-task-001", "paths": []}],
            "assignments": [{"task_id": "mcp-task-001", "owner": "codex"}],
            "dependencies": [{"task_id": "mcp-task-001", "depends_on": []}],
            "results": [],
        },
    )
    runner = CodexSupervisedCommanderRun(tmp_path, "mcp-supervisor-001")
    runner.create()

    adapter = McpRuntimeAdapter(
        {McpToolName.STATUS: lambda _arguments: {"data": runner.status().to_dict()}}
    )
    result = adapter.invoke(
        {
            "request_id": "req-007",
            "tool": "status",
            "arguments": {"run_id": "mcp-supervisor-001"},
        }
    )

    assert result.status is McpResultStatus.OK
    assert result.data["run_id"] == "mcp-supervisor-001"
    assert result.data["next_action"] == "advance"


def test_supervisor_mcp_binding_exposes_only_existing_operations(tmp_path: Path):
    create_plan(
        tmp_path,
        {
            "run_id": "mcp-binding-001",
            "objective": "exercise the existing supervisor binding",
            "base_revision": "abc123",
            "tasks": [
                {
                    "task_id": "mcp-task-001",
                    "owner": "codex",
                    "ownership": [],
                    "dependencies": [],
                    "assignment": {"owner": "codex"},
                }
            ],
            "ownership": [{"task_id": "mcp-task-001", "paths": []}],
            "assignments": [{"task_id": "mcp-task-001", "owner": "codex"}],
            "dependencies": [{"task_id": "mcp-task-001", "depends_on": []}],
            "results": [],
        },
    )
    binding = SupervisorMcpBinding(
        tmp_path,
        "mcp-binding-001",
        authorize=lambda _request: True,
    )

    assert binding.adapter.connected_tools == (
        McpToolName.STATUS,
        McpToolName.RUN,
        McpToolName.REVIEW,
        McpToolName.REWORK,
        McpToolName.INTEGRATE,
        McpToolName.RESUME,
        McpToolName.ARTIFACT_SUMMARY,
    )
    status = binding.invoke(
        {
            "request_id": "req-008",
            "tool": "status",
            "arguments": {"run_id": "mcp-binding-001"},
        }
    )
    assert status.status is McpResultStatus.OK

    proposal = binding.invoke(
        {"request_id": "req-009", "tool": "plan_validate", "arguments": {}}
    )
    assert proposal.status is McpResultStatus.REJECTED
    assert proposal.error_code == "ADAPTER_NOT_CONNECTED"
