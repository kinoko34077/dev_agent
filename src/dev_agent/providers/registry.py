"""Provider identity and binding registry."""

from __future__ import annotations

from dataclasses import replace

from .base import ModelProvider, ProviderError


class ProviderRegistry:
    """Resolve concrete provider instances by binding or unambiguous vendor."""

    @classmethod
    def from_definitions(cls, definitions, *, factory=None) -> "ProviderRegistry":
        if factory is None:
            from .factory import ProviderFactory

            factory = ProviderFactory()
        definitions = list(definitions)
        counts: dict[str, int] = {}
        for definition in definitions:
            counts[definition.provider_id] = counts.get(definition.provider_id, 0) + 1
        providers = []
        for definition in definitions:
            if definition.provider_binding_id is None and counts[definition.provider_id] > 1:
                definition = replace(definition, provider_binding_id=f"{definition.provider_id}:{definition.model}")
            providers.append(factory.create(definition))
        return cls(providers)

    def __init__(self, providers: list[ModelProvider] | tuple[ModelProvider, ...]) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._bindings_by_provider: dict[str, list[str]] = {}
        for provider in providers:
            if not isinstance(provider.provider_id, str) or not provider.provider_id.strip():
                raise ValueError("provider_id must be a non-empty string")
            binding_id = getattr(provider, "provider_binding_id", None) or provider.provider_id
            if not isinstance(binding_id, str) or not binding_id.strip():
                raise ValueError("provider_binding_id must be a non-empty string")
            binding_id = binding_id.strip()
            if binding_id in self._providers:
                raise ValueError(f"duplicate provider_id without distinct provider_binding_id: {binding_id}")
            self._providers[binding_id] = provider
            self._bindings_by_provider.setdefault(provider.provider_id, []).append(binding_id)
        if not self._providers:
            raise ValueError("at least one provider is required")

    def get(self, provider_id: str) -> ModelProvider:
        bindings = self._bindings_by_provider.get(provider_id, [])
        if len(bindings) > 1:
            raise ProviderError(f"provider binding is required for provider: {provider_id}", category="unsupported_capability", retryable=False)
        if len(bindings) == 1:
            return self._providers[bindings[0]]
        raise ProviderError(f"unsupported provider: {provider_id}", category="unsupported_capability", retryable=False)

    def get_binding(self, provider_binding_id: str) -> ModelProvider:
        try:
            return self._providers[provider_binding_id]
        except KeyError as exc:
            raise ProviderError(f"unsupported provider binding: {provider_binding_id}", category="unsupported_capability", retryable=False) from exc

    def bindings_for_provider(self, provider_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._bindings_by_provider.get(provider_id, ())))


__all__ = ["ProviderRegistry"]
