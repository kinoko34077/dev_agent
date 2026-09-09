"""Injected-transport OpenRouter free-route adapter.

The selected OpenRouter model/endpoint and authentication remain in the
injected transport. No OpenRouter SDK or payload type crosses the Kernel
boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelResponse
from urllib.request import urlopen

from ..openai_compatible import OpenAICompatibleHttpProvider, OpenAICompatibleProvider


class OpenRouterFreeProvider(OpenAICompatibleProvider):
    provider_id = "openrouter"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "openrouter/free") -> None:
        super().__init__(transport, model=model)


class OpenRouterHttpProvider(OpenAICompatibleHttpProvider):
    """OpenRouter Chat Completions adapter for free or explicitly selected models."""

    provider_id = "openrouter"
    api_key_env = "OPENROUTER_API_KEY"
    default_base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = default_base_url,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(model=model, api_key=api_key, base_url=base_url, timeout_seconds=timeout_seconds, http_open=urlopen)
