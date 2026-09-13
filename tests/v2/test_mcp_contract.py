from __future__ import annotations

import pytest

from src.dev_agent.mcp.contracts import (
    MCP_TOOL_SPECS,
    McpApprovalRequirement,
    McpResultStatus,
    McpToolName,
    McpToolRequest,
    McpToolResult,
    UnknownOutcomePolicy,
)


def test_mcp_catalog_is_a_small_unique_contract_without_an_adapter():
    assert tuple(MCP_TOOL_SPECS) == (
        McpToolName.STATUS,
        McpToolName.PLAN_PROPOSE,
        McpToolName.PLAN_VALIDATE,
        McpToolName.PLAN_APPLY,
        McpToolName.RUN,
        McpToolName.REVIEW,
        McpToolName.REWORK,
        McpToolName.INTEGRATE,
        McpToolName.RESUME,
        McpToolName.ARTIFACT_SUMMARY,
    )
    assert len(set(MCP_TOOL_SPECS)) == len(MCP_TOOL_SPECS)
    assert all(spec.adapter_connected is False for spec in MCP_TOOL_SPECS.values())


def test_mcp_request_round_trips_json_safe_bounded_arguments():
    request = McpToolRequest(
        request_id="req-001",
        tool=McpToolName.REVIEW,
        arguments={"run_id": "run-001", "task_id": "task-001", "decision": "REWORK"},
        timeout_seconds=30,
    )

    restored = McpToolRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.tool is McpToolName.REVIEW


def test_mcp_request_rejects_unknown_fields_non_json_and_excessive_timeout():
    with pytest.raises(ValueError, match="unknown request field"):
        McpToolRequest.from_dict(
            {
                "request_id": "req-001",
                "tool": "status",
                "arguments": {},
                "untrusted_control": "do something else",
            }
        )

    with pytest.raises(ValueError, match="JSON serializable"):
        McpToolRequest(
            request_id="req-001",
            tool=McpToolName.STATUS,
            arguments={"bad": object()},
        )

    with pytest.raises(ValueError, match="exceeds tool timeout"):
        McpToolRequest(
            request_id="req-001",
            tool=McpToolName.STATUS,
            arguments={},
            timeout_seconds=61,
        )


def test_mcp_specs_keep_approval_and_unknown_effect_boundaries_explicit():
    assert MCP_TOOL_SPECS[McpToolName.STATUS].approval is McpApprovalRequirement.NONE
    assert MCP_TOOL_SPECS[McpToolName.PLAN_PROPOSE].approval is McpApprovalRequirement.NONE
    assert MCP_TOOL_SPECS[McpToolName.PLAN_APPLY].approval is McpApprovalRequirement.EXISTING_AUTHORITY
    assert MCP_TOOL_SPECS[McpToolName.RUN].approval is McpApprovalRequirement.EXPLICIT_OPERATOR
    assert MCP_TOOL_SPECS[McpToolName.INTEGRATE].approval is McpApprovalRequirement.EXISTING_AUTHORITY
    assert MCP_TOOL_SPECS[McpToolName.RUN].unknown_outcome is UnknownOutcomePolicy.RECONCILE
    assert MCP_TOOL_SPECS[McpToolName.INTEGRATE].unknown_outcome is UnknownOutcomePolicy.RECONCILE
    assert MCP_TOOL_SPECS[McpToolName.PLAN_PROPOSE].unknown_outcome is UnknownOutcomePolicy.NO_EXTERNAL_EFFECT


def test_mcp_result_is_reference_first_and_preserves_unknown_status():
    result = McpToolResult(
        request_id="req-002",
        tool=McpToolName.RUN,
        status=McpResultStatus.UNKNOWN,
        data={"run_id": "run-001", "artifact_ref": ".devfarm/results/run-001.json"},
        error_code="EXTERNAL_OUTCOME_UNKNOWN",
        artifact_refs=(".devfarm/results/run-001.json", "https://example.invalid/evidence/1"),
    )

    restored = McpToolResult.from_dict(result.to_dict())

    assert restored == result
    assert restored.status is McpResultStatus.UNKNOWN
    assert restored.error_code == "EXTERNAL_OUTCOME_UNKNOWN"
    assert restored.artifact_refs == result.artifact_refs


def test_mcp_result_rejects_unbounded_or_unsafe_error_payload():
    with pytest.raises(ValueError, match="error_code"):
        McpToolResult(
            request_id="req-003",
            tool=McpToolName.STATUS,
            status=McpResultStatus.FAILED,
            error_code="not a structural code",
        )

    with pytest.raises(ValueError, match="JSON serializable"):
        McpToolResult(
            request_id="req-003",
            tool=McpToolName.STATUS,
            status=McpResultStatus.FAILED,
            data={"bad": object()},
        )
