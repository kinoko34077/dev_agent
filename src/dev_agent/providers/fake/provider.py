"""A deterministic provider that requests one harmless tool, then finishes."""

from __future__ import annotations

from ...domain.protocol import ModelRequest, ModelResponse, ToolCall
from ..base import ModelProvider


class FakeProvider(ModelProvider):
    provider_id = "fake"

    def __init__(self, *, tool_name: str = "echo", tool_arguments: dict | None = None) -> None:
        self.tool_name = tool_name
        self.tool_arguments = dict(tool_arguments or {"value": "ok"})
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            call = ToolCall(
                tool_name=self.tool_name,
                arguments=self.tool_arguments,
                originating_request_id=request.request_id,
            )
            return ModelResponse(
                provider=self.provider_id,
                model="deterministic",
                finish_reason="tool_call",
                tool_calls=[call],
            )
        return ModelResponse(
            provider=self.provider_id,
            model="deterministic",
            finish_reason="stop",
            text_segments=["fake task completed"],
        )
