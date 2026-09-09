import pytest

from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.providers.mistral import MistralHttpProvider
from src.dev_agent.providers.ollama import OllamaProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider
from src.dev_agent.providers.dispatch import ProviderRegistry
from src.dev_agent.providers.base import ProviderError


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


def test_provider_factory_keeps_binding_and_credential_identity_outside_provider_id():
    definition = ProviderDefinition(
        provider_id="mistral",
        model="mistral-small-latest",
        provider_binding_id="mistral:small:key-a",
        credential_id="mistral-key-a",
    )

    provider = ProviderFactory().create(definition)

    assert provider.provider_id == "mistral"
    assert provider.model == "mistral-small-latest"
    assert provider.provider_binding_id == "mistral:small:key-a"
    assert provider.credential_id == "mistral-key-a"
    assert provider.api_key is None


def test_provider_registry_allows_multiple_models_for_one_provider_without_ambiguous_get():
    registry = ProviderRegistry.from_definitions(
        [
            ProviderDefinition(provider_id="mistral", model="mistral-small-latest", provider_binding_id="mistral:small"),
            ProviderDefinition(provider_id="mistral", model="mistral-large-latest", provider_binding_id="mistral:large"),
        ]
    )

    assert registry.bindings_for_provider("mistral") == ("mistral:large", "mistral:small")
    assert registry.get_binding("mistral:small").model == "mistral-small-latest"
    with pytest.raises(ProviderError, match="provider binding"):
        registry.get("mistral")


def test_provider_factory_constructs_gemini_and_ollama_without_resolving_credentials():
    assert isinstance(ProviderFactory().create(ProviderDefinition(provider_id="gemini", model="gemini-2.5-flash")), GeminiHttpProvider)
    assert isinstance(ProviderFactory().create(ProviderDefinition(provider_id="ollama", model="qwen3:8b")), OllamaProvider)
