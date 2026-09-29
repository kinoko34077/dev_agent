"""Read-only Mistral Admin API observation helpers for Track C.

The Admin API exposes organization rate limits, spend-limit status, and usage.
This module only reads those surfaces. It never creates/rotates keys, changes
limits, enables billing, or promotes observations into runtime eligibility.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ..base import ProviderError
from ..openai_compatible.http import urlopen_no_redirect

_MAX_ADMIN_RESPONSE_BYTES = 2 * 1024 * 1024


class MistralAdminObservationError(ValueError):
    """Mistral Admin returned data that cannot be trusted as an observation."""


def _read_json_bounded(response: Any) -> Any:
    raw = response.read(_MAX_ADMIN_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_ADMIN_RESPONSE_BYTES:
        raise MistralAdminObservationError("Mistral Admin response exceeded bounded size")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MistralAdminObservationError("Mistral Admin response was not valid JSON") from exc


def _object(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MistralAdminObservationError(f"{name} must be an object")
    return dict(value)


class MistralAdminObservationClient:
    """Read organization limits and usage with an existing admin API key."""

    def __init__(
        self,
        *,
        admin_api_key: str | None = None,
        base_url: str = "https://api.mistral.ai/v1/admin",
        timeout_seconds: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.admin_api_key = admin_api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or urlopen_no_redirect

    def _key(self) -> str:
        key = self.admin_api_key or os.environ.get("MISTRAL_ADMIN_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError(
                "mistral admin authentication failed: MISTRAL_ADMIN_API_KEY is not configured",
                category="authentication",
                retryable=False,
            )
        return key.strip()

    def _get(self, suffix: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{suffix.lstrip('/')}",
            headers={"x-api-key": self._key(), "Accept": "application/json"},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                return _object(_read_json_bounded(response), name=f"Mistral Admin {suffix} response")
        except HTTPError as exc:
            category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "rate_limit" if exc.code == 429 else "provider_http"
            raise ProviderError(
                f"mistral admin {category}: HTTP {exc.code}",
                category=category,
                retryable=category == "rate_limit",
                http_status=exc.code,
            ) from exc
        except (URLError, OSError) as exc:
            raise ProviderError(
                f"mistral admin transport failed: {type(exc).__name__}",
                category="transport",
                retryable=True,
            ) from exc

    def read_state(self) -> dict[str, Any]:
        rate_limit = self._get("rate-limit")
        spend_limit = self._get("spend-limit")
        usage = self._get("usage")
        return {
            "schema_version": 1,
            "evidence_type": "mistral_admin_read_only_state",
            "authority": "mistral-admin-api",
            "rate_limit": rate_limit,
            "spend_limit": spend_limit,
            "usage": usage,
            "rate_quota_observed": True,
            "billing_state_observed": True,
            "admission_ready": False,
            "interpretation": (
                "Authoritative read-only organization rate-limit, spend-limit, and usage observations. "
                "These raw surfaces do not by themselves prove Free mode, no-charge operation, current "
                "remaining headroom, or runtime eligibility."
            ),
        }


__all__ = ["MistralAdminObservationClient", "MistralAdminObservationError"]
