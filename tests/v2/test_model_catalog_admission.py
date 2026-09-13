from datetime import datetime, timezone
import json

import pytest

from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.model_benchmarks import BenchmarkCatalog
from src.dev_agent.resources.model_capabilities import ModelCapabilityCatalog
from src.dev_agent.resources.model_catalog import ModelAliasCatalog, ModelCatalog
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest
from scripts.refresh_model_catalog import refresh, write_candidate


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def _model_catalog(*, expires_at: str = "2026-09-15T00:00:00+00:00") -> ModelCatalog:
    return ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "gemini",
                    "provider_binding_id": "gemini:slot-a",
                    "model_id": "gemini-3.8-flash",
                    "source": "gemini.models.list",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "expires_at": expires_at,
                }
            ],
        }
    )


def _aliases() -> ModelAliasCatalog:
    return ModelAliasCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.8-flash",
                    "canonical_model_id": "google/gemini-3.8-flash",
                }
            ],
        }
    )


def _benchmarks() -> BenchmarkCatalog:
    return BenchmarkCatalog.from_document(
        {
            "schema_version": 1,
            "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60},
            "entries": [
                {
                    "canonical_model_id": "google/gemini-3.8-flash",
                    "benchmark": "artificial_analysis_intelligence_index",
                    "benchmark_version": "4.3",
                    "model_version": "gemini-3.8-flash",
                    "source": "openrouter.models.api",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "expires_at": "2026-09-21T00:00:00+00:00",
                    "raw_score": 41.2,
                    "normalized_score": 41.2,
                    "confidence": "high",
                    "task_fit": {"planning": 41.1, "coding": 76.3, "review": 41.2, "writing": 41.2},
                }
            ],
        }
    )


def _capabilities() -> ModelCapabilityCatalog:
    return ModelCapabilityCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.8-flash",
                    "capabilities": ["text", "structured_output", "json", "long_context"],
                    "source": "gemini.models.list+adapter_contract",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "expires_at": "2026-09-21T00:00:00+00:00",
                }
            ],
        }
    )


def test_model_admission_requires_an_exact_discovered_alias_and_current_evidence():
    resolver = ModelAdmissionResolver(_model_catalog(), _aliases(), _benchmarks(), _capabilities())

    admitted = resolver.resolve("gemini", "gemini:slot-a", "gemini-3.8-flash", now=NOW)

    assert admitted is not None
    assert admitted.canonical_model_id == "google/gemini-3.8-flash"
    assert admitted.intelligence_tier == "L2"
    assert admitted.task_fit["coding"] == 76.3
    assert admitted.capabilities == frozenset({"text", "structured_output", "json", "long_context"})
    assert resolver.resolve("gemini", "gemini:slot-a", "models/gemini-3.8-flash", now=NOW) is None
    assert resolver.resolve("gemini", "gemini:slot-b", "gemini-3.8-flash", now=NOW) is None


def test_model_admission_fails_closed_when_discovery_or_benchmark_evidence_is_not_current():
    expired_discovery = ModelAdmissionResolver(
        _model_catalog(expires_at="2026-09-14T11:59:59+00:00"),
        _aliases(),
        _benchmarks(),
        _capabilities(),
    )
    missing_benchmark = ModelAdmissionResolver(
        _model_catalog(),
        _aliases(),
        BenchmarkCatalog.from_document(
            {"schema_version": 1, "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60}, "entries": []}
        ),
        _capabilities(),
    )

    assert expired_discovery.resolve("gemini", "gemini:slot-a", "gemini-3.8-flash", now=NOW) is None
    assert missing_benchmark.resolve("gemini", "gemini:slot-a", "gemini-3.8-flash", now=NOW) is None


def test_model_admission_rejects_a_discovered_model_without_an_exact_canonical_alias():
    aliases = ModelAliasCatalog.from_document({"schema_version": 1, "entries": []})
    resolver = ModelAdmissionResolver(_model_catalog(), aliases, _benchmarks(), _capabilities())

    assert resolver.resolve("gemini", "gemini:slot-a", "gemini-3.8-flash", now=NOW) is None


def test_benchmark_tier_is_derived_from_snapshot_thresholds_not_the_model_name():
    benchmarks = _benchmarks()

    score = benchmarks.lookup("google/gemini-3.8-flash", now=NOW)

    assert score is not None
    assert benchmarks.tier_for(score) == "L2"


def test_benchmark_catalog_aggregates_multiple_normalized_sources_without_conflating_task_fit():
    benchmarks = BenchmarkCatalog.from_document(
        {
            "schema_version": 1,
            "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60},
            "entries": [
                {
                    "canonical_model_id": "test/model",
                    "benchmark": "global-index",
                    "benchmark_version": "1",
                    "model_version": "v1",
                    "source": "source-a",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "expires_at": "2026-09-15T00:00:00+00:00",
                    "raw_score": 40,
                    "normalized_score": 40,
                    "confidence": "high",
                    "task_fit": {"planning": 65},
                },
                {
                    "canonical_model_id": "test/model",
                    "benchmark": "coding-index",
                    "benchmark_version": "2",
                    "model_version": "v1",
                    "source": "source-b",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "expires_at": "2026-09-15T00:00:00+00:00",
                    "raw_score": 60,
                    "normalized_score": 60,
                    "confidence": "high",
                    "task_fit": {"coding": 90},
                },
            ],
        }
    )

    score = benchmarks.lookup("test/model", now=NOW)

    assert score is not None
    assert score.normalized_score == 50
    assert score.task_fit == {"planning": 65.0, "coding": 90.0}
    assert score.source_count == 2


@pytest.mark.parametrize(
    ("provider_id", "document", "expected_model"),
    (
        ("gemini", {"models": [{"name": "models/gemini-3.8-flash"}]}, "gemini-3.8-flash"),
        ("cloudflare", {"result": [{"name": "@cf/zai-org/glm-4.7-flash"}]}, "@cf/zai-org/glm-4.7-flash"),
        ("openrouter", {"data": [{"id": "google/gemini-3.8-flash"}]}, "google/gemini-3.8-flash"),
        ("groq", {"data": [{"id": "qwen/qwen3.8-27b"}]}, "qwen/qwen3.8-27b"),
        ("mistral", {"data": [{"id": "mistral-large-latest"}]}, "mistral-large-latest"),
        ("sambanova", {"data": [{"id": "gpt-oss-120b"}]}, "gpt-oss-120b"),
        ("ollama", {"models": [{"model": "qwen3:8b"}]}, "qwen3:8b"),
        ("ollama_cloud", {"models": [{"model": "gpt-oss:120b"}]}, "gpt-oss:120b"),
        ("vercel", {"data": [{"id": "google/gemini-3.8-flash"}]}, "google/gemini-3.8-flash"),
    ),
)
def test_provider_model_discovery_normalizes_documented_list_shapes_without_promoting_models(
    provider_id, document, expected_model
):
    captured = {}

    def fake_get(url, headers, timeout_seconds):
        captured.update({"url": url, "headers": dict(headers), "timeout_seconds": timeout_seconds})
        return document

    discovery = ProviderModelDiscovery(secret_getter=lambda name: "test-secret")
    result = discovery.discover(
        ModelDiscoveryBinding(
            provider_id=provider_id,
            provider_binding_id=f"{provider_id}:catalog",
            api_key_env="TEST_KEY" if provider_id not in {"ollama", "vercel"} else None,
            account_id_env="TEST_ACCOUNT" if provider_id == "cloudflare" else None,
        ),
        now=NOW,
        http_get=fake_get,
    )

    assert [entry.model_id for entry in result.entries] == [expected_model]
    assert result.entries[0].provider_binding_id == f"{provider_id}:catalog"
    assert result.entries[0].source.endswith("models.list")
    assert "test-secret" not in str(result.to_document())
    assert captured["timeout_seconds"] > 0


def test_provider_model_discovery_rejects_unknown_provider_and_malformed_model_document():
    discovery = ProviderModelDiscovery(secret_getter=lambda name: "test-secret")

    with pytest.raises(ValueError, match="unsupported provider"):
        discovery.discover(
            ModelDiscoveryBinding(provider_id="unknown", provider_binding_id="unknown:catalog"),
            now=NOW,
            http_get=lambda *_: {},
        )
    with pytest.raises(ValueError, match="model list"):
        discovery.discover(
            ModelDiscoveryBinding(provider_id="gemini", provider_binding_id="gemini:catalog", api_key_env="TEST_KEY"),
            now=NOW,
            http_get=lambda *_: {"models": [{"name": ""}]},
        )


def test_catalog_refresh_merges_only_typed_discovery_entries_and_never_serializes_credentials():
    class _Discovery:
        def discover(self, binding):
            from src.dev_agent.providers.model_discovery import DiscoveredModel, ModelDiscoveryResult

            return ModelDiscoveryResult(
                (
                    DiscoveredModel(
                        provider_id=binding.provider_id,
                        provider_binding_id=binding.provider_binding_id,
                        model_id="model-b",
                        source="fixture.models.list",
                        observed_at="2026-09-14T00:00:00+00:00",
                        expires_at="2026-09-15T00:00:00+00:00",
                    ),
                )
            )

    document = refresh(
        (
            ModelDiscoveryBinding(provider_id="gemini", provider_binding_id="gemini:slot", api_key_env="TEST_KEY"),
        ),
        discovery=_Discovery(),
    )

    assert document["entries"][0]["model_id"] == "model-b"
    assert "TEST_KEY" not in json.dumps(document)


def test_catalog_refresh_records_a_bounded_sanitized_failure_without_discarding_other_bindings():
    class _Discovery:
        def discover(self, binding):
            if binding.provider_id == "bad":
                raise RuntimeError("endpoint echoed credential-like data")
            from src.dev_agent.providers.model_discovery import DiscoveredModel, ModelDiscoveryResult

            return ModelDiscoveryResult(
                (
                    DiscoveredModel(
                        provider_id=binding.provider_id,
                        provider_binding_id=binding.provider_binding_id,
                        model_id="model-a",
                        source="fixture.models.list",
                        observed_at="2026-09-14T00:00:00+00:00",
                        expires_at="2026-09-15T00:00:00+00:00",
                    ),
                )
            )

    document = refresh(
        (
            ModelDiscoveryBinding(provider_id="good", provider_binding_id="good:catalog"),
            ModelDiscoveryBinding(provider_id="bad", provider_binding_id="bad:catalog"),
        ),
        discovery=_Discovery(),
    )

    assert [entry["provider_id"] for entry in document["entries"]] == ["good"]
    assert document["discovery_failures"] == [
        {"provider_id": "bad", "provider_binding_id": "bad:catalog", "category": "RuntimeError"}
    ]
    assert "credential-like" not in json.dumps(document)


def test_catalog_candidate_writer_is_create_only_until_operator_explicitly_replaces(tmp_path):
    output = tmp_path / "candidate.json"
    write_candidate(output, {"schema_version": 1, "entries": []})

    with pytest.raises(FileExistsError, match="replace_existing"):
        write_candidate(output, {"schema_version": 1, "entries": [{"model_id": "new"}]})

    write_candidate(output, {"schema_version": 1, "entries": [{"model_id": "new"}]}, replace_existing=True)
    assert json.loads(output.read_text(encoding="utf-8"))["entries"][0]["model_id"] == "new"


def test_operator_configured_gemini_free_slots_have_exact_billing_profiles_for_discovered_core_model():
    profile = profile_for("gemini", "gemini:worker:free-2", "gemini-3.8-flash")

    assert profile is not None
    assert profile.billing_mode == "recurring_allowance"
    assert profile.overage_policy == "hard_stop"
    assert profile.no_charge_guaranteed is True
    assert profile.intelligence_tier is None


def test_router_uses_exact_model_admission_for_tier_capability_and_task_fit(tmp_path):
    catalog = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "fake",
                    "provider_binding_id": "fake:planner",
                    "model_id": "planner-model",
                    "source": "fake.models.list",
                    "observed_at": "2026-09-13T00:00:00+00:00",
                    "expires_at": "2026-09-14T00:00:00+00:00",
                }
            ],
        }
    )
    aliases = ModelAliasCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [{"provider_id": "fake", "model_id": "planner-model", "canonical_model_id": "test/planner"}],
        }
    )
    benchmarks = BenchmarkCatalog.from_document(
        {
            "schema_version": 1,
            "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60},
            "entries": [
                {
                    "canonical_model_id": "test/planner",
                    "benchmark": "fixture",
                    "benchmark_version": "1",
                    "model_version": "1",
                    "source": "fixture",
                    "observed_at": "2026-09-13T00:00:00+00:00",
                    "expires_at": "2026-09-14T00:00:00+00:00",
                    "raw_score": 40,
                    "normalized_score": 40,
                    "confidence": "high",
                    "task_fit": {"planning": 70},
                }
            ],
        }
    )
    capabilities = ModelCapabilityCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "fake",
                    "model_id": "planner-model",
                    "capabilities": ["text", "structured_output"],
                    "source": "fixture",
                    "observed_at": "2026-09-13T00:00:00+00:00",
                    "expires_at": "2026-09-14T00:00:00+00:00",
                }
            ],
        }
    )
    admission = ModelAdmissionResolver(catalog, aliases, benchmarks, capabilities)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "fake:planner",
            provider_id="fake",
            provider_binding_id="fake:planner",
            native_unit="request",
            capacity=1,
            capabilities=["text", "structured_output", "tool_call"],
            cost_minor=0,
            intelligence_tier="L1",
            metadata={"model_id": "planner-model", "provider_binding_id": "fake:planner"},
        )
        ledger.observe("fake:planner", available=1, health="healthy", concurrency_limit=1)
        router = ResourceRouter(ledger, model_admission_resolver=admission)

        assert router.choose(
            RouteRequest(
                capabilities={"structured_output"},
                allowed_intelligence_tiers={"L2"},
                task_fit="planning",
                minimum_task_fit_score=60,
            )
        ).resource_id == "fake:planner"
        with pytest.raises(NoRoute):
            router.choose(RouteRequest(capabilities={"tool_call"}))
        with pytest.raises(NoRoute):
            router.choose(RouteRequest(task_fit="planning", minimum_task_fit_score=71))


def test_model_evidence_catalog_loads_the_four_separate_snapshot_layers(tmp_path):
    (tmp_path / "model_catalog_snapshot.json").write_text(
        json.dumps(_model_catalog().to_document()), encoding="utf-8"
    )
    (tmp_path / "model_alias_catalog.json").write_text(
        json.dumps(_aliases().to_document()), encoding="utf-8"
    )
    (tmp_path / "model_benchmark_snapshot.json").write_text(
        json.dumps(_benchmarks().to_document()), encoding="utf-8"
    )
    (tmp_path / "model_capability_snapshot.json").write_text(
        json.dumps(_capabilities().to_document()), encoding="utf-8"
    )

    evidence = ModelEvidenceCatalog.load(tmp_path)

    assert evidence.resolver.resolve("gemini", "gemini:slot-a", "gemini-3.8-flash", now=NOW) is not None
