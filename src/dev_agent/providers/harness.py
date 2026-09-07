"""Small observed-contract probe suite for any ModelProvider."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.protocol import ModelRequest
from .base import ModelProvider


@dataclass
class ContractReport:
    provider_id: str
    capabilities: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


class ContractHarness:
    def probe(self, provider: ModelProvider) -> ContractReport:
        report = ContractReport(provider_id=provider.provider_id)
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
                    report.errors.append(f"{name}: empty response")
            except Exception as exc:
                report.errors.append(f"{name}: {exc}")
        return report


def _task_id() -> str:
    from uuid import uuid4

    return str(uuid4())
