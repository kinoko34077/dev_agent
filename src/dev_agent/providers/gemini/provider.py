"""Injected-transport Gemini adapter.

The transport may be backed by the Google SDK in a deployment, but the SDK
object is never returned to the Kernel. Offline tests use a plain callable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelRequest, ModelResponse
from ..base import ModelProvider, ProviderError
from ..normalize import normalize_response


class GeminiProvider(ModelProvider):
    provider_id = "gemini"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "gemini") -> None:
        self.transport = transport
        self.model = model

    def request(self, request: ModelRequest) -> ModelResponse:
        try:
            raw = self.transport(request.to_dict())
        except Exception as exc:
            raise ProviderError(f"gemini transport failed: {exc}") from exc
        return normalize_response(raw, provider=self.provider_id, default_model=self.model)
