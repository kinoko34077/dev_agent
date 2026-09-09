"""Injected-transport Cloudflare Workers AI adapter.

Endpoint and authentication details remain in the injected transport. The
adapter exposes only the normalized Provider contract to the Kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse, ProtocolError, ToolCall
from ..base import ModelProvider, ProviderError
from ..openai_compatible import OpenAICompatibleProvider


class CloudflareWorkersAIProvider(OpenAICompatibleProvider):
    provider_id = "cloudflare"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "cloudflare") -> None:
        super().__init__(transport, model=model)


class CloudflareWorkersAIHttpProvider(ModelProvider):
    """Cloudflare Workers AI REST adapter.

    Cloudflare's account endpoint and ``{success,result}`` envelope are kept
    here.  The control plane sees only normalized text/tool calls and any
    usage fields explicitly returned by the model; absent quota values remain
    unknown rather than being invented.
    """

    provider_id = "cloudflare"
    # Cloudflare publishes model-specific token prices and a common neuron
    # price.  These constants are intentionally an estimate for models where
    # the response does not report neurons directly; they are never treated
    # as an authoritative remaining quota.
    _NEURON_RATES_PER_MILLION = {
        "@cf/meta/llama-3.1-8b-instruct": (25455, 75455),
    }

    def __init__(
        self,
        *,
        model: str,
        account_id: str | None = None,
        api_token: str | None = None,
        base_url: str = "https://api.cloudflare.com/client/v4",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model.strip()
        self.account_id = account_id
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

    def _credentials(self) -> tuple[str, str]:
        account_id = self.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = self.api_token or os.environ.get("CLOUDFLARE_API_TOKEN")
        if not account_id:
            raise ProviderError("cloudflare authentication failed: CLOUDFLARE_ACCOUNT_ID is not configured", category="authentication", retryable=False)
        if not api_token:
            raise ProviderError("cloudflare authentication failed: CLOUDFLARE_API_TOKEN is not configured", category="authentication", retryable=False)
        return account_id, api_token

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
        payload: dict[str, Any] = {"messages": self._messages(request), "max_tokens": request.max_output_tokens}
        if request.tool_definitions:
            payload["tools"] = [{"type": "function", "function": dict(definition)} for definition in request.tool_definitions]
        return payload

    @classmethod
    def _neuron_observation(cls, model: str, usage: Mapping[str, Any]) -> dict[str, Any] | None:
        rates = cls._NEURON_RATES_PER_MILLION.get(model)
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if rates is None or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (prompt_tokens, completion_tokens)
        ):
            return None
        consumed = math.ceil((prompt_tokens * rates[0] + completion_tokens * rates[1]) / 1_000_000)
        return {
            "unit": "neurons",
            "consumed": consumed,
            "authority": "estimated",
            "source": "cloudflare-neuron-estimate",
            "confidence": 0.25,
        }

    @staticmethod
    def _tool_calls(value: Any, request: ModelRequest) -> list[ToolCall]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ProviderError("cloudflare response decode failed: tool_calls is not a list", category="provider_decode", retryable=False)
        result: list[ToolCall] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ProviderError("cloudflare response decode failed: malformed tool call", category="provider_decode", retryable=False)
            function = item.get("function") if isinstance(item.get("function"), Mapping) else item
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ProviderError("cloudflare response decode failed: tool arguments are not JSON", category="provider_decode", retryable=False) from exc
            if not isinstance(arguments, Mapping):
                raise ProviderError("cloudflare response decode failed: tool arguments are not an object", category="provider_decode", retryable=False)
            name = function.get("name") or function.get("tool_name")
            if not isinstance(name, str) or not name.strip():
                raise ProviderError("cloudflare response decode failed: tool name is missing", category="provider_decode", retryable=False)
            try:
                result.append(ToolCall(tool_name=name, arguments=dict(arguments), provider_call_id=str(item.get("id")) if item.get("id") else None, originating_request_id=request.request_id))
            except (ProtocolError, TypeError, ValueError) as exc:
                raise ProviderError("cloudflare response decode failed: invalid tool call", category="provider_decode", retryable=False) from exc
        return result

    @classmethod
    def _decode(cls, raw: Any, request: ModelRequest, model: str | None = None) -> ModelResponse:
        if not isinstance(raw, Mapping) or raw.get("success") is not True:
            raise ProviderError("cloudflare provider returned an unsuccessful response", category="provider_http", retryable=False)
        result = raw.get("result")
        if not isinstance(result, Mapping):
            raise ProviderError("cloudflare response decode failed: result is missing", category="provider_decode", retryable=False)
        message = result.get("message") if isinstance(result.get("message"), Mapping) else result
        calls = cls._tool_calls(message.get("tool_calls"), request)
        text = message.get("response")
        if text is None:
            text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise ProviderError("cloudflare response decode failed: response is not text", category="provider_decode", retryable=False)
        usage = dict(result.get("usage")) if isinstance(result.get("usage"), Mapping) else {}
        model_name = str(result.get("model") or model or cls.provider_id)
        neuron_observation = cls._neuron_observation(model_name, usage)
        if neuron_observation is not None:
            usage["quota_observation"] = neuron_observation
        if not calls and not text:
            raise ProviderError("cloudflare response has no normalized content", category="provider_decode", retryable=False)
        return ModelResponse(
            provider="cloudflare",
            model=model_name,
            finish_reason=str(result.get("finish_reason") or "stop"),
            text_segments=[text] if text else [],
            tool_calls=calls,
            usage=usage,
        )

    def request(self, request: ModelRequest) -> ModelResponse:
        account_id, api_token = self._credentials()
        model_path = quote(self.model, safe="@/._-")
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = Request(
            f"{self.base_url}/accounts/{quote(account_id, safe='')}/ai/run/{model_path}",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            raise ProviderError(f"cloudflare {category}: HTTP {exc.code}", category=category, retryable=category == "rate_limit", http_status=exc.code) from exc
        except (URLError, OSError) as exc:
            raise ProviderError(f"cloudflare transport failed: {exc}", category="transport", retryable=True) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("cloudflare response decode failed", category="provider_decode", retryable=False) from exc
        return self._decode(raw, request, self.model)


__all__ = ["CloudflareWorkersAIHttpProvider", "CloudflareWorkersAIProvider"]
