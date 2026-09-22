from __future__ import annotations

import pytest

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker_admission import validate_worker_provider
from scripts.devfarm_provider_runtime import build_assigned_providers
from src.dev_agent.providers.ollama import OllamaProvider


def test_build_assigned_providers_materializes_only_resumable_worker_tasks() -> None:
    calls: list[tuple[str, str, float, str | None]] = []

    def builder(name: str, model: str, timeout: float, binding: str | None = None) -> object:
        calls.append((name, model, timeout, binding))
        return object()

    plan = {
        "tasks": [
            {
                "task_id": "worker-ready",
                "owner": "worker",
                "status": "READY",
                "assignment": {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.6-flash",
                    "provider_binding_id": "gemini:worker:free-3",
                },
            },
            {
                "task_id": "worker-dispatched",
                "owner": "worker",
                "status": "DISPATCHED",
                "assignment": {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.6-flash",
                },
            },
            {
                "task_id": "codex-ready",
                "owner": "codex",
                "status": "READY",
                "assignment": {},
            },
        ]
    }

    providers = build_assigned_providers(plan, 30.0, provider_builder=builder)

    assert set(providers) == {"worker-ready"}
    assert calls == [("gemini", "gemini-3.6-flash", 30.0, "gemini:worker:free-3")]


def test_validate_worker_provider_rejects_an_unidentified_injected_instance() -> None:
    with pytest.raises(DevFarmError, match="provider must expose a non-empty provider_id"):
        validate_worker_provider(object())


def test_validate_worker_provider_allows_explicit_local_trial_only() -> None:
    provider = OllamaProvider(model="qwen3.5:9b", base_url="http://127.0.0.1:11434", think=False)
    provider.provider_binding_id = "ollama:local:qwen3.5-9b"
    provider.model_id = "qwen3.5:9b"
    provider.intelligence_tier = "L1"

    with pytest.raises(DevFarmError, match="not eligible"):
        validate_worker_provider(provider)

    provider_id, model_id, binding_id, tier, eligibility = validate_worker_provider(provider, allow_local=True)
    assert (provider_id, model_id, binding_id, tier) == (
        "ollama",
        "qwen3.5:9b",
        "ollama:local:qwen3.5-9b",
        "L1",
    )
    assert eligibility.reason == "local_trial"
