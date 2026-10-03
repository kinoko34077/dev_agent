"""Injected-transport Cloudflare Workers AI adapter.

Endpoint and authentication details remain in the injected transport. The
adapter exposes only the normalized Provider contract to the Kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import json
import math
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from ...domain.protocol import ModelRequest, ModelResponse, ProtocolError, ToolCall
from ..base import ModelProvider, ProviderError, TransportStage, annotate_transport_failure
from ..openai_compatible import OpenAICompatibleProvider
from ..openai_compatible.http import REDIRECT_STATUS_CODES, _read_bounded, urlopen_no_redirect


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
    FREE_NEURON_ALLOWANCE = 10_000
    FREE_NEURON_RESET_SOURCE = "cloudflare_daily_utc"
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

    @staticmethod
    def _error_code(error: HTTPError) -> str | None:
        """Read only the bounded Cloudflare error code, never the raw body."""
        try:
            payload = json.loads(_read_bounded(error).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("errors"), list):
            return None
        for item in payload["errors"]:
            if isinstance(item, Mapping) and isinstance(item.get("code"), (int, str)):
                return str(item["code"])
        return None

    @classmethod
    def neurons_for_usage(cls, model: str, prompt_tokens: int, completion_tokens: int) -> int | None:
        """Return a conservative whole-Neuron debit for an exact model.

        A missing model-specific rate is intentionally not guessed.  The
        caller must then stop or use another independently admitted route.
        ``ceil`` prevents fractional usage from being under-debited.
        """
        rates = cls._NEURON_RATES_PER_MILLION.get(model)
        if rates is None or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (prompt_tokens, completion_tokens)
        ):
            return None
        return math.ceil((prompt_tokens * rates[0] + completion_tokens * rates[1]) / 1_000_000)

    @classmethod
    def _neuron_observation(cls, model: str, usage: Mapping[str, Any]) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        reset_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        period_id = now.date().isoformat()
        reported = usage.get("neurons")
        if isinstance(reported, int) and not isinstance(reported, bool) and reported >= 0:
            return {
                "unit": "neurons",
                "consumed": reported,
                "authority": "provider",
                "quota_authority": "authoritative_provider",
                "evidence_mode": "authoritative_provider",
                "limit": cls.FREE_NEURON_ALLOWANCE,
                "metric": "workers_ai_neurons",
                "window": "day",
                "reset_source": cls.FREE_NEURON_RESET_SOURCE,
                "period_id": period_id,
                "reset_at": reset_at.isoformat(),
                "source": "cloudflare-provider-neurons",
                "confidence": 1.0,
            }
        rates = cls._NEURON_RATES_PER_MILLION.get(model)
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        consumed = cls.neurons_for_usage(model, prompt_tokens, completion_tokens) if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int) else None
        if rates is None or consumed is None:
            return None
        return {
            "unit": "neurons",
            "consumed": consumed,
            "authority": "derived_conservative",
            "quota_authority": "derived_conservative",
            "evidence_mode": "derived_conservative",
            "limit": cls.FREE_NEURON_ALLOWANCE,
            "metric": "workers_ai_neurons",
            "window": "day",
            "reset_source": cls.FREE_NEURON_RESET_SOURCE,
            "period_id": period_id,
            "reset_at": reset_at.isoformat(),
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
        requested_model = model.strip() if isinstance(model, str) and model.strip() else None
        if requested_model is not None and model_name != requested_model:
            usage["provider_reported_model"] = model_name
        neuron_observation = cls._neuron_observation(model_name, usage)
        if neuron_observation is not None:
            usage["quota_observation"] = neuron_observation
        if not calls and not text:
            raise ProviderError("cloudflare response has no normalized content", category="provider_decode", retryable=False)
        return ModelResponse(
            provider="cloudflare",
            model=requested_model or model_name,
            finish_reason=str(result.get("finish_reason") or "stop"),
            text_segments=[text] if text else [],
            tool_calls=calls,
            usage=usage,
        )

    def probe_liveness(self) -> None:
        """Use Cloudflare's account model-list endpoint without generation."""

        account_id, api_token = self._credentials()
        url = f"{self.base_url}/accounts/{quote(account_id, safe='')}/ai/models/search"
        http_request = Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_token}"},
            method="GET",
        )
        transport_stage = TransportStage.RESPONSE_WAIT
        try:
            with urlopen_no_redirect(http_request, timeout=self.timeout_seconds) as response:
                transport_stage = TransportStage.RESPONSE_READ
                raw = json.loads(_read_bounded(response).decode("utf-8"))
        except HTTPError as exc:
            if exc.code in REDIRECT_STATUS_CODES:
                raise ProviderError(
                    f"cloudflare endpoint attempted an HTTP {exc.code} redirect; redirects are not permitted",
                    category="provider_http",
                    retryable=False,
                    http_status=exc.code,
                ) from exc
            if exc.code == 429 and self._error_code(exc) == "3036":
                raise ProviderError(
                    "cloudflare daily free neuron allocation exhausted",
                    category="quota",
                    retryable=False,
                    http_status=exc.code,
                    quota_metric="daily_neurons",
                    quota_window="day_utc",
                    quota_reset_source=self.FREE_NEURON_RESET_SOURCE,
                ) from exc
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            raise ProviderError(
                f"cloudflare liveness probe failed: HTTP {exc.code}",
                category=category,
                retryable=category == "rate_limit",
                http_status=exc.code,
            ) from exc
        except (URLError, OSError) as exc:
            failure = ProviderError("cloudflare liveness probe transport failed", category="transport", retryable=True)
            raise annotate_transport_failure(failure, stage=transport_stage, cause=exc) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("cloudflare liveness probe response decode failed", category="provider_decode", retryable=False) from exc
        if not isinstance(raw, Mapping) or raw.get("success") is not True or not isinstance(raw.get("result"), list):
            raise ProviderError("cloudflare liveness probe response decode failed", category="provider_decode", retryable=False)
        identifiers = {
            identifier.strip()
            for item in raw["result"]
            if isinstance(item, Mapping)
            for identifier in (item.get("name", item.get("id")),)
            if isinstance(identifier, str) and identifier.strip()
        }
        if self.model not in identifiers:
            raise ProviderError(
                "cloudflare selected model is not listed",
                category="provider_unavailable",
                retryable=True,
                failover_safe=True,
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
        transport_stage = TransportStage.RESPONSE_WAIT
        try:
            with urlopen_no_redirect(http_request, timeout=self.timeout_seconds) as response:
                transport_stage = TransportStage.RESPONSE_READ
                raw = json.loads(_read_bounded(response).decode("utf-8"))
        except HTTPError as exc:
            if exc.code in REDIRECT_STATUS_CODES:
                raise ProviderError(
                    f"cloudflare endpoint attempted an HTTP {exc.code} redirect; "
                    "redirects are not permitted and the request destination "
                    "was not followed",
                    category="provider_http",
                    retryable=False,
                    http_status=exc.code,
                ) from exc
            if exc.code == 429 and self._error_code(exc) == "3036":
                raise ProviderError(
                    "cloudflare daily free neuron allocation exhausted",
                    category="quota",
                    retryable=False,
                    http_status=exc.code,
                    quota_metric="daily_neurons",
                    quota_window="day_utc",
                    quota_reset_source=self.FREE_NEURON_RESET_SOURCE,
                ) from exc
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            raise ProviderError(f"cloudflare {category}: HTTP {exc.code}", category=category, retryable=category == "rate_limit", http_status=exc.code) from exc
        except (URLError, OSError) as exc:
            failure = ProviderError(f"cloudflare transport failed: {exc}", category="transport", retryable=True)
            raise annotate_transport_failure(failure, stage=transport_stage, cause=exc) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("cloudflare response decode failed", category="provider_decode", retryable=False) from exc
        return self._decode(raw, request, self.model)


__all__ = ["CloudflareWorkersAIHttpProvider", "CloudflareWorkersAIProvider"]
