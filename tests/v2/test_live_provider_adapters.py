import json

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ToolResult
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.groq import GroqHttpProvider


class _Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_groq_http_adapter_normalizes_tool_calls_usage_and_rate_limit_headers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "id": "chatcmpl-1",
                "model": "llama-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": '{"value":"ok"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
            {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "98",
                "x-ratelimit-limit-tokens": "10000",
                "x-ratelimit-remaining-tokens": "9990",
                "x-ratelimit-reset-requests": "2m",
            },
        )

    monkeypatch.setattr("src.dev_agent.providers.groq.provider.urlopen", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "call echo"}],
        tool_definitions=[{"name": "echo", "description": "echo", "parameters": {"type": "object"}}],
        tool_results=[ToolResult(call_id="00000000-0000-0000-0000-000000000002", tool_name="echo", structured_result={"value": "old"})],
        max_output_tokens=17,
    )

    response = GroqHttpProvider(model="llama-test", api_key="secret", timeout_seconds=4).request(request)

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "llama-test"
    assert captured["payload"]["max_completion_tokens"] == 17
    assert captured["payload"]["tools"][0]["type"] == "function"
    assert response.provider == "groq"
    assert response.tool_calls[0].tool_name == "echo"
    assert response.tool_calls[0].arguments == {"value": "ok"}
    assert response.usage["total_tokens"] == 7
    quota = response.usage["quota_observation"]
    assert quota["request_limit"] == 100
    assert quota["request_remaining"] == 98
    assert quota["token_remaining"] == 9990
    assert quota["source"] == "groq-rate-limit-header"


def test_cloudflare_http_adapter_normalizes_rest_envelope_without_inventing_quota(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"success": True, "result": {"response": "cloud answer"}})

    monkeypatch.setattr("src.dev_agent.providers.cloudflare.provider.urlopen", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=13,
    )

    response = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="account",
        api_token="token",
    ).request(request)

    assert captured["url"].endswith("/accounts/account/ai/run/@cf/meta/llama-3.1-8b-instruct")
    assert captured["payload"]["messages"] == request.messages
    assert captured["payload"]["max_tokens"] == 13
    assert response.provider == "cloudflare"
    assert response.text_segments == ["cloud answer"]
    assert "quota_observation" not in response.usage


def test_groq_live_http_adapter_fails_closed_when_credentials_are_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqHttpProvider(model="m")
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(ProviderError) as exc:
        provider.request(request)

    assert exc.value.category == "authentication"


def test_cloudflare_live_http_adapter_fails_closed_when_credentials_are_missing(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    provider = CloudflareWorkersAIHttpProvider(model="m")
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(ProviderError) as exc:
        provider.request(request)

    assert exc.value.category == "authentication"
