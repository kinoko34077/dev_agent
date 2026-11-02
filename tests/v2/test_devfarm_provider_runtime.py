from __future__ import annotations

import pytest

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker_admission import (
    DevFarmActivationPolicy,
    WORKER_PROVIDER_CHOICES,
    validate_worker_provider,
)
from scripts.devfarm_provider_runtime import build_assigned_providers, build_worker_provider
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


def test_validate_worker_provider_rejects_unqualified_local_high_tier() -> None:
    provider = OllamaProvider(model="qwen3.5:9b", base_url="http://127.0.0.1:11434", think=False)
    provider.provider_binding_id = "ollama:local:qwen3.5-9b"
    provider.model_id = "qwen3.5:9b"
    provider.intelligence_tier = "L2"

    with pytest.raises(DevFarmError, match="tier requires explicit"):
        validate_worker_provider(provider, allow_local=True)


def test_build_worker_provider_requires_explicit_local_trial_opt_in() -> None:
    binding = "ollama:local:qwen3.5-9b"
    with pytest.raises(DevFarmError, match="not active"):
        build_worker_provider("ollama", "qwen3.5:9b", 30.0, binding)

    provider = build_worker_provider(
        "ollama",
        "qwen3.5:9b",
        30.0,
        binding,
        allow_local=True,
    )
    assert provider.provider_id == "ollama"
    assert provider.model_id == "qwen3.5:9b"
    assert provider.provider_binding_id == binding
    assert provider.intelligence_tier == "L1"
    assert provider.think is False
    assert provider.keep_alive == "10m"


@pytest.mark.current_billing_catalog
def test_build_worker_provider_admits_exact_current_free_gemini_as_proposal_candidate() -> None:
    provider = build_worker_provider(
        "gemini",
        "gemini-3.6-flash",
        30.0,
        "gemini:worker:free-3",
    )

    assert provider.provider_id == "gemini"
    assert provider.model_id == "gemini-3.6-flash"
    assert provider.provider_binding_id == "gemini:worker:free-3"
    assert provider.intelligence_tier == "L1"
    assert provider.proposal_only_candidate is True

    provider_id, model_id, binding_id, tier, eligibility = validate_worker_provider(provider)

    assert (provider_id, model_id, binding_id, tier) == (
        "gemini",
        "gemini-3.6-flash",
        "gemini:worker:free-3",
        "L1",
    )
    assert eligibility.capability_qualified is False
    assert eligibility.billing_admitted is True
    assert eligibility.reason == "proposal_only_candidate"


@pytest.mark.current_billing_catalog
def test_build_worker_provider_admits_openrouter_free_variant_as_proposal_candidate() -> None:
    provider = build_worker_provider(
        "openrouter",
        "google/gemma-4-26b-a4b-it:free",
        30.0,
        "openrouter:account",
    )

    provider_id, model_id, binding_id, tier, eligibility = validate_worker_provider(provider)

    assert (provider_id, model_id, binding_id) == (
        "openrouter",
        "google/gemma-4-26b-a4b-it:free",
        "openrouter:account",
    )
    assert tier == "L1"
    assert provider.proposal_only_candidate is True
    assert eligibility.capability_qualified is False
    assert eligibility.billing_admitted is True
    assert eligibility.reason == "proposal_only_candidate"


@pytest.mark.current_billing_catalog
@pytest.mark.parametrize(
    ("provider_id", "model_id", "binding_id"),
    (
        ("mistral", "ministral-3b-latest", "mistral:account"),
        ("vercel", "nvidia/nemotron-3.5-lightning-free", "vercel:account"),
    ),
)
def test_free_remote_worker_providers_remain_proposal_candidates(provider_id, model_id, binding_id):
    eligibility = DevFarmActivationPolicy().proposal_eligibility_for(
        provider_id,
        model_id,
        provider_binding_id=binding_id,
    )

    assert eligibility.operator_activated is True
    assert eligibility.billing_admitted is True
    assert eligibility.eligible is True
    assert eligibility.reason == "proposal_only_candidate"


@pytest.mark.current_billing_catalog
def test_ollama_cloud_model_remains_visible_but_blocked_without_exact_free_entitlement():
    eligibility = DevFarmActivationPolicy().proposal_eligibility_for(
        "ollama_cloud",
        "deepseek-v4.1-flash",
        provider_binding_id="ollama_cloud:account",
    )

    assert eligibility.operator_activated is True
    assert eligibility.billing_admitted is False
    assert eligibility.eligible is False
    assert eligibility.reason == "billing_unknown"

    with pytest.raises(DevFarmError, match="billing_unknown"):
        build_worker_provider(
            "ollama_cloud",
            "deepseek-v4.1-flash",
            30.0,
            "ollama_cloud:account",
        )


@pytest.mark.current_billing_catalog
def test_vercel_paid_catalog_model_is_not_admitted_by_free_plan_credit():
    eligibility = DevFarmActivationPolicy().proposal_eligibility_for(
        "vercel",
        "google/gemini-3.8-flash",
        provider_binding_id="vercel:account",
    )

    assert eligibility.operator_activated is True
    assert eligibility.billing_admitted is False
    assert eligibility.reason == "billing_unknown"
    with pytest.raises(DevFarmError, match="billing_unknown"):
        build_worker_provider(
            "vercel",
            "google/gemini-3.8-flash",
            30.0,
            "vercel:account",
        )


@pytest.mark.current_billing_catalog
def test_sambanova_stays_selectable_but_not_dispatchable_without_no_charge_evidence():
    eligibility = DevFarmActivationPolicy().proposal_eligibility_for(
        "sambanova",
        "Meta-Llama-3.3-70B-Instruct",
        provider_binding_id="sambanova:account",
    )

    assert eligibility.operator_activated is True
    assert eligibility.billing_admitted is False
    assert eligibility.reason == "billing_unknown"
    with pytest.raises(DevFarmError, match="billing_unknown"):
        build_worker_provider(
            "sambanova",
            "Meta-Llama-3.3-70B-Instruct",
            30.0,
            "sambanova:account",
        )


def test_worker_cli_exposes_only_the_active_free_remote_providers_and_local_trial():
    assert {"cloudflare", "gemini", "groq", "mistral", "ollama_cloud", "openrouter", "sambanova", "vercel", "ollama"} <= set(
        WORKER_PROVIDER_CHOICES
    )
    assert "deepseek" not in WORKER_PROVIDER_CHOICES


@pytest.mark.current_billing_catalog
def test_build_worker_provider_rejects_unlisted_free_candidate() -> None:
    with pytest.raises(DevFarmError, match="not dispatchable.*billing_unknown"):
        build_worker_provider(
            "gemini",
            "gemini-not-in-reviewed-catalog",
            30.0,
            "gemini:worker:free-3",
        )
