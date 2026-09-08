"""Provider adapter for a local model process or test backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelRequest, ModelResponse
from ..base import ModelProvider, ProviderError
from ..normalize import normalize_response


class LocalProvider(ModelProvider):
    provider_id = "local"

    def __init__(self, backend: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "local") -> None:
        self.backend = backend
        self.model = model

    def request(self, request: ModelRequest) -> ModelResponse:
        try:
            raw = self.backend(request.to_dict())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"local provider backend failed: {exc}", category="transport", retryable=False) from exc
        return normalize_response(raw, provider=self.provider_id, default_model=self.model)
