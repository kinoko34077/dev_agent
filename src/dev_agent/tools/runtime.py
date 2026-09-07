"""Execute only registered, schema-checked tools."""

from __future__ import annotations

from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus
from ..policy.approvals import ApprovalPolicy
from ..policy.permissions import PathPolicy
from .registry import ToolRegistry


class ToolRuntime:
    IDEMPOTENCY_REQUIRED = frozenset({"local_write", "process", "network_read", "external_write", "financial", "credential", "destructive"})

    def __init__(self, registry: ToolRegistry, *, approvals: ApprovalPolicy | None = None, paths: PathPolicy | None = None) -> None:
        self.registry = registry
        self.result_store = None
        self.approvals = approvals or ApprovalPolicy()
        self.paths = paths

    def with_result_store(self, result_store):
        """Attach a durable idempotency store without coupling registry to state."""
        self.result_store = result_store
        return self

    def execute(self, call: ToolCall, *, approval_granted: bool = False) -> ToolResult:
        if call.idempotency_key and self.result_store is not None:
            previous = self.result_store.get_idempotent(call.idempotency_key)
            if previous is not None:
                return previous
        spec = self.registry.resolve(call.tool_name)
        if spec is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.DENIED,
                error={"category": "policy_denied", "message": f"tool is not enabled: {call.tool_name}"},
            )
        if self.approvals.requires_approval(spec.side_effect_level) and not approval_granted:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "approval_required", "message": "human approval is required"})
        if spec.side_effect_level in self.IDEMPOTENCY_REQUIRED and not call.idempotency_key:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": "idempotency key is required for this side effect"})
        if spec.side_effect_level in self.IDEMPOTENCY_REQUIRED and self.result_store is None:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": "durable result store is required for this side effect"})
        missing = sorted(spec.required_arguments - call.arguments.keys())
        if missing:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "schema_validation", "message": f"missing arguments: {', '.join(missing)}"},
            )
        try:
            arguments = dict(call.arguments)
            if spec.path_argument:
                if self.paths is None or not spec.path_operation or spec.path_argument not in arguments:
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": "path policy is required"})
                arguments[spec.path_argument] = str(self.paths.require(arguments[spec.path_argument], spec.path_operation))
            value = spec.handler(arguments)
            if not isinstance(value, dict):
                raise TypeError("tool handler must return a dict")
            result = ToolResult(call_id=call.call_id, tool_name=call.tool_name, structured_result=value)
            if call.idempotency_key and self.result_store is not None:
                self.result_store.save_idempotent(call.idempotency_key, result)
            return result
        except PermissionError as exc:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": str(exc)})
        except Exception as exc:  # tool failures become data, not uncontrolled runtime errors
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": str(exc)},
            )
