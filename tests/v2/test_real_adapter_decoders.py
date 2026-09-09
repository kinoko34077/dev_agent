import json
import io
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


def test_ollama_response_records_explicit_zero_local_cost(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"model":"local-test","done_reason":"stop","message":{"role":"assistant","content":"ok"},"prompt_eval_count":1,"eval_count":2}'

    monkeypatch.setattr("src.dev_agent.providers.ollama.provider.urlopen", lambda request, timeout: Response())
    response = OllamaProvider(model="local-test").request(ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}]))
    assert response.usage["cost_minor"] == 0


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
    assert function_response["id"] == "provider-1"


def test_gemini_http_payload_includes_function_declarations():
    provider = GeminiHttpProvider(model="gemini-test", api_key="test-key")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], tool_definitions=[{"name": "echo", "description": "Echo", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}}}])
    assert request.to_dict()["tool_definitions"][0]["name"] == "echo"
    assert provider._payload(request)["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "object"


def test_gemini_http_provider_replays_exact_model_parts_and_thought_signature(monkeypatch):
    model_parts = [
        {
            "functionCall": {"name": "echo", "id": "gemini-call-1", "args": {"value": "x"}},
            "thoughtSignature": "signature-1",
        }
    ]
    responses = iter(
        [
            {"candidates": [{"content": {"role": "model", "parts": model_parts}, "finishReason": "STOP"}]},
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}, "finishReason": "STOP"}]},
        ]
    )
    captured = []

    class Response:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(self.raw).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return Response(next(responses))

    monkeypatch.setattr(gemini_provider_module, "urlopen", fake_urlopen)
    task_id = _id()
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    first = ModelRequest(task_id=task_id, messages=[{"role": "user", "content": "call echo"}], tool_definitions=[{"name": "echo"}])
    response = provider.request(first)
    assert response.tool_calls[0].provider_call_id == "gemini-call-1"

    second = ModelRequest(
        task_id=task_id,
        messages=[
            {"role": "user", "content": "call echo"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "gemini-call-1", "type": "function"}]},
        ],
        tool_results=[ToolResult(call_id=_id(), tool_name="echo", provider_call_id="gemini-call-1", structured_result={"echo": "x"})],
    )
    provider.request(second)
    assert captured[1]["contents"] == [
        {"role": "user", "parts": [{"text": "call echo"}]},
        {"role": "model", "parts": model_parts},
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": "gemini-call-1",
                        "name": "echo",
                        "response": {
                            "call_id": second.tool_results[0].call_id,
                            "provider_call_id": "gemini-call-1",
                            "status": "succeeded",
                            "result": {"echo": "x"},
                            "error": None,
                        },
                    }
                }
            ],
        },
    ]
    assert provider.transcript_diagnostics(task_id) == {
        "model_turn_count": 2,
        "function_call_count": 1,
        "thought_signatures_received": 1,
        "thought_signatures_replayed": 1,
    }


def test_gemini_http_provider_replays_parallel_function_call_parts_in_order(monkeypatch):
    parts = [
        {"functionCall": {"name": "one", "id": "call-1", "args": {}}, "thoughtSignature": "sig-1"},
        {"functionCall": {"name": "two", "id": "call-2", "args": {}}},
    ]
    responses = iter(
        [
            {"candidates": [{"content": {"parts": parts}}]},
            {"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
        ]
    )
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(next(responses)).encode("utf-8")

    monkeypatch.setattr(gemini_provider_module, "urlopen", lambda request, timeout: (captured.append(json.loads(request.data)) or Response()))
    task_id = _id()
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    provider.request(ModelRequest(task_id=task_id, messages=[{"role": "user", "content": "call both"}]))
    results = [
        ToolResult(call_id=_id(), tool_name="one", provider_call_id="call-1", structured_result={"ok": 1}),
        ToolResult(call_id=_id(), tool_name="two", provider_call_id="call-2", structured_result={"ok": 2}),
    ]
    provider.request(ModelRequest(task_id=task_id, messages=[{"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}, {"id": "call-2"}]}], tool_results=results))
    assert captured[1]["contents"][0]["parts"] == parts
    function_responses = captured[1]["contents"][1]["parts"]
    assert [item["functionResponse"]["id"] for item in function_responses] == ["call-1", "call-2"]


def test_gemini_http_provider_thinking_effort_is_adapter_local():
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "reason"}], metadata={"thinking_effort": "low"})
    assert provider._payload(request)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}

    legacy = GeminiHttpProvider(model="gemini-2.5-flash", api_key="test-key")
    legacy_request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "reason"}], metadata={"thinking_effort": "low", "gemini_thinking_budget": 512})
    assert legacy._payload(legacy_request)["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 512}


def test_gemini_http_provider_rejects_tool_turn_without_preserved_transcript():
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    request = ModelRequest(
        task_id=_id(),
        messages=[{"role": "assistant", "content": "", "tool_calls": [{"id": "missing"}]}],
        tool_results=[ToolResult(call_id=_id(), tool_name="echo", provider_call_id="missing", structured_result={})],
    )
    with pytest.raises(ProviderError, match="transcript is unavailable") as exc_info:
        provider._payload(request)
    assert exc_info.value.category == "provider_context"


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


def test_gemini_http_provider_exposes_safe_http_diagnostic_without_secret(monkeypatch):
    body = b'{"error":{"code":400,"message":"Invalid argument for apiKey=AIza-secret-value","status":"INVALID_ARGUMENT"}}'
    error = HTTPError("https://example.test", 400, "bad request", {}, io.BytesIO(body))

    def failing_urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(gemini_provider_module, "urlopen", failing_urlopen)
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError) as exc_info:
        GeminiHttpProvider(model="gemini-test", api_key="test-key").request(request)
    assert "INVALID_ARGUMENT" in str(exc_info.value)
    assert "secret-value" not in str(exc_info.value)
    assert "apiKey=[REDACTED]" in str(exc_info.value)


def _id():
    from uuid import uuid4

    return str(uuid4())
