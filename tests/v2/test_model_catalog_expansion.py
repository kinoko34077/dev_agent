from datetime import datetime, timezone

import pytest

from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.providers.benchmark_discovery import (
    benchmark_scores_from_document,
    parse_benchmark_document,
)
from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from src.dev_agent.resources.model_candidates import materialize_provider_bindings
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.model_evidence_builder import (
    build_alias_document,
    build_capability_document,
)
from src.dev_agent.resources.model_catalog import ModelCatalog, ModelCatalogError
from src.dev_agent.resources.router import ResourceRouter, RouteRequest
from src.dev_agent.resources.ledger import ResourceLedger


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def _catalog_document():
    return {
        "schema_version": 1,
        "entries": [
            {
                "provider_id": "gemini",
                "provider_binding_id": "gemini:project-a",
                "model_id": "gemini-3.7-flash",
                "source": "gemini.models.list",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "expires_at": "2026-09-21T00:00:00+00:00",
                "metadata": {
                    "display_name": "Gemini 3.7 Flash",
                    "version": "3.0",
                    "input_token_limit": 1048576,
                    "output_token_limit": 65536,
                    "supported_generation_methods": ["generateContent", "countTokens"],
                    "thinking_supported": True,
                },
            },
            {
                "provider_id": "gemini",
                "provider_binding_id": "gemini:project-a",
                "model_id": "gemini-2.5-flash-tts",
                "source": "gemini.models.list",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "expires_at": "2026-09-21T00:00:00+00:00",
                "metadata": {
                    "display_name": "Gemini 2.5 Flash TTS",
                    "supported_generation_methods": ["generateContent"],
                },
            },
            {
                "provider_id": "gemini",
                "provider_binding_id": "gemini:project-a",
                "model_id": "gemini-2.5-flash",
                "source": "gemini.models.list",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "expires_at": "2026-09-21T00:00:00+00:00",
                "metadata": {
                    "display_name": "Gemini 2.5 Flash",
                    "input_token_limit": 1048576,
                    "supported_generation_methods": ["generateContent", "countTokens"],
                },
            },
            {
                "provider_id": "gemini",
                "provider_binding_id": "gemini:project-b",
                "model_id": "gemini-3.7-flash",
                "source": "gemini.models.list",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "expires_at": "2026-09-21T00:00:00+00:00",
                "metadata": {
                    "display_name": "Gemini 3.7 Flash",
                    "input_token_limit": 1048576,
                    "supported_generation_methods": ["generateContent"],
                },
            },
        ],
    }


def test_gemini_discovery_preserves_bounded_metadata_and_catalog_round_trip():
    discovery = ProviderModelDiscovery(secret_getter=lambda _name: "secret")
    result = discovery.discover(
        ModelDiscoveryBinding(
            provider_id="gemini",
            provider_binding_id="gemini:project-a",
            api_key_env="GEMINI_API_KEY",
        ),
        now=NOW,
        http_get=lambda *_args: {
            "models": [
                {
                    "name": "models/gemini-3.7-flash",
                    "displayName": "Gemini 3.7 Flash",
                    "version": "3.0",
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536,
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                    "thinking": True,
                    "description": "must not be copied into the snapshot",
                }
            ]
        },
    )

    entry = result.entries[0]
    assert entry.model_id == "gemini-3.7-flash"
    assert entry.metadata["input_token_limit"] == 1048576
    assert entry.metadata["supported_generation_methods"] == ["generateContent", "countTokens"]
    assert entry.metadata["thinking_supported"] is True
    assert "description" not in entry.metadata
    catalog = ModelCatalog.from_document(result.to_document())
    assert catalog.lookup("gemini", "gemini:project-a", "gemini-3.7-flash", now=NOW) is not None


def test_openrouter_discovery_preserves_nested_architecture_modalities():
    discovery = ProviderModelDiscovery(secret_getter=lambda _name: "secret")
    result = discovery.discover(
        ModelDiscoveryBinding(
            provider_id="openrouter",
            provider_binding_id="openrouter:account",
            api_key_env="OPENROUTER_API_KEY",
        ),
        now=NOW,
        http_get=lambda *_args: {
            "data": [
                {
                    "id": "provider/text-model",
                    "context_length": 131072,
                    "architecture": {
                        "modality": "text->text",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                    "supported_parameters": ["max_tokens"],
                }
            ]
        },
    )

    assert result.entries[0].metadata["modality"] == "text->text"
    assert result.entries[0].metadata["input_modalities"] == ["text"]
    assert result.entries[0].metadata["output_modalities"] == ["text"]


def test_model_evidence_builder_expands_aliases_and_derives_only_safe_capabilities():
    catalog = ModelCatalog.from_document(_catalog_document())

    aliases = build_alias_document(catalog, now=NOW)
    alias_pairs = {(entry["provider_id"], entry["model_id"], entry["canonical_model_id"]) for entry in aliases["entries"]}
    assert ("gemini", "gemini-3.7-flash", "google/gemini-3.7-flash") in alias_pairs
    assert ("gemini", "gemini-2.5-flash", "google/gemini-2.5-flash") in alias_pairs
    assert all("tts" not in entry["model_id"] for entry in aliases["entries"])

    capabilities = build_capability_document(catalog, now=NOW)
    by_model = {entry["model_id"]: entry for entry in capabilities["entries"]}
    assert by_model["gemini-3.7-flash"]["capabilities"] == ["long_context", "text"]
    assert by_model["gemini-2.5-flash"]["capabilities"] == ["long_context", "text"]
    assert "structured_output" not in by_model["gemini-3.7-flash"]["capabilities"]
    assert "gemini-2.5-flash-tts" not in by_model


def test_discovery_expansion_materializes_distinct_execution_bindings_for_one_credential():
    catalog = ModelCatalog.from_document(_catalog_document())
    binding = OperationProviderBinding(
        provider_id="gemini",
        provider_binding_id="gemini:project-a",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        quota_domain="gemini:project:a",
    )

    candidates = materialize_provider_bindings(binding, catalog, expand_discovered_models=True, now=NOW)

    assert [candidate.model for candidate in candidates] == ["gemini-2.5-flash", "gemini-3.7-flash"]
    assert len({candidate.binding_id for candidate in candidates}) == 2
    assert {candidate.qualification_binding_id for candidate in candidates} == {"gemini:project-a"}
    assert all(candidate.binding_id != candidate.qualification_binding_id for candidate in candidates)


def test_benchmark_discovery_uses_exact_or_date_suffixed_model_identity_only():
    document = {
        "data": [
            {
                "source": "artificial-analysis",
                "model_permaslug": "google/gemini-3.7-flash-20260813",
                "display_name": "Gemini 3.7 Flash",
                "intelligence_index": 39.4,
                "coding_index": 76.1,
                "agentic_index": 36.4,
                "meta": {"version": "2026-08"},
            },
            {
                "source": "artificial-analysis",
                "model_permaslug": "google/gemini-unknown-20260813",
                "intelligence_index": 33.0,
            },
        ]
    }
    records = parse_benchmark_document(document, observed_at=NOW)
    assert len(records) == 2
    scores = benchmark_scores_from_document(
        document,
        canonical_model_ids={"google/gemini-3.7-flash"},
        observed_at=NOW,
    )
    assert len(scores) == 1
    assert scores[0]["canonical_model_id"] == "google/gemini-3.7-flash"
    assert scores[0]["model_version"] == "google/gemini-3.7-flash-20260813"
    assert scores[0]["task_fit"]["planning"] == 36.4
    assert scores[0]["task_fit"]["coding"] == 76.1


def test_benchmark_discovery_rejects_ambiguous_date_suffixed_mapping():
    document = {
        "data": [
            {
                "source": "artificial-analysis",
                "model_permaslug": "vendor/model-20260813",
                "intelligence_index": 31.0,
            }
        ]
    }
    with pytest.raises(ModelCatalogError, match="ambiguous"):
        benchmark_scores_from_document(
            document,
            canonical_model_ids={"vendor/model-20260812", "vendor/model-20260814"},
            observed_at=NOW,
        )


def test_model_admission_diagnostic_identifies_the_first_missing_evidence_layer():
    catalog = ModelCatalog.from_document(_catalog_document())
    aliases = {
        "schema_version": 1,
        "entries": [
            {
                "provider_id": "gemini",
                "model_id": "gemini-3.7-flash",
                "canonical_model_id": "google/gemini-3.7-flash",
            }
        ],
    }
    from src.dev_agent.resources.model_catalog import ModelAliasCatalog
    from src.dev_agent.resources.model_benchmarks import BenchmarkCatalog
    from src.dev_agent.resources.model_capabilities import ModelCapabilityCatalog

    resolver = ModelAdmissionResolver(
        catalog,
        ModelAliasCatalog.from_document(aliases),
        BenchmarkCatalog.from_document(
            {
                "schema_version": 1,
                "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60},
                "entries": [],
            }
        ),
        ModelCapabilityCatalog.from_document({"schema_version": 1, "entries": []}),
    )

    diagnostic = resolver.diagnose("gemini", "gemini:project-a", "gemini-3.7-flash", now=NOW)

    assert diagnostic.discovery == "PASS"
    assert diagnostic.alias == "PASS"
    assert diagnostic.benchmark == "MISSING"
    assert diagnostic.result == "BENCHMARK_MISSING"
    assert resolver.resolve("gemini", "gemini:project-a", "gemini-3.7-flash", now=NOW) is None


def test_planner_pool_can_expand_one_credential_binding_into_multiple_admitted_models(monkeypatch):
    import scripts.devfarm_planner_shadow as planner_shadow
    from types import SimpleNamespace

    catalog = ModelCatalog.from_document(_catalog_document())

    class _QualificationResolver:
        def resolve(self, provider_id, binding_id, model_id, *, min_confidence="high", **_kwargs):
            if min_confidence != "high" or binding_id != "gemini:project-a":
                return None
            return SimpleNamespace(intelligence_tier="L2", routing_capabilities=frozenset({"text"}))

    class _ModelResolver:
        def __init__(self):
            self.catalog = catalog

        def resolve(self, provider_id, binding_id, model_id, **_kwargs):
            if model_id not in {"gemini-2.5-flash", "gemini-3.7-flash"}:
                return None
            return SimpleNamespace(
                intelligence_tier="L2",
                capabilities=frozenset({"text"}),
                task_fit={"planning": 50.0},
            )

    profile = SimpleNamespace(no_charge_guaranteed=True)
    monkeypatch.setattr(planner_shadow, "profile_for", lambda *_args: profile)
    candidates = planner_shadow.admit_planner_pool(
        (
            OperationProviderBinding(
                provider_id="gemini",
                provider_binding_id="gemini:project-a",
                model="gemini-2.5-flash",
                api_key_env="GEMINI_API_KEY",
                quota_domain="gemini:project:a",
            ),
        ),
        resolver=_QualificationResolver(),
        model_admission_resolver=_ModelResolver(),
        model_catalog=catalog,
        expand_discovered_models=True,
        now=NOW,
    )

    assert [binding.model for binding, _qualification, _profile in candidates] == [
        "gemini-2.5-flash",
        "gemini-3.7-flash",
    ]
    assert len({binding.binding_id for binding, _qualification, _profile in candidates}) == 2


def test_planner_shadow_rechecks_expanded_binding_against_credential_evidence(monkeypatch):
    """Expanded execution identities must retain the qualification identity."""

    import scripts.devfarm_planner_shadow as planner_shadow
    import src.dev_agent.resources.router as router_module
    from src.dev_agent.domain.protocol import ModelResponse
    from types import SimpleNamespace

    catalog = ModelCatalog.from_document(_catalog_document())
    parent_task_id = "00000000-0000-4000-8000-000000000001"
    base_binding = OperationProviderBinding(
        provider_id="fake",
        provider_binding_id="fake:project-a",
        model="model-a",
        api_key_env="FAKE_API_KEY",
        quota_domain="fake:project:a",
    )
    # Keep this test focused on the expanded-identity handoff rather than on
    # the network-provider class authority gate used by production adapters.
    catalog = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "fake",
                    "provider_binding_id": "fake:project-a",
                    "model_id": model,
                    "source": "test",
                    "observed_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2027-01-01T00:00:00+00:00",
                    "metadata": {"supported_generation_methods": ["generateContent"]},
                }
                for model in ("model-a", "model-b")
            ],
        }
    )

    class _QualificationResolver:
        def resolve(self, provider_id, binding_id, model_id, *, min_confidence="high", **_kwargs):
            if (provider_id, binding_id, model_id, min_confidence) != (
                "fake",
                "fake:project-a",
                model_id,
                "high",
            ) or model_id not in {"model-a", "model-b"}:
                return None
            return SimpleNamespace(intelligence_tier="L2", routing_capabilities=frozenset({"text"}))

    class _ModelAdmissionResolver:
        def __init__(self):
            self.catalog = catalog

        def resolve(self, provider_id, binding_id, model_id, **_kwargs):
            if provider_id != "fake" or binding_id != "fake:project-a" or model_id not in {"model-a", "model-b"}:
                return None
            return SimpleNamespace(
                intelligence_tier="L2",
                capabilities=frozenset({"text"}),
                task_fit={"planning": 50.0},
            )

    class _PlannerProvider:
        def __init__(self, binding):
            self.provider_id = "fake"
            self.provider_binding_id = binding.binding_id
            self.model_id = binding.model

        def request(self, request):
            return ModelResponse(
                provider="fake",
                model=self.model_id,
                usage={"cost_minor": 0},
                structured_output={
                    "parent_task_id": request.task_id,
                    "rationale": "bounded test proposal",
                    "planning_cycle": 1,
                    "proposal_id": "expanded-identity-proposal",
                    "children": [
                        {
                            "child_key": "implementation",
                            "objective": "Add the focused implementation.",
                            "task_type": "worker",
                            "risk": "normal",
                            "sensitivity": "normal",
                            "required_capabilities": ["text"],
                            "dependencies": [],
                            "dependency_types": {},
                            "suggested_owner": "worker",
                        }
                    ],
                },
            )

    profile = SimpleNamespace(
        cost_minor=0,
        price_currency="JPY",
        no_charge_guaranteed=True,
        expires_at="2026-09-21T00:00:00+00:00",
        billing_mode="free_fixed",
        overage_policy="hard_stop",
        allowance_amount=None,
        allowance_currency=None,
        allowance_period=None,
    )
    providers = {}

    def _build_provider(*, binding):
        providers[binding.binding_id] = _PlannerProvider(binding)
        return providers[binding.binding_id]

    monkeypatch.setattr(planner_shadow, "QualificationResolver", _QualificationResolver)
    monkeypatch.setattr(planner_shadow, "ResourceRouter", router_module.ResourceRouter)
    monkeypatch.setattr(planner_shadow, "profile_for", lambda *_args: profile)
    monkeypatch.setattr(router_module, "profile_for", lambda *_args: profile)
    monkeypatch.setattr(router_module, "ModelAdmissionResolver", _ModelAdmissionResolver)
    monkeypatch.setattr(planner_shadow, "_build_provider", _build_provider)

    result = planner_shadow.run_shadow(
        objective="Create one bounded implementation proposal.",
        parent_task_id=parent_task_id,
        provider_id="fake",
        binding_id=base_binding.binding_id,
        model_id=base_binding.model,
        api_key_env=base_binding.api_key_env,
        quota_domain=base_binding.quota_domain,
        timeout_seconds=5,
        allow_unknown_quota=True,
        repository="example/repo",
        branch="v2/bootstrap",
        provider_pool=(base_binding,),
        model_admission_resolver=_ModelAdmissionResolver(),
        model_catalog=catalog,
        expand_discovered_models=True,
    )

    assert result["status"] == "live_shadow_validated"
    assert result["model"] in {"model-a", "model-b"}
    assert result["binding"].startswith("fake:project-a:")


def test_router_can_prefer_provider_and_model_diversity_after_hard_filters(tmp_path):
    with ResourceLedger(tmp_path / "diversity.sqlite3") as ledger:
        for resource_id, provider, binding, model in (
            ("a-1", "provider-a", "provider-a:one", "same-model"),
            ("a-2", "provider-a", "provider-a:two", "same-model"),
            ("b-1", "provider-b", "provider-b:one", "different-model"),
        ):
            ledger.register_resource(
                resource_id,
                provider_id=provider,
                provider_binding_id=binding,
                native_unit="request",
                capacity=1,
                capabilities=["text"],
                cost_minor=0,
                metadata={"model_id": model, "provider_binding_id": binding},
            )
            ledger.observe(resource_id, available=1, health="healthy", concurrency_limit=1)

        router = ResourceRouter(ledger)
        assert router.choose(RouteRequest(capabilities={"text"})).resource_id == "a-1"
        assert router.choose(RouteRequest(capabilities={"text"}, prefer_diversity=True)).resource_id == "b-1"
