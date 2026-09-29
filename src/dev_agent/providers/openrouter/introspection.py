"""Read-only OpenRouter account/model introspection for R9 candidate discovery.

Spend controls, credits, and model metadata are useful operational evidence but
are deliberately not converted into ResourceRouter quota observations here.
In particular, ``limit_remaining`` from ``GET /api/v1/key`` is spend headroom,
not proof of request/token rate-limit headroom.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, InvalidOperation
import os
from typing import Any

from ..base import ProviderError
from ..openai_compatible.http import OpenAICompatibleHttpTransport


class OpenRouterIntrospectionError(ValueError):
    """OpenRouter returned metadata that cannot be trusted for introspection."""


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OpenRouterIntrospectionError(f"{name} must be an object")
    return value


def _array(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise OpenRouterIntrospectionError(f"{name} must be an array")
    return value


def _zero_price(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


class OpenRouterIntrospectionClient:
    """Use the existing OpenRouter key for bounded read-only metadata calls."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._http = OpenAICompatibleHttpTransport(opener)

    def _key(self) -> str:
        value = self.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not isinstance(value, str) or not value.strip():
            raise ProviderError(
                "openrouter authentication failed: OPENROUTER_API_KEY is not configured",
                category="authentication",
                retryable=False,
            )
        return value.strip()

    def _get(self, path: str) -> Mapping[str, Any]:
        raw, _headers = self._http.get_json(
            provider_id="openrouter",
            url=f"{self.base_url}{path}",
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        return _mapping(raw, name="OpenRouter response")

    def read_key_metadata(self) -> dict[str, Any]:
        payload = _mapping(self._get("/key").get("data"), name="OpenRouter key data")
        # The deprecated `rate_limit` field is intentionally omitted.  The
        # retained fields are spend/account controls and must not be treated as
        # request/token rate quota by routing code.
        allowed = (
            "label",
            "limit",
            "limit_remaining",
            "limit_reset",
            "usage",
            "usage_daily",
            "usage_weekly",
            "usage_monthly",
            "is_free_tier",
        )
        return {name: payload[name] for name in allowed if name in payload}

    def read_credits(self) -> dict[str, Any]:
        payload = _mapping(self._get("/credits").get("data"), name="OpenRouter credits data")
        result: dict[str, Any] = {}
        for name in ("total_credits", "total_usage"):
            if name in payload and isinstance(payload[name], (int, float)) and not isinstance(payload[name], bool):
                result[name] = payload[name]
        return result

    def read_models(self) -> list[dict[str, Any]]:
        items = _array(self._get("/models").get("data"), name="OpenRouter model data")
        result: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, Mapping):
                continue
            model_id = raw.get("id")
            pricing = raw.get("pricing")
            supported = raw.get("supported_parameters", [])
            if not isinstance(model_id, str) or not model_id.strip() or not isinstance(pricing, Mapping):
                continue
            if not isinstance(supported, list):
                supported = []
            supported_parameters = sorted({item.strip() for item in supported if isinstance(item, str) and item.strip()})
            entry: dict[str, Any] = {
                "id": model_id.strip(),
                "pricing": {
                    key: value
                    for key, value in pricing.items()
                    if key in {"prompt", "completion", "request", "image", "audio"}
                    and isinstance(value, (str, int, float))
                    and not isinstance(value, bool)
                },
                "supported_parameters": supported_parameters,
            }
            context_length = raw.get("context_length")
            if isinstance(context_length, int) and not isinstance(context_length, bool) and context_length > 0:
                entry["context_length"] = context_length
            result.append(entry)
        return result

    @staticmethod
    def zero_price_candidates(
        models: Iterable[Mapping[str, Any]],
        *,
        required_parameters: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        required = frozenset(required_parameters or ())
        candidates: list[dict[str, Any]] = []
        for raw in models:
            if not isinstance(raw, Mapping):
                continue
            model_id = raw.get("id")
            pricing = raw.get("pricing")
            supported = raw.get("supported_parameters")
            if not isinstance(model_id, str) or not isinstance(pricing, Mapping) or not isinstance(supported, list):
                continue
            supported_set = {item for item in supported if isinstance(item, str)}
            if not required.issubset(supported_set):
                continue
            if not (_zero_price(pricing.get("prompt")) and _zero_price(pricing.get("completion"))):
                continue
            request_price = pricing.get("request")
            if request_price is not None and not _zero_price(request_price):
                continue
            candidates.append(
                {
                    "id": model_id,
                    "supported_parameters": sorted(supported_set),
                    "context_length": raw.get("context_length") if isinstance(raw.get("context_length"), int) else None,
                    "pricing": dict(pricing),
                }
            )
        candidates.sort(key=lambda item: item["id"])
        return candidates

    def read_state(self, *, required_parameters: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
        spend_control = self.read_key_metadata()
        credits: dict[str, Any] | None
        credits_status = "AVAILABLE"
        try:
            credits = self.read_credits()
        except ProviderError as exc:
            # OpenRouter documents /credits as requiring a Management key. A
            # normal inference key must still be able to introspect itself and
            # the model catalog, so this narrower authorization failure is not
            # allowed to abort candidate discovery.
            if exc.category != "authorization":
                raise
            credits = None
            credits_status = "MANAGEMENT_KEY_REQUIRED"
        models = self.read_models()
        return {
            "schema_version": 1,
            "evidence_type": "openrouter_read_only_account_model_state",
            "authority": "openrouter-api",
            "spend_control": spend_control,
            "credits": credits,
            "credits_status": credits_status,
            "zero_price_candidates": self.zero_price_candidates(models, required_parameters=required_parameters),
            "model_count": len(models),
            "admission_ready": False,
            "interpretation": (
                "Spend/key limits and model pricing are candidate-discovery evidence only. "
                "They are not request/token rate-quota observations and do not establish RUNTIME_ELIGIBLE."
            ),
        }


__all__ = ["OpenRouterIntrospectionClient", "OpenRouterIntrospectionError"]
