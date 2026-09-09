"""Injected-transport Groq adapter.

The transport owns HTTP/authentication details. Only the normalized
ModelProvider contract crosses into the Kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    provider_id = "groq"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "groq") -> None:
        super().__init__(transport, model=model)

