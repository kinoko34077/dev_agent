import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.groq import GroqHttpProvider
from src.dev_agent.providers.openai_compatible import OpenAICompatibleHttpProvider


class _Response:
    headers = {}

    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._payload


class _TestProvider(OpenAICompatibleHttpProvider):
    provider_id = "compatible-test"
    api_key_env = "COMPATIBLE_TEST_API_KEY"
    default_base_url = "https://compatible.test/v1"


def test_openai_compatible_http_provider_owns_wire_request_and_normalization():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "model": "compatible-model",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 3},
            }
        )

    provider = _TestProvider(model="compatible-model", api_key="secret", timeout_seconds=4, http_open=fake_urlopen)
    response = provider.request(
        ModelRequest(
            task_id="00000000-0000-0000-0000-000000000001",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=17,
        )
    )

    assert captured["url"] == "https://compatible.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "compatible-model"
    assert captured["payload"]["max_completion_tokens"] == 17
    assert response.provider == "compatible-test"
    assert response.text_segments == ["ready"]
    assert response.usage["total_tokens"] == 3


def test_openai_compatible_http_preserves_requested_binding_when_backend_reports_alias():
    def fake_urlopen(_request, timeout=None):
        assert timeout is not None
        return _Response(
            {
                "model": "backend-model-alias",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
            }
        )

    provider = _TestProvider(model="requested-model", api_key="secret", http_open=fake_urlopen)
    response = provider.request(
        ModelRequest(
            task_id="00000000-0000-0000-0000-000000000001",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert response.model == "requested-model"
    assert response.usage["provider_reported_model"] == "backend-model-alias"


def test_openai_compatible_http_error_detail_is_safe_for_provider_secrets():
    def fake_urlopen(_request, timeout):
        assert timeout == 4.0
        raise HTTPError(
            "https://compatible.test/v1/chat/completions",
            403,
            "Forbidden",
            {},
            BytesIO(b'{"error":{"message":"invalid api key sk-secret-value","type":"auth_error"}}'),
        )

    provider = _TestProvider(model="compatible-model", api_key="secret", timeout_seconds=4, http_open=fake_urlopen)

    with pytest.raises(ProviderError) as exc:
        provider.request(ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "hello"}]))

    assert exc.value.category == "authorization"
    assert "invalid api key" in str(exc.value)
    assert "sk-secret-value" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_openai_compatible_http_provider_can_probe_models_without_chat_dispatch():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _Response({"object": "list", "data": [{"id": "model-a", "owned_by": "provider"}]})

    provider = _TestProvider(model="compatible-model", api_key="secret", timeout_seconds=4, http_open=fake_urlopen)

    models = provider.list_models()

    assert captured["url"] == "https://compatible.test/v1/models"
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 4.0
    assert models == [{"id": "model-a", "owned_by": "provider"}]


def test_openai_compatible_http_provider_exposes_provider_neutral_quota_probe():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["timeout"] = timeout
        return _Response(
            {"object": "list", "data": [{"id": "model-a"}]},
            headers={
                "x-ratelimit-limit-requests": "10",
                "x-ratelimit-remaining-requests": "9",
                "x-ratelimit-reset-requests": "2s",
            },
        )

    provider = GroqHttpProvider(model="model-a", api_key="secret", timeout_seconds=4)
    provider._http = provider._http.__class__(fake_urlopen)

    result = provider.probe_quota("groq:worker", "groq-project")

    assert captured == {"url": "https://api.groq.com/openai/v1/models", "method": "GET", "timeout": 4.0}
    observation = result["quota_observation"]
    assert observation["quota_domain"] == "groq-project"
    assert observation["request_limit"] == 10
    assert observation["request_remaining"] == 9
