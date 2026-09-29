import importlib
import json

import pytest

from src.dev_agent.providers.base import ProviderError


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n=-1):
        return self.payload if n < 0 else self.payload[:n]


def _client_class():
    module = importlib.import_module("src.dev_agent.providers.groq.introspection")
    return module.GroqIntrospectionClient


def test_groq_discovery_requires_existing_key_before_network(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    called = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called without an existing Groq key")

    client = _client_class()(opener=unexpected_open)
    with pytest.raises(ProviderError) as caught:
        client.read_models()

    assert called is False
    assert caught.value.category == "authentication"


def test_groq_discovery_reads_active_model_catalog_without_generation():
    captured = []

    def fake_open(request, timeout):
        captured.append((request.full_url, request.method, {k.lower(): v for k, v in request.header_items()}, timeout, request.data))
        return _Response(
            {
                "object": "list",
                "data": [
                    {"id": "openai/gpt-oss-120b", "object": "model", "active": True, "context_window": 131072},
                    {"id": "qwen/qwen3.8-27b", "object": "model", "active": True, "context_window": 131072},
                ],
            }
        )

    state = _client_class()(api_key="existing-groq-key", opener=fake_open, timeout_seconds=4).read_state()

    assert state["authority"] == "groq-api"
    assert state["model_ids"] == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert state["model_count"] == 2
    assert state["rate_quota_observed"] is False
    assert state["admission_ready"] is False
    assert len(captured) == 1
    url, method, headers, timeout, data = captured[0]
    assert url == "https://api.groq.com/openai/v1/models"
    assert method == "GET"
    assert headers["authorization"] == "Bearer existing-groq-key"
    assert timeout == 4.0
    assert data is None
    assert "existing-groq-key" not in json.dumps(state)
