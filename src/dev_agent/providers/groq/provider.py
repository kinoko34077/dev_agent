"""Groq Provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from urllib.request import urlopen

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleHttpProvider, OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    """Injected-transport Groq adapter."""

    provider_id = "groq"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "groq") -> None:
        super().__init__(transport, model=model)


class GroqHttpProvider(OpenAICompatibleHttpProvider):
    """Groq Chat Completions adapter with provider-specific quota headers."""

    provider_id = "groq"
    api_key_env = "GROQ_API_KEY"
    default_base_url = "https://api.groq.com/openai/v1"
    _RESET_PART = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>h|m|s)")

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = default_base_url,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(model=model, api_key=api_key, base_url=base_url, timeout_seconds=timeout_seconds, http_open=urlopen)

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
            seconds += float(match.group("value")) * {"h": 3600, "m": 60, "s": 1}[match.group("unit")]
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


__all__ = ["GroqHttpProvider", "GroqProvider"]
