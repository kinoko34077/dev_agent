import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ToolResult
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.providers.gemini.decoder import decode_generate_content
from src.dev_agent.providers.ollama.provider import OllamaProvider
import src.dev_agent.providers.gemini.provider as gemini_provider_module


def test_gemini_rest_function_call_fixture_decodes_to_normalized_tool_call():
    raw = json.loads((Path(__file__).parent / "fixtures" / "gemini" / "function_call_response.json").read_text(encoding="utf-8"))
    response = decode_generate_content(raw, model="gemini-test", request_id="b9913466-2d8b-4da3-a665-47fd8ddf5819")
    assert response.tool_calls[0].tool_name == "echo"
    assert response.tool_calls[0].arguments == {"value": "fixture"}
    assert response.tool_calls[0].provider_call_id == "b9913466-2d8b-4da3-a665-47fd8ddf5819"
    assert response.tool_calls[0].call_id != response.tool_calls[0].provider_call_id


def test_gemini_rest_malformed_response_is_classified():
    with pytest.raises(ProviderError, match="missing candidate content"):
        decode_generate_content({"candidates": []}, model="gemini-test")


def test_ollama_payload_preserves_normalized_tool_result_identity():
    provider = OllamaProvider(model="local-test")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], tool_results=[ToolResult(call_id=_id(), tool_name="echo", structured_result={"value": "ok"})])
    payload = provider._payload(request)
    assert payload["options"]["num_predict"] == request.max_output_tokens
    tool_message = payload["messages"][-1]
    assert tool_message["tool_name"] == "echo"
    assert json.loads(tool_message["content"])["call_id"] == request.tool_results[0].call_id


def test_gemini_http_provider_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GEMINI_API_KEY is not configured"):
        GeminiHttpProvider(model="gemini-test").request(ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}]))


def test_gemini_http_payload_preserves_bound_and_tool_result_identity():
    provider = GeminiHttpProvider(model="gemini-test", api_key="test-key")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], max_output_tokens=7, tool_results=[ToolResult(call_id=_id(), tool_name="echo", provider_call_id="provider-1", structured_result={"value": "ok"})])
    payload = provider._payload(request)
    assert payload["generationConfig"]["maxOutputTokens"] == 7
    function_response = payload["contents"][-1]["parts"][0]["functionResponse"]
    assert function_response["name"] == "echo"
    assert function_response["response"]["call_id"] == request.tool_results[0].call_id
    assert function_response["response"]["provider_call_id"] == "provider-1"


def test_gemini_http_payload_includes_function_declarations():
    provider = GeminiHttpProvider(model="gemini-test", api_key="test-key")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], tool_definitions=[{"name": "echo", "description": "Echo", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}}}])
    assert request.to_dict()["tool_definitions"][0]["name"] == "echo"
    assert provider._payload(request)["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "object"


def test_gemini_http_provider_decodes_mocked_generate_content(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}'

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(gemini_provider_module, "urlopen", fake_urlopen)
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], max_output_tokens=9)
    response = GeminiHttpProvider(model="gemini-test", api_key="test-key").request(request)
    assert response.text_segments == ["ok"]
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 9
    assert "key=" not in captured["url"]
    assert captured["timeout"] == 30.0


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("unauthorized", "authentication"),
        ("rate_limit", "rate_limit"),
        ("transport", "transport"),
    ],
)
def test_gemini_http_provider_classifies_transport_failures(monkeypatch, kind, category):
    error = HTTPError("https://example.test", 401 if kind == "unauthorized" else 429, kind, {}, None) if kind != "transport" else URLError("timed out")

    def failing_urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(gemini_provider_module, "urlopen", failing_urlopen)
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError, match=category):
        GeminiHttpProvider(model="gemini-test", api_key="test-key").request(request)


def _id():
    from uuid import uuid4

    return str(uuid4())
