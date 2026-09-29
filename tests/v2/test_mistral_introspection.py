import json

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.mistral.introspection import MistralIntrospectionClient, MistralIntrospectionError


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


def test_mistral_introspection_fails_before_network_without_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    called = False

    def opener(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ProviderError) as caught:
        MistralIntrospectionClient(opener=opener).read_state()

    assert caught.value.category == "authentication"
    assert called is False


def test_mistral_introspection_reads_models_without_generation():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["data"] = request.data
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _Response(
            {
                "object": "list",
                "data": [
                    {
                        "id": "mistral-small-latest",
                        "archived": False,
                        "max_context_length": 262144,
                        "owned_by": "mistralai",
                        "capabilities": {"completion_chat": True, "function_calling": True},
                    },
                    {"id": "archived-model", "archived": True},
                ],
            }
        )

    state = MistralIntrospectionClient(api_key="secret", timeout_seconds=4, opener=opener).read_state()

    assert captured["url"] == "https://api.mistral.ai/v1/models"
    assert captured["method"] == "GET"
    assert captured["data"] is None
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 4.0
    assert state["model_ids"] == ["mistral-small-latest"]
    assert state["models"][0]["capabilities"]["function_calling"] is True
    assert state["rate_quota_observed"] is False
    assert state["admission_ready"] is False


def test_mistral_introspection_rejects_malformed_model_rows():
    def opener(_request, timeout):
        return _Response({"data": [{"archived": False}]})

    with pytest.raises(MistralIntrospectionError):
        MistralIntrospectionClient(api_key="secret", opener=opener).read_models()
