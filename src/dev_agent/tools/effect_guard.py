"""Durable effect-intent and reconciliation helpers for ToolRuntime."""

from __future__ import annotations

from typing import Any

from ..domain.protocol import ToolCall, ToolResult, ToolResultStatus


class EffectGuard:
    """Keep ambiguous side effects out of automatic retry paths."""

    def __init__(self, result_store: Any | None = None) -> None:
        self.result_store = result_store

    @staticmethod
    def reconciliation_result(call: ToolCall, *, cause: str, message: str, status: ToolResultStatus = ToolResultStatus.FAILED) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            status=status,
            error={"category": "reconciliation_required", "cause": cause, "message": message},
        )

    def mark_unknown(self, call: ToolCall, *, cause: str, message: str, status: ToolResultStatus = ToolResultStatus.FAILED) -> ToolResult:
        # Only a claimed intent can be moved to unknown. Validation and
        # policy failures happen before dispatch and remain ordinary failures.
        if self.result_store is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": message},
            )
        intent = self.result_store.get_effect_intent(call.idempotency_key)
        if intent is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolResultStatus.FAILED,
                error={"category": "tool_execution", "message": message},
            )
        if intent.get("status") in {"succeeded", "confirmed_failed", "reconciled"}:
            result_payload = intent.get("result")
            if isinstance(result_payload, dict) and "call_id" in result_payload and "status" in result_payload:
                try:
                    return ToolResult.from_dict(result_payload)
                except Exception:
                    pass
            return self.reconciliation_result(
                call,
                cause="terminal_effect_without_result",
                message="effect is terminal but its normalized result cannot be decoded",
            )
        self.result_store.mark_effect_unknown(call.idempotency_key, reason=f"{cause}: {message}")
        return self.reconciliation_result(call, cause=cause, message=message, status=status)


__all__ = ["EffectGuard"]
