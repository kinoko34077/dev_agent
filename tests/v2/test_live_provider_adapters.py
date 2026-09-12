import json

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ToolResult
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.groq import GroqHttpProvider
from src.dev_agent.providers.mistral import MistralHttpProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider
from src.dev_agent.providers.sambanova import SambaNovaHttpProvider


class _Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self.payload
        return self.payload[:n]


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

    monkeypatch.setattr("src.dev_agent.providers.groq.provider.urlopen_no_redirect", fake_urlopen)
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

    monkeypatch.setattr("src.dev_agent.providers.cloudflare.provider.urlopen_no_redirect", fake_urlopen)
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


def test_cloudflare_http_adapter_preserves_requested_binding_when_backend_reports_alias(monkeypatch):
    def fake_urlopen(_request, timeout):
        return _Response(
            {
                "success": True,
                "result": {"model": "@cf/meta/llama-3.1-8b-fast-v2", "response": "cloud answer"},
            }
        )

    monkeypatch.setattr("src.dev_agent.providers.cloudflare.provider.urlopen_no_redirect", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "hello"}],
    )

    response = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="account",
        api_token="token",
    ).request(request)

    assert response.model == "@cf/meta/llama-3.1-8b-instruct"
    assert response.usage["provider_reported_model"] == "@cf/meta/llama-3.1-8b-fast-v2"


def test_cloudflare_http_adapter_marks_neuron_usage_as_estimated(monkeypatch):
    def fake_urlopen(request, timeout):
        return _Response(
            {
                "success": True,
                "result": {
                    "response": "cloud answer",
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 2000, "total_tokens": 3000},
                },
            }
        )

    monkeypatch.setattr("src.dev_agent.providers.cloudflare.provider.urlopen_no_redirect", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "hello"}],
    )

    response = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="account",
        api_token="token",
    ).request(request)

    quota = response.usage["quota_observation"]
    assert quota["unit"] == "neurons"
    assert quota["consumed"] == 177
    assert quota["authority"] == "estimated"
    assert quota["source"] == "cloudflare-neuron-estimate"
    assert quota["confidence"] < 1


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


def test_sambanova_http_adapter_normalizes_tool_calls_usage_and_rate_limit_headers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "id": "chatcmpl-sn-1",
                "model": "Meta-Llama-3.3-70B-Instruct",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_sn_1",
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
                "x-ratelimit-limit-requests": "20",
                "x-ratelimit-remaining-requests": "19",
                "x-ratelimit-limit-requests-day": "20",
                "x-ratelimit-remaining-requests-day": "18",
                "x-ratelimit-reset-requests": "30s",
                "x-ratelimit-reset-requests-day": "2h",
            },
        )

    monkeypatch.setattr("src.dev_agent.providers.sambanova.provider.urlopen_no_redirect", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "call echo"}],
        tool_definitions=[{"name": "echo", "description": "echo", "parameters": {"type": "object"}}],
        tool_results=[ToolResult(call_id="00000000-0000-0000-0000-000000000002", tool_name="echo", structured_result={"value": "old"})],
        max_output_tokens=17,
    )

    response = SambaNovaHttpProvider(model="Meta-Llama-3.3-70B-Instruct", api_key="secret", timeout_seconds=4).request(request)

    assert captured["url"] == "https://api.sambanova.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "Meta-Llama-3.3-70B-Instruct"
    assert captured["payload"]["max_completion_tokens"] == 17
    assert captured["payload"]["tools"][0]["type"] == "function"
    assert response.provider == "sambanova"
    assert response.tool_calls[0].tool_name == "echo"
    assert response.tool_calls[0].arguments == {"value": "ok"}
    assert response.usage["total_tokens"] == 7
    quota = response.usage["quota_observation"]
    assert quota["request_limit"] == 20
    assert quota["request_remaining"] == 19
    assert quota["daily_remaining"] == 18
    assert quota["source"] == "sambanova-rate-limit-header"


def test_sambanova_http_adapter_fails_closed_when_credentials_are_missing(monkeypatch):
    monkeypatch.delenv("SAMBANOVA_API_KEY", raising=False)
    provider = SambaNovaHttpProvider(model="m")
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(ProviderError) as exc:
        provider.request(request)

    assert exc.value.category == "authentication"


def test_openrouter_http_adapter_uses_openai_compatible_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response(
            {
                "model": "openrouter/free",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    monkeypatch.setattr("src.dev_agent.providers.openrouter.provider.urlopen_no_redirect", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=13,
    )

    response = OpenRouterHttpProvider(model="openrouter/free", api_key="secret", timeout_seconds=4).request(request)

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "openrouter/free"
    assert captured["payload"]["max_completion_tokens"] == 13
    assert captured["timeout"] == 4.0
    assert response.provider == "openrouter"
    assert response.text_segments == ["ready"]


def test_mistral_http_adapter_uses_shared_transport_with_mistral_token_field(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response(
            {
                "model": "mistral-small-latest",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    monkeypatch.setattr("src.dev_agent.providers.mistral.provider.urlopen_no_redirect", fake_urlopen)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=13,
    )

    response = MistralHttpProvider(model="mistral-small-latest", api_key="secret", timeout_seconds=4).request(request)

    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "mistral-small-latest"
    assert captured["payload"]["max_tokens"] == 13
    assert "max_completion_tokens" not in captured["payload"]
    assert captured["timeout"] == 4.0
    assert response.provider == "mistral"
    assert response.text_segments == ["ready"]
