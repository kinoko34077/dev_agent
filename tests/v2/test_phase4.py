import json
from pathlib import Path

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.harness import ContractHarness
from src.dev_agent.providers.local import LocalProvider
from src.dev_agent.providers.gemini import GeminiProvider


def test_local_provider_completes_contract_without_cloud():
    def backend(request):
        if request["allowed_tools"]:
            return {"model": "local-test", "finish_reason": "tool_call", "tool_calls": [{"tool_name": "echo", "arguments": {"value": "local"}}]}
        return {"model": "local-test", "text_segments": ["local response"]}

    report = ContractHarness().probe(LocalProvider(backend, model="local-test"))
    assert report.errors == []
    assert report.capabilities == {"text", "tool_call"}


def test_local_provider_normalizes_model_response_directly():
    provider = LocalProvider(lambda request: ModelResponse(provider="local", model="test", text_segments=["ok"]))
    response = provider.request(ModelRequest(task_id=_task_id(), messages=[{"role": "user", "content": "x"}]))
    assert response.text_segments == ["ok"]


def _task_id():
    from uuid import uuid4

    return str(uuid4())


def test_v1_whichoneof_fixture_becomes_classified_adapter_failure():
    fixture = Path(__file__).parent / "fixtures" / "v1" / "gemini_whichoneof_error.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    with pytest.raises(ProviderError, match="raw response error"):
        GeminiProvider(lambda request: raw).request(ModelRequest(task_id=_task_id(), messages=[{"role": "user", "content": "fixture"}]))
