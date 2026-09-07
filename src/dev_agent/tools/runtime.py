"""Execute only registered, schema-checked tools."""

from __future__ import annotations

from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus
from .registry import ToolRegistry


class ToolRuntime:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.result_store = None

    def with_result_store(self, result_store):
        """Attach a durable idempotency store without coupling registry to state."""
        self.result_store = result_store
        return self

    def execute(self, call: ToolCall) -> ToolResult:
        if call.idempotency_key and self.result_store is not None:
            previous = self.result_store.get_idempotent(call.idempotency_key)
            if previous is not None:
                return previous
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
            result = ToolResult(call_id=call.call_id, structured_result=value)
            if call.idempotency_key and self.result_store is not None:
                self.result_store.save_idempotent(call.idempotency_key, result)
            return result
        except Exception as exc:  # tool failures become data, not uncontrolled runtime errors
            return ToolResult(
                call_id=call.call_id,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": str(exc)},
            )
