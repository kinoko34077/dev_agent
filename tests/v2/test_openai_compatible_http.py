import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.openai_compatible import OpenAICompatibleHttpProvider


class _Response:
    headers = {}

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

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
