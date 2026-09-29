"""Read-only Groq model discovery for Track C candidate auditing.

The Models API proves only which models are currently exposed to the existing
credential.  It does not perform inference and therefore cannot produce the
response rate-limit headers required for strict runtime admission.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from typing import Any

from ..base import ProviderError
from ..openai_compatible.http import OpenAICompatibleHttpTransport


class GroqIntrospectionError(ValueError):
    """Groq returned model metadata that cannot be trusted."""


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GroqIntrospectionError(f"{name} must be an object")
    return value


class GroqIntrospectionClient:
    """Inspect the existing Groq credential without performing generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
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
        key = self.api_key or os.environ.get("GROQ_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError(
                "groq authentication failed: GROQ_API_KEY is not configured",
                category="authentication",
                retryable=False,
            )
        return key.strip()

    def read_models(self) -> list[dict[str, Any]]:
        raw, _headers = self._http.get_json(
            provider_id="groq",
            url=f"{self.base_url}/models",
            api_key=self._key(),
            timeout_seconds=self.timeout_seconds,
        )
        document = _mapping(raw, name="Groq models response")
        rows = document.get("data")
        if not isinstance(rows, list):
            raise GroqIntrospectionError("Groq models data must be an array")
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise GroqIntrospectionError("Groq models data contains a malformed item")
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise GroqIntrospectionError("Groq model id must be a non-empty string")
            item: dict[str, Any] = {"id": model_id.strip()}
            if isinstance(row.get("active"), bool):
                item["active"] = row["active"]
            if isinstance(row.get("context_window"), int) and not isinstance(row.get("context_window"), bool):
                item["context_window"] = row["context_window"]
            if isinstance(row.get("max_completion_tokens"), int) and not isinstance(row.get("max_completion_tokens"), bool):
                item["max_completion_tokens"] = row["max_completion_tokens"]
            if isinstance(row.get("owned_by"), str):
                item["owned_by"] = row["owned_by"]
            result.append(item)
        result.sort(key=lambda item: item["id"])
        return result

    def read_state(self) -> dict[str, Any]:
        models = self.read_models()
        active = [item for item in models if item.get("active", True) is not False]
        return {
            "schema_version": 1,
            "evidence_type": "groq_read_only_model_state",
            "authority": "groq-api",
            "model_ids": [item["id"] for item in active],
            "models": active,
            "model_count": len(active),
            "rate_quota_observed": False,
            "admission_ready": False,
            "interpretation": (
                "The Groq Models API is read-only candidate-discovery evidence. "
                "Strict request/token quota evidence remains observable only on an inference response; "
                "no generation is performed by this client."
            ),
        }


__all__ = ["GroqIntrospectionClient", "GroqIntrospectionError"]
