"""Injected-transport Gemini adapter.

The transport may be backed by the Google SDK in a deployment, but the SDK
object is never returned to the Kernel. Offline tests use a plain callable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse
from ..base import ModelProvider, ProviderError
from .decoder import decode_generate_content
from ..normalize import normalize_response


class GeminiProvider(ModelProvider):
    provider_id = "gemini"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "gemini") -> None:
        self.transport = transport
        self.model = model

    def request(self, request: ModelRequest) -> ModelResponse:
        try:
            raw = self.transport(request.to_dict())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"gemini transport failed: {exc}", category="transport", retryable=True) from exc
        return normalize_response(raw, provider=self.provider_id, default_model=self.model)


class GeminiHttpProvider(ModelProvider):
    """Minimal REST adapter for Gemini ``generateContent``."""

    provider_id = "gemini"

    def __init__(self, *, model: str, api_key: str | None = None, base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout_seconds: float = 30.0) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _key(self) -> str:
        key = self.api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("gemini authentication failed: GEMINI_API_KEY is not configured", category="authentication", retryable=False)
        return key

    @staticmethod
    def _contents(request: ModelRequest) -> list[dict[str, Any]]:
        contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]} for item in request.messages]
        for result in request.tool_results:
            contents.append({"role": "user", "parts": [{"functionResponse": {"name": result.tool_name or "unknown", "response": {"call_id": result.call_id, "provider_call_id": result.provider_call_id, "status": result.status.value, "result": result.structured_result, "error": result.error}}}]})
        return contents

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload = {"contents": self._contents(request), "generationConfig": {"maxOutputTokens": request.max_output_tokens}}
        if request.tool_definitions:
            payload["tools"] = [{"functionDeclarations": request.tool_definitions}]
        return payload

    def request(self, request: ModelRequest) -> ModelResponse:
        key = self._key()
        url = f"{self.base_url}/models/{quote(self.model, safe='')}:generateContent"
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            category = "rate_limit" if exc.code == 429 else "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "provider_http"
            raise ProviderError(f"gemini {category}: HTTP {exc.code}", category=category, retryable=category in {"rate_limit"}, http_status=exc.code) from exc
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"gemini transport failed: {exc}", category="transport", retryable=True) from exc
        return decode_generate_content(raw, model=self.model, request_id=request.request_id)
