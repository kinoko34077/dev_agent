import json
from io import BytesIO
from urllib.error import HTTPError

from scripts.probe_groq_models import probe


class _Response:
    headers = {}

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._payload
        return self._payload[:n]


def test_groq_models_probe_distinguishes_listed_and_unlisted_models(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://api.groq.com/openai/v1/models"
        assert request.method == "GET"
        assert timeout == 2.0
        return _Response({"object": "list", "data": [{"id": "llama-available", "owned_by": "groq"}]})

    monkeypatch.setattr("src.dev_agent.providers.groq.provider.urlopen_no_redirect", fake_urlopen)

    listed = probe(model="llama-available", timeout_seconds=2)
    unlisted = probe(model="llama-missing", timeout_seconds=2)

    assert listed["status"] == "ok"
    assert listed["model_status"] == "listed"
    assert listed["diagnosis"] == "models_endpoint_ok_inference_permission_unproven"
    assert unlisted["status"] == "model_not_listed"
    assert unlisted["model_status"] == "not_listed"


def test_groq_models_probe_reports_account_permission_without_leaking_error_body(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")

    def fake_urlopen(_request, timeout):
        assert timeout == 2.0
        raise HTTPError(
            "https://api.groq.com/openai/v1/models",
            403,
            "Forbidden",
            {},
            BytesIO(b'{"error":{"message":"organization denied api_key=gsk-secret-value","type":"access_error"}}'),
        )

    monkeypatch.setattr("src.dev_agent.providers.groq.provider.urlopen_no_redirect", fake_urlopen)

    output = probe(model="llama-any", timeout_seconds=2)

    assert output["status"] == "failed"
    assert output["category"] == "authorization"
    assert output["diagnosis"] == "models_endpoint_permission_denied"
    assert "gsk-secret-value" not in output["message"]
    assert "[REDACTED]" in output["message"]
