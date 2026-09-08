"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.protocol import ModelRequest, ModelResponse


class ProviderError(RuntimeError):
    """A provider failed before returning a normalized response."""

    def __init__(self, message: str, *, category: str | None = None, retryable: bool | None = None, http_status: int | None = None) -> None:
        super().__init__(message)
        prefix = message.split(":", 1)[0].strip()
        self.category = category or (prefix if prefix in {"transport", "authentication", "authorization", "rate_limit", "quota", "provider_http", "provider_decode", "context_limit", "output_limit", "safety_block", "unsupported_capability"} else "provider_http")
        self.retryable = retryable if retryable is not None else self.category in {"transport", "rate_limit", "quota"}
        self.http_status = http_status


class ModelProvider(ABC):
    provider_id: str = "unknown"

    @abstractmethod
    def request(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized response for a normalized request."""
