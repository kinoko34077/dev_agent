"""Execute only registered, schema-checked tools."""

from __future__ import annotations

from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus
from .registry import ToolRegistry


class ToolRuntime:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, call: ToolCall) -> ToolResult:
        spec = self.registry.resolve(call.tool_name)
        if spec is None:
            return ToolResult(
                call_id=call.call_id,
                status=ToolResultStatus.DENIED,
                error={"category": "policy_denied", "message": f"tool is not enabled: {call.tool_name}"},
            )
        missing = sorted(spec.required_arguments - call.arguments.keys())
        if missing:
            return ToolResult(
                call_id=call.call_id,
                status=ToolResultStatus.FAILED,
                error={"category": "schema_validation", "message": f"missing arguments: {', '.join(missing)}"},
            )
        try:
            value = spec.handler(dict(call.arguments))
            if not isinstance(value, dict):
                raise TypeError("tool handler must return a dict")
            return ToolResult(call_id=call.call_id, structured_result=value)
        except Exception as exc:  # tool failures become data, not uncontrolled runtime errors
            return ToolResult(
                call_id=call.call_id,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": str(exc)},
            )
