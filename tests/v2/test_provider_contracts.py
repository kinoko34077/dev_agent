import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare import CloudflareWorkersAIProvider
from src.dev_agent.providers.groq import GroqProvider
from src.dev_agent.providers.harness import ContractHarness
from src.dev_agent.providers.mistral import MistralProvider
from src.dev_agent.providers.openrouter import OpenRouterFreeProvider


def _backend(request):
    return {"model": "free-test", "text_segments": [request["messages"][-1]["content"]]}


@pytest.mark.parametrize(
    ("provider_type", "provider_id"),
    (
        (GroqProvider, "groq"),
        (CloudflareWorkersAIProvider, "cloudflare"),
        (MistralProvider, "mistral"),
        (OpenRouterFreeProvider, "openrouter"),
    ),
)
def test_free_provider_adapters_satisfy_normalized_contract(provider_type, provider_id):
    provider = provider_type(_backend, model="free-test")

    report = ContractHarness().probe(provider)

    assert report.errors == []
    assert report.capabilities == {"text"}
    assert report.provider_id == provider_id


def test_free_provider_adapters_preserve_typed_provider_errors():
    def failing_backend(_request):
        raise ProviderError("quota exhausted", category="quota", retryable=True)

    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    for provider in (
        GroqProvider(failing_backend),
        CloudflareWorkersAIProvider(failing_backend),
        MistralProvider(failing_backend),
        OpenRouterFreeProvider(failing_backend),
    ):
        with pytest.raises(ProviderError) as exc:
            provider.request(request)
        assert exc.value.category == "quota"
