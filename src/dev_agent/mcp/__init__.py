"""Transport-neutral MCP contracts and a thin in-process adapter.

The package contains no transport, server, scheduler, budget, or authority
implementation.  ``McpRuntimeAdapter`` only delegates to handlers supplied by
the existing Operation, Commander, and Supervisor authorities.
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
from .runtime import (
    McpAuthorizer,
    McpFailed,
    McpHandler,
    McpRejected,
    McpRuntimeAdapter,
    McpUnknownOutcome,
)
from .human import (
    CodexExpertProposal,
    CodexMcpEnvelope,
    CodexMcpExpertAdapter,
    CodexMcpHumanAdapter,
    CodexMcpProtocolError,
    McpJsonLineTransport,
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
    "McpAuthorizer",
    "McpFailed",
    "McpHandler",
    "McpRejected",
    "McpRuntimeAdapter",
    "McpUnknownOutcome",
    "CodexExpertProposal",
    "CodexMcpEnvelope",
    "CodexMcpExpertAdapter",
    "CodexMcpHumanAdapter",
    "CodexMcpProtocolError",
    "McpJsonLineTransport",
]
