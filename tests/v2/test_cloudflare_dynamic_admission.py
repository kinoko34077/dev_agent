from datetime import datetime, timezone

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.operation import configured_provider_pool_from_environment
from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.model_candidates import materialize_provider_bindings
from src.dev_agent.resources.model_catalog import ModelCatalog, ModelCatalogEntry
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.qualification import QualificationResolver


NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def test_cloudflare_discovery_preserves_bounded_task_and_tags():
    discovery = ProviderModelDiscovery(
        secret_getter=lambda name: {
            "CLOUDFLARE_API_TOKEN": "token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
        }.get(name)
    )
    result = discovery.discover(
        ModelDiscoveryBinding(
            provider_id="cloudflare",
            provider_binding_id="cloudflare:account",
            api_key_env="CLOUDFLARE_API_TOKEN",
            account_id_env="CLOUDFLARE_ACCOUNT_ID",
        ),
        now=NOW,
        http_get=lambda *_: {
            "success": True,
            "result": [
                {
                    "id": "uuid",
                    "name": "@cf/example/model",
                    "description": "must not persist",
                    "task": {"id": "text-generation", "name": "Text Generation", "description": "drop"},
                    "tags": [
                        {"name": "reasoning", "description": "drop"},
                        {"name": "tools"},
                    ],
                }
            ],
        },
    )

    entry = result.entries[0]
    assert entry.model_id == "@cf/example/model"
    assert dict(entry.metadata) == {
        "task_name": "Text Generation",
        "tags": ["reasoning", "tools"],
    }
    assert "description" not in str(result.to_document())


def test_cloudflare_catalog_filter_excludes_non_text_and_guard_models():
    def entry(model_id, metadata):
        return ModelCatalogEntry(
            provider_id="cloudflare",
            provider_binding_id="cloudflare:account",
            model_id=model_id,
            source="cloudflare.models.list",
            observed_at="2026-10-03T00:00:00+00:00",
            expires_at="2026-10-10T00:00:00+00:00",
            metadata=metadata,
        )

    assert entry("@cf/example/chat", {"task_name": "Text Generation"}).is_text_generation_candidate()
    assert not entry("@cf/example/image", {"task_name": "Text-to-Image"}).is_text_generation_candidate()
    assert not entry(
        "@cf/meta/llama-guard-3-8b",
        {"task_name": "Text Generation", "tags": ["moderation", "safety"]},
    ).is_text_generation_candidate()


def test_cloudflare_dynamic_billing_policy_allows_free_and_denies_paid_required_models():
    free_profile = profile_for(
        "cloudflare",
        "cloudflare:account",
        "@cf/zai-org/glm-4.7-flash",
    )
    assert free_profile is not None
    assert free_profile.no_charge_guaranteed is True
    assert free_profile.billing_mode == "recurring_allowance"
    assert free_profile.overage_policy == "hard_stop"

    assert profile_for(
        "cloudflare",
        "cloudflare:account",
        "@cf/zai-org/glm-5.3",
    ) is None


def test_configured_cloudflare_uses_dynamic_discovery_unless_operator_pins_model():
    base = {
        "CLOUDFLARE_API_TOKEN": "token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
    }

    dynamic = next(
        item
        for item in configured_provider_pool_from_environment(base.get)
        if item.provider_id == "cloudflare"
    )
    assert dynamic.provider_binding_id == "cloudflare:account"
    assert dynamic.credential_binding_id == "cloudflare:account"
    assert dynamic.expand_discovered_models is True

    pinned_env = dict(base)
    pinned_env["CLOUDFLARE_MODEL"] = "@cf/zai-org/glm-4.7-flash"
    pinned = next(
        item
        for item in configured_provider_pool_from_environment(pinned_env.get)
        if item.provider_id == "cloudflare"
    )
    assert pinned.model == "@cf/zai-org/glm-4.7-flash"
    assert pinned.expand_discovered_models is False


def test_cloudflare_candidate_materialization_keeps_credential_lane_exact():
    binding = next(
        item
        for item in configured_provider_pool_from_environment(
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            }.get
        )
        if item.provider_id == "cloudflare"
    )
    catalog = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "cloudflare",
                    "provider_binding_id": "cloudflare:account",
                    "model_id": "@cf/zai-org/glm-4.7-flash",
                    "source": "cloudflare.models.list",
                    "observed_at": "2026-10-03T00:00:00+00:00",
                    "expires_at": "2026-10-10T00:00:00+00:00",
                    "metadata": {"task_name": "Text Generation"},
                },
                {
                    "provider_id": "cloudflare",
                    "provider_binding_id": "cloudflare:account",
                    "model_id": "@cf/example/image",
                    "source": "cloudflare.models.list",
                    "observed_at": "2026-10-03T00:00:00+00:00",
                    "expires_at": "2026-10-10T00:00:00+00:00",
                    "metadata": {"task_name": "Text-to-Image"},
                },
            ],
        }
    )

    expanded = materialize_provider_bindings(
        binding,
        catalog,
        expand_discovered_models=True,
        now=NOW,
    )
    glm = next(item for item in expanded if item.model == "@cf/zai-org/glm-4.7-flash")
    assert glm.binding_id != glm.credential_binding_id
    assert glm.credential_binding_id == "cloudflare:account"
    assert all(item.model != "@cf/example/image" for item in expanded)


def test_current_cloudflare_free_candidates_have_conservative_neuron_rates():
    assert CloudflareWorkersAIHttpProvider.neurons_for_usage(
        "@cf/meta/llama-3.1-8b-instruct-fp8", 1_000_000, 1_000_000
    ) == 39_906
    assert CloudflareWorkersAIHttpProvider.neurons_for_usage(
        "@cf/zai-org/glm-4.7-flash", 1_000_000, 1_000_000
    ) == 41_900
    assert CloudflareWorkersAIHttpProvider.neurons_for_usage(
        "@cf/google/gemma-4-26b-a4b-it", 1_000_000, 1_000_000
    ) == 36_364
    assert CloudflareWorkersAIHttpProvider.neurons_for_usage(
        "@cf/nvidia/nemotron-3-120b-a12b", 1_000_000, 1_000_000
    ) == 181_819


def test_cloudflare_decoder_accepts_current_chat_completion_result_shape():
    request = ModelRequest(messages=[{"role": "user", "content": "ready"}])
    response = CloudflareWorkersAIHttpProvider._decode(
        {
            "success": True,
            "result": {
                "model": "@cf/zai-org/glm-4.7-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ready"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "neurons": 1.25,
                },
            },
        },
        request,
        "@cf/zai-org/glm-4.7-flash",
    )

    assert response.text_segments == ["ready"]
    assert response.model == "@cf/zai-org/glm-4.7-flash"
    assert response.usage["quota_observation"]["consumed"] == 2
    assert response.usage["quota_observation"]["consumption_authority"] == "authoritative_provider"
    assert response.usage["quota_observation"]["quota_authority"] == "derived_conservative"


def test_current_canonical_cloudflare_catalog_materializes_only_exact_qualified_free_candidates():
    binding = next(
        item
        for item in configured_provider_pool_from_environment(
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            }.get
        )
        if item.provider_id == "cloudflare"
    )
    catalog = ModelEvidenceCatalog.load_default().catalog
    qualification = QualificationResolver()
    admitted = []
    for candidate in materialize_provider_bindings(
        binding,
        catalog,
        expand_discovered_models=True,
        now=NOW,
    ):
        evidence_binding = candidate.credential_binding_id
        if catalog.lookup(candidate.provider_id, evidence_binding, candidate.model, now=NOW) is None:
            continue
        if qualification.resolve(
            candidate.provider_id,
            evidence_binding,
            candidate.model,
            now=NOW,
        ) is None:
            continue
        profile = profile_for(candidate.provider_id, evidence_binding, candidate.model)
        if profile is None or not profile.no_charge_guaranteed:
            continue
        admitted.append(candidate.model)

    assert set(admitted) == {
        "@cf/zai-org/glm-4.7-flash",
        "@cf/google/gemma-4-26b-a4b-it",
        "@cf/nvidia/nemotron-3-120b-a12b",
    }
