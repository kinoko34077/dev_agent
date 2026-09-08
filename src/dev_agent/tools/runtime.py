"""Execute registered tools with explicit policy and execution boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from typing import Any

from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus
from ..policy.approvals import ApprovalPolicy, canonical_arguments_hash
from ..policy.permissions import PathPolicy
from .registry import ToolRegistry, ToolSpec
from .schema import SchemaValidationError, validate


class _ToolTimedOut(Exception):
    pass


class _ToolCancelled(Exception):
    pass


class _ToolResponseDecodeError(Exception):
    pass


class _ToolProcessError(Exception):
    pass


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _handler_reference(spec: ToolSpec) -> str | None:
    if spec.handler_ref:
        return spec.handler_ref
    module = getattr(spec.handler, "__module__", None)
    qualname = getattr(spec.handler, "__qualname__", None)
    if not module or not qualname or "<locals>" in qualname:
        return None
    return f"{module}:{qualname}"


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the worker and descendants, then leave reaping to caller."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP alone does not guarantee descendant
        # termination on Windows; taskkill's /T closes the whole tree.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        process.kill()


def _run_subprocess(handler_ref: str, arguments: dict[str, Any], timeout_seconds: float, cancel_event: Event | None) -> dict[str, Any]:
    worker = Path(__file__).with_name("process_worker.py")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, str(worker), handler_ref],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path.cwd()),
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    result_box: dict[str, Any] = {}

    def communicate() -> None:
        try:
            result_box["output"] = process.communicate(input=payload)
        except BaseException as exc:  # pragma: no cover - OS-level failures vary
            result_box["exception"] = exc
        finally:
            # Record when the worker actually finished.  On a busy CI runner
            # the reader thread can observe a short-lived worker just after
            # the parent deadline; classifying that late completion as a
            # generic process failure would violate the hard timeout contract.
            result_box["finished_at"] = monotonic()

    reader = Thread(target=communicate, name="dev-agent-tool-worker-io", daemon=True)
    reader.start()
    deadline = monotonic() + timeout_seconds
    while reader.is_alive():
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process_tree(process)
            reader.join(timeout=2.0)
            raise _ToolCancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            reader.join(timeout=2.0)
            raise _ToolTimedOut()
        reader.join(timeout=min(0.05, remaining))
    reader.join()
    if result_box.get("finished_at", monotonic()) > deadline:
        raise _ToolTimedOut()
    if "exception" in result_box:
        raise _ToolProcessError("isolated tool process communication failed") from result_box["exception"]
    stdout, _stderr = result_box.get("output", ("", ""))
    if process.returncode != 0 and not stdout.strip():
        raise _ToolProcessError("isolated tool process failed")
    try:
        packet = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _ToolResponseDecodeError("isolated tool returned malformed JSON") from exc
    if not isinstance(packet, dict) or packet.get("ok") is not True:
        detail = packet.get("error", {}) if isinstance(packet, dict) else {}
        message = detail.get("message", "isolated tool failed") if isinstance(detail, dict) else "isolated tool failed"
        raise _ToolProcessError(str(message))
    value = packet.get("result")
    if not isinstance(value, dict):
        raise _ToolResponseDecodeError("isolated tool result must be an object")
    return value


class ToolRuntime:
    IDEMPOTENCY_REQUIRED = frozenset({"local_write", "process", "network_read", "external_write", "financial", "credential", "destructive"})
    EXTERNAL_GUARDED = frozenset({"external_write", "financial", "credential", "destructive"})
    EFFECT_GUARDED = EXTERNAL_GUARDED | frozenset({"local_write", "process"})

    def __init__(self, registry: ToolRegistry, *, approvals: ApprovalPolicy | None = None, paths: PathPolicy | None = None) -> None:
        self.registry = registry
        self.result_store = None
        self.approvals = approvals or ApprovalPolicy()
        self.paths = paths

    def with_result_store(self, result_store):
        """Compatibility alias for the immutable ``bound_to`` binding."""
        return self.bound_to(result_store)

    def bound_to(self, result_store) -> "ToolRuntime":
        """Return a new runtime bound to one durable result store."""
        bound = ToolRuntime(self.registry, approvals=self.approvals, paths=self.paths)
        bound.result_store = result_store
        return bound

    def effective_arguments(self, call: ToolCall) -> dict[str, Any]:
        """Return the deterministic argument payload used for tool identity.

        This is deliberately policy-neutral: permission checks still happen in
        ``execute``.  Identity, approval, and dispatch must nevertheless agree
        on path canonicalization, otherwise equivalent relative and absolute
        paths could receive different operation keys.
        """
        spec = self.registry.resolve(call.tool_name)
        arguments = dict(call.arguments)
        if spec is not None and spec.path_argument and self.paths is not None and spec.path_argument in arguments:
            arguments[spec.path_argument] = str(self.paths.canonical(arguments[spec.path_argument]))
        return arguments

    @staticmethod
    def _reconciliation_result(call: ToolCall, *, cause: str, message: str, status: ToolResultStatus = ToolResultStatus.FAILED) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            status=status,
            error={"category": "reconciliation_required", "cause": cause, "message": message},
        )

    def _mark_unknown(self, call: ToolCall, *, cause: str, message: str, status: ToolResultStatus = ToolResultStatus.FAILED) -> ToolResult:
        # Only a claimed intent can be moved to unknown.  Validation and
        # policy failures happen before dispatch and must remain ordinary
        # failures rather than producing a reconciliation record with no
        # external operation behind it.
        intent = self.result_store.get_effect_intent(call.idempotency_key)
        if intent is None:
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.FAILED, error={"category": "tool_execution", "message": message})
        if intent.get("status") in {"succeeded", "confirmed_failed", "reconciled"}:
            result_payload = intent.get("result")
            if isinstance(result_payload, dict) and "call_id" in result_payload and "status" in result_payload:
                try:
                    return ToolResult.from_dict(result_payload)
                except Exception:
                    pass
            return self._reconciliation_result(call, cause="terminal_effect_without_result", message="effect is terminal but its normalized result cannot be decoded")
        self.result_store.mark_effect_unknown(call.idempotency_key, reason=f"{cause}: {message}")
        return self._reconciliation_result(call, cause=cause, message=message, status=status)

    @staticmethod
    def _run_in_process(handler, arguments: dict[str, Any], timeout_seconds: float, cancel_event: Event | None) -> dict[str, Any]:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(handler, arguments)
        deadline = monotonic() + timeout_seconds
        try:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    future.cancel()  # best effort only; running Python threads cannot be killed
                    raise _ToolTimedOut()
                try:
                    value = future.result(timeout=min(0.05, remaining))
                    return value
                except FutureTimeoutError:
                    if cancel_event is not None and cancel_event.is_set():
                        future.cancel()
                        raise _ToolCancelled()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def execute(self, call: ToolCall, *, approval_id: str | None = None, task_id: str | None = None, cancel_event: Event | None = None) -> ToolResult:
        if cancel_event is not None and cancel_event.is_set():
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.CANCELLED, error={"category": "cancelled", "message": "tool execution was cancelled before dispatch"})
        previous = None
        if call.idempotency_key and self.result_store is not None:
            previous = self.result_store.get_idempotent(call.idempotency_key)
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
        effect_dispatched = False
        try:
            arguments = self.effective_arguments(call)
            if _json_size(arguments) > spec.max_argument_bytes:
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "limits_exceeded", "message": "tool arguments exceed configured byte limit"})
            if spec.path_argument:
                if self.paths is None or not spec.path_operation or spec.path_argument not in arguments:
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": "path policy is required"})
                arguments[spec.path_argument] = str(self.paths.require(arguments[spec.path_argument], spec.path_operation))
            # Canonicalization can expand a relative path.  The effective
            # arguments, not only the provider-supplied shape, are bounded.
            if _json_size(arguments) > spec.max_argument_bytes:
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "limits_exceeded", "message": "effective tool arguments exceed configured byte limit"})
            if previous is not None and previous.tool_name != call.tool_name:
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    status=ToolResultStatus.DENIED,
                    error={"category": "idempotency_conflict", "message": "idempotency key is bound to a different tool"},
                )
            if spec.side_effect_level in self.EFFECT_GUARDED:
                intent = self.result_store.get_effect_intent(call.idempotency_key)
                if intent is not None:
                    stored_arguments = intent.get("arguments")
                    if (
                        intent.get("tool_name") != call.tool_name
                        or (task_id is not None and intent.get("task_id") not in {task_id, "unknown"})
                        or not isinstance(stored_arguments, dict)
                        or canonical_arguments_hash(stored_arguments) != canonical_arguments_hash(arguments)
                    ):
                        return ToolResult(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            status=ToolResultStatus.DENIED,
                            error={"category": "idempotency_conflict", "message": "idempotency key is bound to a different side-effect operation"},
                        )
                    if intent["status"] in {"succeeded", "reconciled"} and intent.get("result"):
                        result_payload = intent["result"]
                        if isinstance(result_payload, dict) and "call_id" in result_payload and "status" in result_payload:
                            return ToolResult.from_dict(result_payload)
                        return self._reconciliation_result(call, cause="missing_normalized_result", message="external effect is confirmed but no normalized local result is available")
                    if intent["status"] == "confirmed_failed":
                        return ToolResult(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            status=ToolResultStatus.FAILED,
                            error={"category": "effect_confirmed_failed", "message": "external effect was reconciled as confirmed failure; retry is prohibited"},
                        )
                    return self._reconciliation_result(call, cause="pending_effect", message="external effect intent is pending; reconcile before retry", status=ToolResultStatus.DENIED)
                if previous is not None:
                    return ToolResult(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        status=ToolResultStatus.DENIED,
                        error={"category": "idempotency_conflict", "message": "idempotent result has no corresponding effect intent"},
                    )
            elif previous is not None:
                return previous
            if spec.requires_subprocess and _handler_reference(spec) is None:
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "isolation_required", "message": "isolated tools require an importable top-level handler"})
            if self.approvals.requires_approval(spec.side_effect_level) and not self.approvals.authorize(spec.side_effect_level, approval_id=approval_id, task_id=task_id or call.originating_request_id, call_id=call.call_id, arguments_hash=canonical_arguments_hash(arguments), store=self.result_store):
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "approval_required", "message": "human approval is required"})
            if spec.side_effect_level in self.EFFECT_GUARDED:
                if not self.result_store.create_effect_intent(call.idempotency_key, task_id=task_id or "unknown", tool_name=call.tool_name, arguments=arguments):
                    return self._reconciliation_result(call, cause="claim_lost", message="external effect claim lost; reconcile before retry", status=ToolResultStatus.DENIED)
                self.result_store.transition_effect_intent(call.idempotency_key, to_status="dispatching")
                effect_dispatched = True
            if spec.requires_subprocess:
                value = _run_subprocess(_handler_reference(spec), arguments, spec.timeout_seconds, cancel_event)  # type: ignore[arg-type]
            else:
                value = self._run_in_process(spec.handler, arguments, spec.timeout_seconds, cancel_event)
            if not isinstance(value, dict):
                raise _ToolResponseDecodeError("tool handler must return a dict")
            if _json_size(value) > spec.max_result_bytes:
                if spec.side_effect_level in self.EFFECT_GUARDED:
                    return self._mark_unknown(call, cause="result_limit", message="side-effect result exceeds configured byte limit")
                return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.FAILED, error={"category": "limits_exceeded", "message": "tool result exceeds configured byte limit"})
            if spec.output_schema:
                try:
                    validate(value, spec.output_schema)
                except SchemaValidationError as exc:
                    if spec.side_effect_level in self.EFFECT_GUARDED:
                        return self._mark_unknown(call, cause="schema_validation", message=str(exc))
                    return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.FAILED, error={"category": "schema_validation", "message": str(exc)})
            result = ToolResult(call_id=call.call_id, tool_name=call.tool_name, provider_call_id=call.provider_call_id, structured_result=value)
            if spec.side_effect_level in self.EFFECT_GUARDED:
                self.result_store.complete_effect_intent(call.idempotency_key, result)
            if call.idempotency_key and self.result_store is not None:
                self.result_store.save_idempotent(call.idempotency_key, result)
            return result
        except _ToolTimedOut:
            if spec.side_effect_level in self.EFFECT_GUARDED:
                return self._mark_unknown(call, cause="timeout", message="tool execution timed out", status=ToolResultStatus.TIMEOUT)
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.TIMEOUT, error={"category": "timeout", "message": "tool execution timed out"})
        except _ToolCancelled:
            if spec.side_effect_level in self.EFFECT_GUARDED:
                return self._mark_unknown(call, cause="cancelled", message="side-effect execution was cancelled after dispatch", status=ToolResultStatus.DENIED)
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.CANCELLED, error={"category": "cancelled", "message": "tool execution was cancelled"})
        except PermissionError as exc:
            if effect_dispatched and spec.side_effect_level in self.EFFECT_GUARDED and call.idempotency_key and self.result_store is not None:
                return self._mark_unknown(call, cause="handler_exception", message="external effect outcome is unknown; reconcile before retry")
            return ToolResult(call_id=call.call_id, tool_name=call.tool_name, status=ToolResultStatus.DENIED, error={"category": "policy_denied", "message": str(exc)})
        except Exception as exc:  # tool failures become data, not uncontrolled runtime errors
            if spec.side_effect_level in self.EFFECT_GUARDED and call.idempotency_key and self.result_store is not None:
                intent = self.result_store.get_effect_intent(call.idempotency_key)
                if intent is not None and intent.get("status") in {"succeeded", "confirmed_failed", "reconciled"}:
                    return self._mark_unknown(call, cause="terminal_persistence", message="effect completed but local result persistence needs reconciliation")
                cause = "response_decode" if isinstance(exc, (_ToolResponseDecodeError, _ToolProcessError, json.JSONDecodeError, TypeError)) else "connection_error" if isinstance(exc, (ConnectionError, TimeoutError)) else "handler_exception"
                return self._mark_unknown(call, cause=cause, message="external effect outcome is unknown; reconcile before retry")
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": str(exc)},
            )
