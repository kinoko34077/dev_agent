"""Provider adapter for an OpenAI-compatible endpoint or local gateway."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelRequest, ModelResponse
from ..base import ModelProvider, ProviderError
from ..normalize import normalize_response


class OpenAICompatibleProvider(ModelProvider):
    provider_id = "openai_compatible"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "compatible") -> None:
        self.transport = transport
        self.model = model

    def request(self, request: ModelRequest) -> ModelResponse:
        try:
            raw = self.transport(request.to_dict())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"openai-compatible transport failed: {exc}", category="transport", retryable=True) from exc
        return normalize_response(raw, provider=self.provider_id, default_model=self.model)
