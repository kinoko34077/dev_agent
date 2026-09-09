"""Configuration-only construction of concrete Provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .cloudflare import CloudflareWorkersAIHttpProvider
from .gemini import GeminiHttpProvider
from .groq import GroqHttpProvider
from .mistral import MistralHttpProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterHttpProvider
from .sambanova import SambaNovaHttpProvider


@dataclass(frozen=True)
class ProviderDefinition:
    """Non-secret Provider construction settings.

    API keys are deliberately resolved by each adapter from its external
    environment; they cannot be embedded in this definition.
    """

    provider_id: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = 30.0
    provider_binding_id: str | None = None
    credential_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if self.base_url is not None and (not isinstance(self.base_url, str) or not self.base_url.strip()):
            raise ValueError("base_url must be a non-empty string or None")
        for name, value in (("provider_binding_id", self.provider_binding_id), ("credential_id", self.credential_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "provider_id", self.provider_id.strip())
        object.__setattr__(self, "model", self.model.strip())
        if self.base_url is not None:
            object.__setattr__(self, "base_url", self.base_url.strip())
        if self.provider_binding_id is not None:
            object.__setattr__(self, "provider_binding_id", self.provider_binding_id.strip())
        if self.credential_id is not None:
            object.__setattr__(self, "credential_id", self.credential_id.strip())

    @property
    def model_id(self) -> str:
        return self.model


class ProviderFactory:
    _HTTP_PROVIDERS = {
        "cloudflare": CloudflareWorkersAIHttpProvider,
        "gemini": GeminiHttpProvider,
        "groq": GroqHttpProvider,
        "mistral": MistralHttpProvider,
        "ollama": OllamaProvider,
        "openrouter": OpenRouterHttpProvider,
        "sambanova": SambaNovaHttpProvider,
    }

    def create(self, definition: ProviderDefinition) -> Any:
        if not isinstance(definition, ProviderDefinition):
            raise TypeError("definition must be a ProviderDefinition")
        provider_type = self._HTTP_PROVIDERS.get(definition.provider_id)
        if provider_type is None:
            raise ValueError(f"unsupported provider: {definition.provider_id}")
        arguments: dict[str, Any] = {
            "model": definition.model,
            "timeout_seconds": definition.timeout_seconds,
        }
        if definition.base_url is not None:
            arguments["base_url"] = definition.base_url
        provider = provider_type(**arguments)
        # These are non-secret identity labels.  Adapters continue to resolve
        # credentials only from their own external environment or secret store.
        setattr(provider, "provider_binding_id", definition.provider_binding_id or definition.provider_id)
        setattr(provider, "model_id", definition.model)
        setattr(provider, "credential_id", definition.credential_id)
        return provider


__all__ = ["ProviderDefinition", "ProviderFactory"]
