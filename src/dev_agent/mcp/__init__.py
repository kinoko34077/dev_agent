"""Unconnected, schema-only MCP operation contracts.

The package intentionally contains no transport, server, scheduler, budget,
or authority implementation.  Runtime wiring remains an explicit later gate
over the existing Operation, Commander, and Supervisor APIs.
"""

from .contracts import (
    MCP_TOOL_SPECS,
    McpApprovalRequirement,
    McpResultStatus,
    McpToolName,
    McpToolRequest,
    McpToolResult,
    McpToolSpec,
    UnknownOutcomePolicy,
)

__all__ = [
    "MCP_TOOL_SPECS",
    "McpApprovalRequirement",
    "McpResultStatus",
    "McpToolName",
    "McpToolRequest",
    "McpToolResult",
    "McpToolSpec",
    "UnknownOutcomePolicy",
]
