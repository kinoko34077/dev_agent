"""Thin, transport-free MCP runtime adapter.

The adapter translates the existing bounded MCP request/result envelopes to
caller-owned handlers.  It does not implement scheduling, retries, budget,
approval, reconciliation, or integration authority.  Those responsibilities
remain in the handler composition supplied by the existing Control Plane.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from time import monotonic
from typing import Any

from ..security.audit import AuditRecorder
from .contracts import (
    MCP_TOOL_SPECS,
    McpApprovalRequirement,
    McpResultStatus,
    McpToolName,
    McpToolRequest,
    McpToolResult,
)


_ERROR_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class McpRejected(RuntimeError):
    """A handler rejected bounded input or an existing authority check."""

    def __init__(self, error_code: str = "REQUEST_REJECTED") -> None:
        super().__init__(error_code)
        self.error_code = error_code


class McpFailed(RuntimeError):
    """A known handler failure without an ambiguous external effect."""

    def __init__(self, error_code: str = "HANDLER_FAILED") -> None:
        super().__init__(error_code)
        self.error_code = error_code


class McpUnknownOutcome(RuntimeError):
    """The existing handler cannot confirm whether an external effect landed."""

    def __init__(self, error_code: str = "EXTERNAL_OUTCOME_UNKNOWN") -> None:
        super().__init__(error_code)
        self.error_code = error_code


McpHandler = Callable[[Mapping[str, Any]], McpToolResult | Mapping[str, Any]]
McpAuthorizer = Callable[[McpToolRequest], bool]


def _error_code(value: Any, fallback: str) -> str:
    if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
        return value
    return fallback


def _safe_artifact_refs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("artifact_refs must be a sequence")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("artifact_refs must contain strings")
        sanitized = AuditRecorder.sanitize_payload({"reference": item})["reference"]
        if isinstance(sanitized, str):
            result.append(sanitized)
    return tuple(result)


class McpRuntimeAdapter:
    """Bind selected MCP tools to existing authority-owned handlers.

    This is intentionally an in-process boundary.  A future MCP transport can
    call :meth:`invoke` without adding another state machine or authority
    layer.  A missing handler is a bounded rejection, never a best-effort
    fallback.
    """

    def __init__(
        self,
        handlers: Mapping[McpToolName | str, McpHandler],
        *,
        authorize: McpAuthorizer | None = None,
    ) -> None:
        if not isinstance(handlers, Mapping):
            raise TypeError("handlers must be a mapping")
        normalized: dict[McpToolName, McpHandler] = {}
        for name, handler in handlers.items():
            try:
                tool = name if isinstance(name, McpToolName) else McpToolName(name)
            except (TypeError, ValueError) as exc:
                raise ValueError("handlers contain an unknown MCP tool") from exc
            if not callable(handler):
                raise TypeError(f"handler for {tool.value} must be callable")
            normalized[tool] = handler
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable")
        self._handlers = normalized
        self._authorize = authorize

    @property
    def connected_tools(self) -> tuple[McpToolName, ...]:
        return tuple(tool for tool in MCP_TOOL_SPECS if tool in self._handlers)

    def invoke(self, request: McpToolRequest | Mapping[str, Any]) -> McpToolResult:
        if not isinstance(request, McpToolRequest):
            request = McpToolRequest.from_dict(request)
        tool = request.tool
        spec = MCP_TOOL_SPECS[tool]
        handler = self._handlers.get(tool)
        if handler is None:
            return self._result(request, McpResultStatus.REJECTED, "ADAPTER_NOT_CONNECTED")
        if spec.approval is not McpApprovalRequirement.NONE:
            if self._authorize is None:
                return self._result(request, McpResultStatus.REJECTED, "APPROVAL_REQUIRED")
            try:
                approved = self._authorize(request)
            except Exception:
                return self._result(request, McpResultStatus.REJECTED, "AUTHORITY_UNAVAILABLE")
            if approved is not True:
                return self._result(request, McpResultStatus.REJECTED, "APPROVAL_REQUIRED")

        started = monotonic()
        try:
            value = handler(request.arguments)
        except McpUnknownOutcome as exc:
            return self._result(
                request,
                McpResultStatus.UNKNOWN,
                _error_code(exc.error_code, "EXTERNAL_OUTCOME_UNKNOWN"),
            )
        except TimeoutError:
            return self._timeout_result(request, spec.unknown_outcome.value == "reconcile")
        except McpRejected as exc:
            return self._result(request, McpResultStatus.REJECTED, _error_code(exc.error_code, "REQUEST_REJECTED"))
        except McpFailed as exc:
            return self._result(request, McpResultStatus.FAILED, _error_code(exc.error_code, "HANDLER_FAILED"))
        except Exception:
            # Do not expose handler messages, which may contain provider
            # responses or credentials.  The existing authority remains the
            # source of any detailed evidence artifact.
            return self._result(request, McpResultStatus.FAILED, "HANDLER_ERROR")

        if monotonic() - started > float(request.timeout_seconds):
            return self._timeout_result(request, spec.unknown_outcome.value == "reconcile")
        try:
            return self._coerce_result(request, value)
        except (TypeError, ValueError):
            return self._result(request, McpResultStatus.FAILED, "INVALID_HANDLER_RESULT")

    def invoke_dict(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Convenience boundary for a JSON transport without adding one."""

        return self.invoke(value).to_dict()

    def _timeout_result(self, request: McpToolRequest, reconcile: bool) -> McpToolResult:
        status = McpResultStatus.UNKNOWN if reconcile else McpResultStatus.FAILED
        return self._result(request, status, "MCP_TIMEOUT")

    def _result(self, request: McpToolRequest, status: McpResultStatus, error_code: str | None) -> McpToolResult:
        return McpToolResult(
            request_id=request.request_id,
            tool=request.tool,
            status=status,
            error_code=error_code,
        )

    def _coerce_result(self, request: McpToolRequest, value: McpToolResult | Mapping[str, Any]) -> McpToolResult:
        if isinstance(value, McpToolResult):
            if value.request_id != request.request_id or value.tool is not request.tool:
                raise ValueError("handler result identity does not match request")
            data = AuditRecorder.sanitize_payload(dict(value.data))
            return McpToolResult(
                request_id=request.request_id,
                tool=request.tool,
                status=value.status,
                data=data,
                error_code=value.error_code,
                artifact_refs=_safe_artifact_refs(value.artifact_refs),
            )
        if not isinstance(value, Mapping):
            raise TypeError("handler result must be an object")
        known = {"status", "data", "error_code", "artifact_refs"}
        status = value.get("status", McpResultStatus.OK.value)
        data = value.get("data")
        if data is None:
            data = {key: item for key, item in value.items() if key not in known}
        if not isinstance(data, Mapping):
            raise TypeError("handler result data must be an object")
        try:
            normalized_status = status if isinstance(status, McpResultStatus) else McpResultStatus(status)
        except (TypeError, ValueError) as exc:
            raise ValueError("handler result status is invalid") from exc
        safe_data = AuditRecorder.sanitize_payload(dict(data))
        return McpToolResult(
            request_id=request.request_id,
            tool=request.tool,
            status=normalized_status,
            data=safe_data,
            error_code=_error_code(value.get("error_code"), "HANDLER_ERROR") if value.get("error_code") is not None else None,
            artifact_refs=_safe_artifact_refs(value.get("artifact_refs")),
        )


__all__ = [
    "McpAuthorizer",
    "McpFailed",
    "McpHandler",
    "McpRejected",
    "McpRuntimeAdapter",
    "McpUnknownOutcome",
]
