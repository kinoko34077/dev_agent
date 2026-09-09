"""Injected-transport Groq adapter.

The transport owns HTTP/authentication details. Only the normalized
ModelProvider contract crosses into the Kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse, ProtocolError, ToolCall
from ..base import ModelProvider, ProviderError
from ..openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    provider_id = "groq"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "groq") -> None:
        super().__init__(transport, model=model)


class GroqHttpProvider(ModelProvider):
    """Groq Chat Completions REST adapter.

    Authentication, endpoint details, OpenAI-compatible response decoding,
    and Groq rate-limit header parsing stay inside this adapter.  The Kernel
    receives only the normalized ``ModelResponse`` contract.
    """

    provider_id = "groq"
    _RESET_PART = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>h|m|s)")

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

    def _key(self) -> str:
        key = self.api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ProviderError("groq authentication failed: GROQ_API_KEY is not configured", category="authentication", retryable=False)
        return key

    @staticmethod
    def _messages(request: ModelRequest) -> list[dict[str, Any]]:
        messages = [dict(item) for item in request.messages]
        for result in request.tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.provider_call_id or result.call_id,
                    "name": result.tool_name or "unknown",
                    "content": json.dumps(
                        {"status": result.status.value, "result": result.structured_result, "error": result.error},
                        ensure_ascii=False,
                    ),
                }
            )
        return messages

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(request),
            "max_completion_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.tool_definitions:
            payload["tools"] = [{"type": "function", "function": dict(definition)} for definition in request.tool_definitions]
        return payload

    @staticmethod
    def _header(headers: Any, name: str) -> str | None:
        if headers is None or not hasattr(headers, "items"):
            return None
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value).strip()
        return None

    @classmethod
    def _header_int(cls, headers: Any, name: str) -> int | None:
        value = cls._header(headers, name)
        if value is None or not value.isdigit():
            return None
        return int(value)

    @classmethod
    def _reset_at(cls, value: str | None) -> str | None:
        if not value:
            return None
        remaining = value.strip()
        if not remaining:
            return None
        seconds = 0.0
        position = 0
        for match in cls._RESET_PART.finditer(remaining):
            if match.start() != position:
                return None
            amount = float(match.group("value"))
            seconds += amount * {"h": 3600, "m": 60, "s": 1}[match.group("unit")]
            position = match.end()
        if position != len(remaining):
            return None
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    @classmethod
    def _quota_observation(cls, headers: Any) -> dict[str, Any] | None:
        values = {
            "request_limit": cls._header_int(headers, "x-ratelimit-limit-requests"),
            "request_remaining": cls._header_int(headers, "x-ratelimit-remaining-requests"),
            "token_limit": cls._header_int(headers, "x-ratelimit-limit-tokens"),
            "token_remaining": cls._header_int(headers, "x-ratelimit-remaining-tokens"),
            "reset_at": cls._reset_at(cls._header(headers, "x-ratelimit-reset-requests")),
        }
        if not any(value is not None for value in values.values()):
            return None
        values.update({"source": "groq-rate-limit-header", "confidence": 1.0})
        return values

    @staticmethod
    def _decode(raw: Any, request: ModelRequest, headers: Any) -> ModelResponse:
        if not isinstance(raw, Mapping):
            raise ProviderError("groq response decode failed: response is not an object", category="provider_decode", retryable=False)
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderError("groq response decode failed: choices are missing", category="provider_decode", retryable=False)
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError("groq response decode failed: message is missing", category="provider_decode", retryable=False)
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ProviderError("groq response decode failed: tool_calls is not a list", category="provider_decode", retryable=False)
        for item in raw_calls:
            if not isinstance(item, Mapping) or not isinstance(item.get("function"), Mapping):
                raise ProviderError("groq response decode failed: malformed tool call", category="provider_decode", retryable=False)
            function = item["function"]
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ProviderError("groq response decode failed: tool arguments are not JSON", category="provider_decode", retryable=False) from exc
            if not isinstance(arguments, Mapping):
                raise ProviderError("groq response decode failed: tool arguments are not an object", category="provider_decode", retryable=False)
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ProviderError("groq response decode failed: tool name is missing", category="provider_decode", retryable=False)
            try:
                calls.append(ToolCall(tool_name=name, arguments=dict(arguments), provider_call_id=str(item.get("id")) if item.get("id") else None, originating_request_id=request.request_id))
            except (ProtocolError, TypeError, ValueError) as exc:
                raise ProviderError("groq response decode failed: invalid tool call", category="provider_decode", retryable=False) from exc
        text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise ProviderError("groq response decode failed: content is not text", category="provider_decode", retryable=False)
        usage = dict(raw.get("usage")) if isinstance(raw.get("usage"), Mapping) else {}
        quota = GroqHttpProvider._quota_observation(headers)
        if quota is not None:
            usage["quota_observation"] = quota
        if not calls and not text:
            raise ProviderError("groq response has no normalized content", category="provider_decode", retryable=False)
        return ModelResponse(
            provider="groq",
            model=str(raw.get("model") or "groq"),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            text_segments=[text] if text else [],
            tool_calls=calls,
            usage=usage,
        )

    def request(self, request: ModelRequest) -> ModelResponse:
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key()}"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
                headers = response.headers
        except HTTPError as exc:
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            raise ProviderError(f"groq {category}: HTTP {exc.code}", category=category, retryable=category == "rate_limit", http_status=exc.code) from exc
        except (URLError, OSError) as exc:
            raise ProviderError(f"groq transport failed: {exc}", category="transport", retryable=True) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("groq response decode failed", category="provider_decode", retryable=False) from exc
        return self._decode(raw, request, headers)


__all__ = ["GroqHttpProvider", "GroqProvider"]
