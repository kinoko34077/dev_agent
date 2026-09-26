from __future__ import annotations

import json

import pytest

from src.dev_agent.providers.ollama.lifecycle import OllamaLifecycleError, OllamaModelManager


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._payload if n < 0 else self._payload[:n]

    def readline(self, n: int = -1) -> bytes:
        payload = self._payload + b"\n"
        return payload if n < 0 else payload[:n]


def test_manager_parses_installed_and_loaded_models(monkeypatch):
    responses = iter([
        {"models": [{"name": "qwen3.5:9b", "size": 123, "digest": "sha"}]},
        {"models": [{"name": "qwen3.5:9b", "size": 123, "size_vram": 456, "expires_at": "later"}]},
    ])
    monkeypatch.setattr(
        "src.dev_agent.providers.ollama.lifecycle.urlopen_no_redirect",
        lambda request, timeout: _Response(next(responses)),
    )

    manager = OllamaModelManager()
    installed = manager.list_installed()
    loaded = manager.list_loaded()

    assert installed[0].name == "qwen3.5:9b"
    assert installed[0].size == 123
    assert installed[0].digest == "sha"
    assert loaded[0].size_vram == 456


def test_ensure_loaded_is_idempotent_and_posts_bounded_keep_alive(monkeypatch):
    calls = []
    responses = iter([
        {"models": [{"name": "qwen3.5:9b"}]},
        {"models": []},
        {"done": True},
        {"models": [{"name": "qwen3.5:9b", "size_vram": 999}]},
        {"models": [{"name": "qwen3.5:9b", "size_vram": 999}]},
        {"models": [{"name": "qwen3.5:9b"}]},
        {"models": [{"name": "qwen3.5:9b", "size_vram": 999}]},
    ])

    def fake_open(request, timeout):
        calls.append((request.method, request.full_url, json.loads(request.data) if request.data else None, timeout))
        return _Response(next(responses))

    monkeypatch.setattr("src.dev_agent.providers.ollama.lifecycle.urlopen_no_redirect", fake_open)
    manager = OllamaModelManager(poll_interval_seconds=0, clock=lambda: 0.0)
    manager.ensure_loaded("qwen3.5:9b", keep_alive="10m", deadline_seconds=2)
    manager.ensure_loaded("qwen3.5:9b", keep_alive="10m", deadline_seconds=2)

    assert calls[0][0] == "GET"
    assert calls[1][0] == "GET"
    assert calls[2][0] == "POST"
    assert calls[2][2] == {
        "model": "qwen3.5:9b",
        "prompt": "__dev_agent_lifecycle_probe__",
        "stream": True,
        "keep_alive": "10m",
        "options": {"num_predict": 1},
        "think": False,
    }
    assert calls[2][3] == 2.0
    assert sum(call[0] == "POST" for call in calls) == 1


def test_unload_refuses_active_requests_and_is_idempotent(monkeypatch):
    responses = iter([
        {"models": [{"name": "qwen3.5:9b"}]},
        {"models": [{"name": "qwen3.5:9b"}]},
        {"models": [{"name": "qwen3.5:9b"}]},
        {"done": True},
        {"models": []},
        {"models": []},
    ])
    monkeypatch.setattr(
        "src.dev_agent.providers.ollama.lifecycle.urlopen_no_redirect",
        lambda request, timeout: _Response(next(responses)),
    )
    manager = OllamaModelManager(poll_interval_seconds=0, clock=lambda: 0.0)
    manager.acquire("qwen3.5:9b")
    with pytest.raises(OllamaLifecycleError, match="active requests"):
        manager.unload("qwen3.5:9b")
    manager.release("qwen3.5:9b")
    manager.unload("qwen3.5:9b")
    manager.unload("qwen3.5:9b")


def test_manager_rejects_non_loopback_endpoint():
    with pytest.raises(ValueError, match="loopback"):
        OllamaModelManager(base_url="http://192.168.1.5:11434")
