"""Small observed-contract probe suite for any ModelProvider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain.protocol import ModelRequest, ToolResult
from .base import ModelProvider


@dataclass
class ContractReport:
    provider_id: str
    model: str | None = None
    adapter_version: str = "v2"
    tested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None
    confidence: str = "observed"
    api_version: str | None = None
    known_quirks: list[str] = field(default_factory=list)
    capabilities: set[str] = field(default_factory=set)
    errors: list[dict[str, str]] = field(default_factory=list)


class ContractHarness:
    def probe(self, provider: ModelProvider) -> ContractReport:
        report = ContractReport(provider_id=provider.provider_id, model=getattr(provider, "model", None))
        cases = (
            ("text", ModelRequest(task_id=_task_id(), messages=[{"role": "user", "content": "reply briefly"}])),
            ("tool_call", ModelRequest(task_id=_task_id(), messages=[{"role": "user", "content": "call echo"}], allowed_tools=["echo"])),
        )
        for name, request in cases:
            try:
                response = provider.request(request)
                if name == "text" and (response.text_segments or response.parts):
                    report.capabilities.add("text")
                if name == "tool_call" and response.tool_calls:
                    report.capabilities.add("tool_call")
                if not response.text_segments and not response.parts and not response.tool_calls:
                    report.errors.append({"case": name, "category": "empty_response", "message": "provider returned no content"})
            except Exception as exc:
                report.errors.append({"case": name, "category": getattr(exc, "category", type(exc).__name__), "message": str(exc)})
        return report

    def probe_cases(self, provider: ModelProvider, cases: tuple[tuple[str, ModelRequest], ...]) -> ContractReport:
        report = ContractReport(provider_id=provider.provider_id, model=getattr(provider, "model", None))
        for name, request in cases:
            try:
                response = provider.request(request)
                if response.tool_calls:
                    report.capabilities.add(f"{name}:tool_call")
                if response.text_segments or response.parts:
                    report.capabilities.add(f"{name}:text")
            except Exception as exc:
                report.errors.append({"case": name, "category": getattr(exc, "category", type(exc).__name__), "message": str(exc)})
        return report

    def probe_tool_roundtrip(self, provider: ModelProvider) -> ContractReport:
        """Exercise model-generated ToolCalls followed by normalized results."""
        report = ContractReport(provider_id=provider.provider_id, model=getattr(provider, "model", None))
        request = ModelRequest(
            task_id=_task_id(),
            messages=[{"role": "user", "content": "call echo twice, then summarize"}],
            allowed_tools=["echo"],
        )
        try:
            response = provider.request(request)
            if not response.tool_calls:
                report.errors.append({"case": "tool_roundtrip", "category": "missing_tool_call", "message": "provider did not select a ToolCall"})
                return report
            if len(response.tool_calls) > 1:
                report.capabilities.add("sequential_tool_call")
            results = [
                ToolResult(call_id=call.call_id, tool_name=call.tool_name, structured_result={"ok": True})
                for call in response.tool_calls
            ]
            follow_up = ModelRequest(
                task_id=request.task_id,
                messages=request.messages,
                allowed_tools=request.allowed_tools,
                tool_results=results,
            )
            final = provider.request(follow_up)
            if final.text_segments or final.parts:
                report.capabilities.add("tool_result_roundtrip")
            else:
                report.errors.append({"case": "tool_roundtrip", "category": "missing_final_response", "message": "provider did not return a final response after ToolResults"})
        except Exception as exc:
            report.errors.append({"case": "tool_roundtrip", "category": getattr(exc, "category", type(exc).__name__), "message": str(exc)})
        return report


def _task_id() -> str:
    from uuid import uuid4

    return str(uuid4())
