"""Read-only Mistral model discovery for Track C candidate auditing.

The Models API proves only which models are exposed to the existing credential.
It does not perform inference and does not promote rate-limit evidence into
strict runtime admission.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from typing import Any

from ..base import ProviderError
from ..openai_compatible.http import OpenAICompatibleHttpTransport


class MistralIntrospectionError(ValueError):
    """Mistral returned model metadata that cannot be trusted."""


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MistralIntrospectionError(f"{name} must be an object")
    return value


class MistralIntrospectionClient:
    """Inspect an existing Mistral credential without performing generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.mistral.ai/v1",
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
        key = self.api_key or os.environ.get("MISTRAL_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError(
                "mistral authentication failed: MISTRAL_API_KEY is not configured",
                category="authentication",
                retryable=False,
            )
        return key.strip()

    def read_models(self) -> list[dict[str, Any]]:
        raw, _headers = self._http.get_json(
            provider_id="mistral",
            url=f"{self.base_url}/models",
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        document = _mapping(raw, name="Mistral models response")
        rows = document.get("data")
        if not isinstance(rows, list):
            raise MistralIntrospectionError("Mistral models data must be an array")
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise MistralIntrospectionError("Mistral models data contains a malformed item")
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise MistralIntrospectionError("Mistral model id must be a non-empty string")
            if row.get("archived") is True:
                continue
            item: dict[str, Any] = {"id": model_id.strip()}
            if isinstance(row.get("max_context_length"), int) and not isinstance(row.get("max_context_length"), bool):
                item["max_context_length"] = row["max_context_length"]
            capabilities = row.get("capabilities")
            if isinstance(capabilities, Mapping):
                item["capabilities"] = {
                    key: value for key, value in capabilities.items() if isinstance(value, bool)
                }
            if isinstance(row.get("owned_by"), str):
                item["owned_by"] = row["owned_by"]
            result.append(item)
        result.sort(key=lambda item: item["id"])
        return result

    def read_state(self) -> dict[str, Any]:
        models = self.read_models()
        return {
            "schema_version": 1,
            "evidence_type": "mistral_read_only_model_state",
            "authority": "mistral-api",
            "model_ids": [item["id"] for item in models],
            "models": models,
            "model_count": len(models),
            "rate_quota_observed": False,
            "admission_ready": False,
            "interpretation": (
                "The Mistral Models API is read-only candidate-discovery evidence. "
                "Strict rate-limit evidence requires a separate bounded live header observation; "
                "this client performs no generation and derives no remaining/headroom."
            ),
        }


__all__ = ["MistralIntrospectionClient", "MistralIntrospectionError"]
