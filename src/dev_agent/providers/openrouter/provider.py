"""Injected-transport OpenRouter free-route adapter.

The selected OpenRouter model/endpoint and authentication remain in the
injected transport. No OpenRouter SDK or payload type crosses the Kernel
boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleProvider


class OpenRouterFreeProvider(OpenAICompatibleProvider):
    provider_id = "openrouter"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "openrouter/free") -> None:
        super().__init__(transport, model=model)
