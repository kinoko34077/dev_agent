"""Injected-transport Mistral adapter.

The transport owns endpoint and authentication details. The Kernel only sees
the normalized ModelProvider contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import urlopen

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleHttpProvider, OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    provider_id = "mistral"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "mistral-small-latest") -> None:
        super().__init__(transport, model=model)


class MistralHttpProvider(OpenAICompatibleHttpProvider):
    """Mistral Chat Completions adapter on the shared HTTP boundary."""

    provider_id = "mistral"
    api_key_env = "MISTRAL_API_KEY"
    default_base_url = "https://api.mistral.ai/v1"
    max_output_tokens_field = "max_tokens"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = default_base_url,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(model=model, api_key=api_key, base_url=base_url, timeout_seconds=timeout_seconds, http_open=urlopen)
