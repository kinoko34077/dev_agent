import json

from scripts.qualify_free_provider import qualify


class _Response:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._payload


def test_free_provider_qualification_uses_live_response_for_quota_and_dispatch(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    responses = [
        _Response(
            {
                "model": "test-model",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 2},
            },
            {"x-ratelimit-limit-requests": "10", "x-ratelimit-remaining-requests": "9", "x-ratelimit-reset-requests": "1m"},
        ),
        _Response(
            {
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"id": "call-live", "function": {"name": "echo", "arguments": '{"value":"free-provider-live"}'}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 3},
            }
        ),
        _Response(
            {
                "model": "test-model",
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 4},
            }
        ),
    ]

    def fake_urlopen(_request, timeout):
        assert timeout == 2.0
        return responses.pop(0)

    monkeypatch.setattr("src.dev_agent.providers.groq.provider.urlopen", fake_urlopen)

    output = qualify(provider_name="groq", model="test-model", timeout_seconds=2)

    assert output["status"] == "completed"
    assert output["quota_status"] == "observed"
    assert output["quota_observation"]["request_remaining"] == 9
    assert output["tool_result_count"] == 1
    assert output["provider_audit_count"] >= 2
    assert responses == []


def test_openrouter_free_qualification_uses_canonical_dispatch_path(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    responses = [
        _Response(
            {
                "model": "openrouter/free",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 2},
            }
        ),
        _Response(
            {
                "model": "openrouter/free",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"id": "call-live", "function": {"name": "echo", "arguments": '{"value":"free-provider-live"}'}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 3},
            }
        ),
        _Response(
            {
                "model": "openrouter/free",
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 4},
            }
        ),
    ]

    def fake_urlopen(_request, timeout):
        assert timeout == 2.0
        return responses.pop(0)

    monkeypatch.setattr("src.dev_agent.providers.openrouter.provider.urlopen", fake_urlopen)

    output = qualify(provider_name="openrouter", model="openrouter/free", timeout_seconds=2)

    assert output["status"] == "completed"
    assert output["provider"] == "openrouter"
    assert output["quota_status"] == "unknown_not_reported"
    assert output["tool_result_count"] == 1
    assert output["provider_audit_count"] >= 2
    assert responses == []


def test_mistral_free_qualification_uses_canonical_dispatch_path(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-secret")
    responses = [
        _Response(
            {
                "model": "mistral-small-latest",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 2},
            }
        ),
        _Response(
            {
                "model": "mistral-small-latest",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"id": "call-live", "function": {"name": "echo", "arguments": '{"value":"free-provider-live"}'}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 3},
            }
        ),
        _Response(
            {
                "model": "mistral-small-latest",
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 4},
            }
        ),
    ]

    def fake_urlopen(_request, timeout):
        assert timeout == 2.0
        return responses.pop(0)

    monkeypatch.setattr("src.dev_agent.providers.mistral.provider.urlopen", fake_urlopen)

    output = qualify(provider_name="mistral", model="mistral-small-latest", timeout_seconds=2)

    assert output["status"] == "completed"
    assert output["provider"] == "mistral"
    assert output["quota_status"] == "unknown_not_reported"
    assert output["tool_result_count"] == 1
    assert output["provider_audit_count"] >= 2
    assert responses == []
