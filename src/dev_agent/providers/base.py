"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.protocol import ModelRequest, ModelResponse


class ProviderError(RuntimeError):
    """A provider failed before returning a normalized response."""


class ModelProvider(ABC):
    provider_id: str = "unknown"

    @abstractmethod
    def request(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized response for a normalized request."""
