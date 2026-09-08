"""Execute only registered, schema-checked tools."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus
from ..policy.approvals import ApprovalPolicy, canonical_arguments_hash
from ..policy.permissions import PathPolicy
from .registry import ToolRegistry
from .schema import SchemaValidationError, validate


class ToolRuntime:
    IDEMPOTENCY_REQUIRED = frozenset({"local_write", "process", "network_read", "external_write", "financial", "credential", "destructive"})
    EXTERNAL_GUARDED = frozenset({"external_write", "financial", "credential", "destructive"})

    def __init__(self, registry: ToolRegistry, *, approvals: ApprovalPolicy | None = None, paths: PathPolicy | None = None) -> None:
        self.registry = registry
        self.result_store = None
        self.approvals = approvals or ApprovalPolicy()
        self.paths = paths

    def with_result_store(self, result_store):
        """Attach a durable idempotency store without coupling registry to state."""
        self.result_store = result_store
        return self

    def execute(self, call: ToolCall, *, approval_id: str | None = None, task_id: str | None = None) -> ToolResult:
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
            if spec.input_schema:
                validate(call.arguments, spec.input_schema)
        except SchemaValidationError as exc:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.FAILED, error={"category": "schema_validation", "message": str(exc)})
        try:
            arguments = dict(call.arguments)
            if spec.path_argument:
                if self.paths is None or not spec.path_operation or spec.path_argument not in arguments:
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": "path policy is required"})
                arguments[spec.path_argument] = str(self.paths.require(arguments[spec.path_argument], spec.path_operation))
            if spec.side_effect_level in self.EXTERNAL_GUARDED:
                intent = self.result_store.get_effect_intent(call.idempotency_key)
                if intent is not None:
                    if intent["status"] == "succeeded" and intent.get("result"):
                        return ToolResult.from_dict(intent["result"])
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "reconciliation_required", "message": "external effect intent is pending; reconcile before retry"})
            if self.approvals.requires_approval(spec.side_effect_level) and not self.approvals.authorize(spec.side_effect_level, approval_id=approval_id, task_id=task_id or call.originating_request_id, call_id=call.call_id, arguments_hash=canonical_arguments_hash(call.arguments), store=self.result_store):
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "approval_required", "message": "human approval is required"})
            if spec.side_effect_level in self.EXTERNAL_GUARDED:
                if not self.result_store.create_effect_intent(call.idempotency_key, task_id=task_id or "unknown", tool_name=call.tool_name, arguments=arguments):
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "reconciliation_required", "message": "external effect claim lost; reconcile before retry"})
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(spec.handler, arguments)
            try:
                value = future.result(timeout=spec.timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                if spec.side_effect_level in self.EXTERNAL_GUARDED:
                    self.result_store.mark_effect_unknown(call.idempotency_key, reason="handler timeout after dispatch")
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.TIMEOUT, error={"category": "timeout", "message": "tool execution timed out"})
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if not isinstance(value, dict):
                raise TypeError("tool handler must return a dict")
            if spec.output_schema:
                try:
                    validate(value, spec.output_schema)
                except SchemaValidationError as exc:
                    if spec.side_effect_level in self.EXTERNAL_GUARDED:
                        self.result_store.mark_effect_unknown(call.idempotency_key, reason="invalid output after dispatch")
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.FAILED, error={"category": "schema_validation", "message": str(exc)})
            result = ToolResult(call_id=call.call_id, tool_name=call.tool_name, structured_result=value)
            if spec.side_effect_level in self.EXTERNAL_GUARDED:
                self.result_store.complete_effect_intent(call.idempotency_key, result)
            if call.idempotency_key and self.result_store is not None:
                self.result_store.save_idempotent(call.idempotency_key, result)
            return result
        except PermissionError as exc:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": str(exc)})
        except Exception as exc:  # tool failures become data, not uncontrolled runtime errors
            if spec.side_effect_level in self.EXTERNAL_GUARDED and call.idempotency_key and self.result_store is not None:
                self.result_store.mark_effect_unknown(call.idempotency_key, reason=f"handler exception: {type(exc).__name__}")
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "reconciliation_required", "message": "external effect outcome is unknown; reconcile before retry"})
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": str(exc)},
            )
