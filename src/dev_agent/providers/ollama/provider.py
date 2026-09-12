"""Ollama /api/chat adapter using only the standard library HTTP client."""

from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse, ToolCall
from ..base import ModelProvider, ProviderError
from ..openai_compatible.http import _read_bounded


class OllamaProvider(ModelProvider):
    provider_id = "ollama"

    def __init__(self, *, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 30.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _payload(self, request: ModelRequest) -> dict:
        messages = list(request.messages)
        for result in request.tool_results:
            messages.append({"role": "tool", "tool_name": result.tool_name, "content": json.dumps({"call_id": result.call_id, "status": result.status.value, "result": result.structured_result, "error": result.error}, ensure_ascii=False)})
        # ``num_predict`` is Ollama's runtime output bound.  Keeping this
        # mapping here (rather than relying on model defaults) makes the
        # kernel's finite execution limits effective for a real local model.
        return {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": request.max_output_tokens},
            "tools": [{"type": "function", "function": definition} for definition in request.tool_definitions],
        }

    def request(self, request: ModelRequest) -> ModelResponse:
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = Request(f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(_read_bounded(response).decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"ollama transport failed: {exc}", category="transport", retryable=True) from exc
        try:
            message = raw["message"]
            calls = []
            for item in message.get("tool_calls", []):
                function = item["function"]
                calls.append(ToolCall(tool_name=function["name"], arguments=function.get("arguments", {}), provider_call_id=item.get("id"), originating_request_id=request.request_id))
            text = message.get("content", "")
            usage = {key: raw[key] for key in ("prompt_eval_count", "eval_count", "total_duration") if key in raw}
            # Ollama is a local, non-billed resource in the Phase 6 router.
            # Emit an explicit zero charge so the budget lifecycle can
            # reconcile the dispatch instead of treating absent billing data
            # as an ambiguous paid outcome.
            usage["cost_minor"] = 0
            return ModelResponse(provider=self.provider_id, model=raw.get("model", self.model), finish_reason=raw.get("done_reason", "stop"), text_segments=[text] if text else [], tool_calls=calls, usage=usage)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"ollama response decode failed: {exc}", category="provider_decode", retryable=False) from exc
