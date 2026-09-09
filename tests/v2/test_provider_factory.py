import pytest

from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.providers.mistral import MistralHttpProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider
from src.dev_agent.providers.dispatch import ProviderRegistry


def test_provider_factory_creates_http_provider_without_embedding_credentials():
    definition = ProviderDefinition(
        provider_id="openrouter",
        model="openrouter/free",
        timeout_seconds=4,
    )

    provider = ProviderFactory().create(definition)

    assert isinstance(provider, OpenRouterHttpProvider)
    assert provider.model == "openrouter/free"
    assert provider.api_key is None
    assert provider.timeout_seconds == 4.0


def test_provider_registry_can_be_built_from_provider_definitions():
    registry = ProviderRegistry.from_definitions(
        [
            ProviderDefinition(provider_id="mistral", model="mistral-small-latest"),
            ProviderDefinition(provider_id="openrouter", model="openrouter/free"),
        ]
    )

    assert isinstance(registry.get("mistral"), MistralHttpProvider)
    assert registry.get("openrouter").model == "openrouter/free"


def test_provider_factory_rejects_unknown_provider_definition():
    with pytest.raises(ValueError, match="unsupported provider"):
        ProviderFactory().create(ProviderDefinition(provider_id="unknown", model="model"))
