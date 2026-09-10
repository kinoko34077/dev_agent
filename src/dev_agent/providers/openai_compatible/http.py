"""Shared HTTP transport and decoder for OpenAI-compatible Providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse, ProtocolError, ToolCall
from ..base import ModelProvider, ProviderError


class OpenAICompatibleHttpTransport:
    """Perform one JSON POST without leaking provider SDK objects."""

    _SECRET_ASSIGNMENT = re.compile(r"(?i)\b(api[_ -]?key|token|authorization|password|secret)\b\s*[:=]\s*[^\s,;}]+")
    _SECRET_TOKEN = re.compile(r"(?i)\b(?:sk|gsk|sn)[-_][a-z0-9_-]+\b")

    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener or urlopen

    @staticmethod
    def _safe_error_detail(error: HTTPError) -> str | None:
        try:
            raw = error.read(4096)
        except (OSError, ValueError):
            return None
        if not raw:
            return None
        try:
            decoded = raw.decode("utf-8", errors="replace")
        except AttributeError:
            return None
        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError:
            payload = decoded
        if isinstance(payload, Mapping):
            nested = payload.get("error")
            if isinstance(nested, Mapping):
                values = [nested.get(key) for key in ("message", "type", "code")]
            else:
                values = [payload.get(key) for key in ("message", "type", "code")]
            detail = ": ".join(str(value).strip() for value in values if isinstance(value, str) and value.strip())
        elif isinstance(payload, str):
            detail = payload.strip()
        else:
            detail = None
        if not detail:
            return None
        detail = " ".join(detail.split())
        detail = OpenAICompatibleHttpTransport._SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", detail)
        detail = OpenAICompatibleHttpTransport._SECRET_TOKEN.sub("[REDACTED]", detail)
        return detail[:512]

    def _request_json(
        self,
        *,
        provider_id: str,
        url: str,
        method: str,
        payload: Mapping[str, Any] | None,
        api_key: str,
        timeout_seconds: float,
    ) -> tuple[Any, Any]:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method=method,
        )
        try:
            with self._opener(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
                return raw, response.headers
        except HTTPError as exc:
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            detail = self._safe_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            raise ProviderError(
                f"{provider_id} {category}: HTTP {exc.code}{suffix}",
                category=category,
                retryable=category == "rate_limit",
                http_status=exc.code,
            ) from exc
        except (URLError, OSError) as exc:
            raise ProviderError(f"{provider_id} transport failed: {exc}", category="transport", retryable=True) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{provider_id} response decode failed", category="provider_decode", retryable=False) from exc

    def post_json(
        self,
        *,
        provider_id: str,
        url: str,
        payload: Mapping[str, Any],
        api_key: str,
        timeout_seconds: float,
    ) -> tuple[Any, Any]:
        return self._request_json(
            provider_id=provider_id,
            url=url,
            method="POST",
            payload=payload,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def get_json(
        self,
        *,
        provider_id: str,
        url: str,
        api_key: str,
        timeout_seconds: float,
    ) -> tuple[Any, Any]:
        return self._request_json(
            provider_id=provider_id,
            url=url,
            method="GET",
            payload=None,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )


class OpenAICompatibleHttpProvider(ModelProvider):
    """Normalized Chat Completions Provider built on the shared HTTP layer."""

    provider_id = "openai_compatible_http"
    api_key_env = "OPENAI_API_KEY"
    default_base_url = "https://api.openai.com/v1"
    max_output_tokens_field = "max_completion_tokens"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        http_open: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        resolved_base_url = self.default_base_url if base_url is None else base_url
        if not isinstance(resolved_base_url, str) or not resolved_base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = resolved_base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._http = OpenAICompatibleHttpTransport(http_open)

    def _key(self) -> str:
        key = self.api_key or os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError(
                f"{self.provider_id} authentication failed: {self.api_key_env} is not configured",
                category="authentication",
                retryable=False,
            )
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
            "stream": False,
        }
        payload[self.max_output_tokens_field] = request.max_output_tokens
        if request.tool_definitions:
            payload["tools"] = [{"type": "function", "function": dict(definition)} for definition in request.tool_definitions]
        return payload

    @classmethod
    def _header(cls, headers: Any, name: str) -> str | None:
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
    def _quota_observation(cls, headers: Any) -> dict[str, Any] | None:
        return None

    @classmethod
    def _tool_calls(cls, value: Any, request: ModelRequest) -> list[ToolCall]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ProviderError(f"{cls.provider_id} response decode failed: tool_calls is not a list", category="provider_decode", retryable=False)
        calls: list[ToolCall] = []
        for item in value:
            if not isinstance(item, Mapping) or not isinstance(item.get("function"), Mapping):
                raise ProviderError(f"{cls.provider_id} response decode failed: malformed tool call", category="provider_decode", retryable=False)
            function = item["function"]
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ProviderError(f"{cls.provider_id} response decode failed: tool arguments are not JSON", category="provider_decode", retryable=False) from exc
            if not isinstance(arguments, Mapping):
                raise ProviderError(f"{cls.provider_id} response decode failed: tool arguments are not an object", category="provider_decode", retryable=False)
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ProviderError(f"{cls.provider_id} response decode failed: tool name is missing", category="provider_decode", retryable=False)
            try:
                calls.append(
                    ToolCall(
                        tool_name=name,
                        arguments=dict(arguments),
                        provider_call_id=str(item.get("id")) if item.get("id") else None,
                        originating_request_id=request.request_id,
                    )
                )
            except (ProtocolError, TypeError, ValueError) as exc:
                raise ProviderError(f"{cls.provider_id} response decode failed: invalid tool call", category="provider_decode", retryable=False) from exc
        return calls

    @classmethod
    def _decode(cls, raw: Any, request: ModelRequest, headers: Any) -> ModelResponse:
        if not isinstance(raw, Mapping):
            raise ProviderError(f"{cls.provider_id} response decode failed: response is not an object", category="provider_decode", retryable=False)
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderError(f"{cls.provider_id} response decode failed: choices are missing", category="provider_decode", retryable=False)
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError(f"{cls.provider_id} response decode failed: message is missing", category="provider_decode", retryable=False)
        calls = cls._tool_calls(message.get("tool_calls"), request)
        text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise ProviderError(f"{cls.provider_id} response decode failed: content is not text", category="provider_decode", retryable=False)
        usage = dict(raw.get("usage")) if isinstance(raw.get("usage"), Mapping) else {}
        quota = cls._quota_observation(headers)
        if quota is not None:
            usage["quota_observation"] = quota
        if not calls and not text:
            raise ProviderError(f"{cls.provider_id} response has no normalized content", category="provider_decode", retryable=False)
        return ModelResponse(
            provider=cls.provider_id,
            model=str(raw.get("model") or cls.provider_id),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            text_segments=[text] if text else [],
            tool_calls=calls,
            usage=usage,
        )

    def request(self, request: ModelRequest) -> ModelResponse:
        raw, headers = self._http.post_json(
            provider_id=self.provider_id,
            url=f"{self.base_url}/chat/completions",
            payload=self._payload(request),
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        return self._decode(raw, request, headers)

    def list_models(self) -> list[dict[str, Any]]:
        """Return safe model metadata from the provider's models endpoint."""
        raw, _headers = self._http.get_json(
            provider_id=self.provider_id,
            url=f"{self.base_url}/models",
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(raw, Mapping) or not isinstance(raw.get("data"), list):
            raise ProviderError(f"{self.provider_id} models response decode failed", category="provider_decode", retryable=False)
        models: list[dict[str, Any]] = []
        for item in raw["data"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise ProviderError(f"{self.provider_id} models response decode failed", category="provider_decode", retryable=False)
            models.append(dict(item))
        return models

    def probe_quota(self, resource_id: str, quota_domain: str) -> dict[str, Any]:
        """Return one provider-neutral quota observation from a safe probe.

        The endpoint is deliberately the existing models probe, not a chat
        request.  Providers that do not expose usable rate-limit headers
        return an empty mapping; the requalification coordinator then keeps
        the existing block instead of inventing quota telemetry.
        """

        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("resource_id must be a non-empty string")
        if not isinstance(quota_domain, str) or not quota_domain.strip():
            raise ValueError("quota_domain must be a non-empty string")
        _raw, headers = self._http.get_json(
            provider_id=self.provider_id,
            url=f"{self.base_url}/models",
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        quota = self._quota_observation(headers)
        if quota is None:
            return {}
        observation = dict(quota)
        observation["quota_domain"] = quota_domain.strip()
        observation.setdefault("source", f"{self.provider_id}-quota-probe")
        return {"quota_observation": observation}


__all__ = ["OpenAICompatibleHttpProvider", "OpenAICompatibleHttpTransport"]
