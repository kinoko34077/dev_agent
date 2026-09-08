import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.gemini import GeminiProvider
from src.dev_agent.providers.harness import ContractHarness
from src.dev_agent.providers.openai_compatible import OpenAICompatibleProvider
from src.dev_agent.providers.base import ProviderError


def _request_id():
    from uuid import uuid4

    return str(uuid4())


def _backend(request):
    if request["allowed_tools"]:
        return {"model": "adapter-test", "tool_calls": [{"tool_name": "echo", "arguments": {"value": "ok"}}], "finish_reason": "tool_call"}
    return {"model": "adapter-test", "text_segments": ["adapter response"]}


def test_gemini_adapter_uses_normalized_contract():
    report = ContractHarness().probe(GeminiProvider(_backend, model="adapter-test"))
    assert report.errors == []
    assert report.capabilities == {"text", "tool_call"}


def test_independent_adapter_uses_same_core_harness():
    report = ContractHarness().probe(OpenAICompatibleProvider(_backend, model="adapter-test"))
    assert report.errors == []
    assert report.capabilities == {"text", "tool_call"}


def test_adapters_do_not_require_provider_sdk_objects():
    response = GeminiProvider(lambda request: {"text_segments": ["ok"]}).request(ModelRequest(task_id=_request_id(), messages=[{"role": "user", "content": "x"}]))
    assert response.provider == "gemini"
    assert response.text_segments == ["ok"]


def test_adapters_preserve_typed_backend_provider_errors():
    def failing_backend(_request):
        raise ProviderError("quota exhausted", category="quota", retryable=True)

    request = ModelRequest(task_id=_request_id(), messages=[{"role": "user", "content": "x"}])
    for provider in (GeminiProvider(failing_backend), OpenAICompatibleProvider(failing_backend)):
        with pytest.raises(ProviderError) as exc:
            provider.request(request)
        assert exc.value.category == "quota"
