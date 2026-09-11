import pytest

from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.providers.mistral import MistralHttpProvider
from src.dev_agent.providers.ollama import OllamaProvider
from src.dev_agent.providers.ollama_cloud import OllamaCloudHttpProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider
from src.dev_agent.providers.vercel import VercelAIGatewayHttpProvider
from src.dev_agent.providers.dispatch import ProviderRegistry
from src.dev_agent.providers.base import ProviderError
from scripts.qualify_free_provider import _provider


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


def test_provider_factory_constructs_cloud_bindings_with_distinct_identities():
    ollama_cloud = ProviderFactory().create(
        ProviderDefinition(
            provider_id="ollama_cloud",
            model="gpt-oss:20b-cloud",
            provider_binding_id="ollama_cloud:free",
            api_key_env="OLLAMA_API_KEY",
        )
    )
    vercel = ProviderFactory().create(
        ProviderDefinition(
            provider_id="vercel",
            model="openai/gpt-oss-120b",
            provider_binding_id="vercel:free",
            api_key_env="AI_GATEWAY_API_KEY",
        )
    )

    assert isinstance(ollama_cloud, OllamaCloudHttpProvider)
    assert ollama_cloud.base_url == "https://ollama.com/v1"
    assert ollama_cloud.api_key_env == "OLLAMA_API_KEY"
    assert isinstance(vercel, VercelAIGatewayHttpProvider)
    assert vercel.base_url == "https://ai-gateway.vercel.sh/v1"
    assert vercel.api_key_env == "AI_GATEWAY_API_KEY"


def test_provider_factory_keeps_gemini_credential_env_and_project_metadata_non_secret():
    provider = ProviderFactory().create(
        ProviderDefinition(
            provider_id="gemini",
            model="gemini-3.5-flash-lite",
            provider_binding_id="gemini:worker:free-2",
            credential_id="gemini-key-2",
            api_key_env="GEMINI_API_KEY_2",
            project_id="projects/394829782092",
        )
    )

    assert provider.api_key_env == "GEMINI_API_KEY_2"
    assert provider.credential_id == "gemini-key-2"
    assert provider.project_id == "projects/394829782092"


def test_provider_factory_does_not_copy_api_key_value_into_provider_definition(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_2", "test-secret-value")
    provider = ProviderFactory().create(
        ProviderDefinition(
            provider_id="gemini",
            model="gemini-3.5-flash-lite",
            provider_binding_id="gemini:worker:free-2",
            api_key_env="GEMINI_API_KEY_2",
        )
    )

    assert provider.api_key is None
    assert provider.api_key_env == "GEMINI_API_KEY_2"
    assert "test-secret-value" not in repr(provider.__dict__)


def test_free_qualification_uses_factory_for_gemini():
    provider = _provider("gemini", "gemini-3.5-flash-lite", 4)
    assert isinstance(provider, GeminiHttpProvider)
    assert provider.provider_binding_id == "gemini:qualification"
