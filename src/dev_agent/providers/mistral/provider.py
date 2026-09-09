"""Injected-transport Mistral adapter.

The transport owns endpoint and authentication details. The Kernel only sees
the normalized ModelProvider contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    provider_id = "mistral"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "mistral-small-latest") -> None:
        super().__init__(transport, model=model)
