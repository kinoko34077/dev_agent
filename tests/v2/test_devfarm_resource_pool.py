from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.devfarm_resource_pool import ResourcePoolError, admit_resource_pool, build_provider, compose_resource_pool
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.model_benchmarks import BenchmarkCatalog
from src.dev_agent.resources.model_capabilities import ModelCapabilityCatalog
from src.dev_agent.resources.model_catalog import ModelAliasCatalog, ModelCatalog


class _QualificationResolver:
    def __init__(self, tier: str):
        self.tier = tier

    def resolve(self, _provider_id, _binding_id, _model_id, **_kwargs):
        return SimpleNamespace(
            intelligence_tier=self.tier,
            routing_capabilities=frozenset({"text"}),
        )


class _Provider:
    provider_id = "openrouter"
    provider_binding_id = "openrouter:free"
    model_id = "openrouter/free"
    intelligence_tier = "L1"

    def request(self, _request):  # pragma: no cover - composition does not call providers
        raise AssertionError("provider must not be called during pool composition")


class _RespondingProvider(_Provider):
    def request(self, _request):  # pragma: no cover - execution is injected
        raise AssertionError("the focused route test uses the dispatcher callback")


def _binding() -> OperationProviderBinding:
    return OperationProviderBinding(
        provider_id="openrouter",
        provider_binding_id="openrouter:free",
        model="openrouter/free",
        api_key_env="OPENROUTER_API_KEY",
        quota_domain="openrouter:account",
    )


def test_resource_pool_provider_preserves_host_owned_ollama_runtime_settings(monkeypatch):
    captured = {}

    def fake_create(_factory, definition):
        captured["definition"] = definition
        return object()

    monkeypatch.setattr("scripts.devfarm_resource_pool.ProviderFactory.create", fake_create)
    binding = OperationProviderBinding(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        model="qwen3.5:9b",
        base_url="http://127.0.0.1:11434",
        keep_alive="10m",
        think=False,
    )

    build_provider(binding=binding)

    definition = captured["definition"]
    assert definition.keep_alive == "10m"
    assert definition.think is False


def test_local_ollama_admission_does_not_require_cloud_quota_or_qualification():
    binding = OperationProviderBinding(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        model="qwen3.5:9b",
        base_url="http://127.0.0.1:11434",
        intelligence_tier="L1",
    )

    admitted = admit_resource_pool(
        (binding,),
        resolver=_QualificationResolver("L2"),
        required_tier="L1",
        model_admission_resolver=_missing_model_evidence_resolver(),
    )

    assert len(admitted) == 1
    assert admitted[0][0].quota_domain is None
    assert admitted[0][2].no_charge_guaranteed is True


def test_local_ollama_composition_projects_local_privacy_and_no_qualification():
    binding = OperationProviderBinding(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        model="qwen3.5:9b",
        base_url="http://127.0.0.1:11434",
        intelligence_tier="L1",
    )
    qualification = SimpleNamespace(intelligence_tier="L1", routing_capabilities=frozenset({"text"}))
    profile = profile_for("ollama", binding.binding_id, binding.model)

    with compose_resource_pool(
        ((binding, qualification, profile),),
        resolver=_QualificationResolver("L1"),
        model_admission_resolver=_missing_model_evidence_resolver(),
        provider_builder=lambda *, binding: _Provider(),
    ) as runtime:
        resource = runtime.ledger.get_resource("devfarm-shadow:ollama:local:qwen3.5-9b")

    assert resource is not None
    assert resource["metadata"]["privacy_profile"] == "local_only"
    assert resource["metadata"]["qualification_required"] is False
    assert resource["quota_domain"] is None


def _missing_model_evidence_resolver() -> ModelAdmissionResolver:
    return ModelAdmissionResolver(
        ModelCatalog.from_document({"schema_version": 1, "entries": []}),
        ModelAliasCatalog.from_document({"schema_version": 1, "entries": []}),
        BenchmarkCatalog.from_document(
            {"schema_version": 1, "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60}, "entries": []}
        ),
        ModelCapabilityCatalog.from_document({"schema_version": 1, "entries": []}),
    )


def test_composition_accepts_exact_qualified_l1_without_model_evidence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-secret")
    qualification = _QualificationResolver("L1").resolve("openrouter", "openrouter:free", "openrouter/free")
    profile = profile_for("openrouter", "openrouter:free", "openrouter/free")

    with compose_resource_pool(
        ((_binding(), qualification, profile),),
        resolver=_QualificationResolver("L1"),
        model_admission_resolver=_missing_model_evidence_resolver(),
        provider_builder=lambda *, binding: _Provider(),
    ) as runtime:
        resource = runtime.ledger.get_resource("devfarm-shadow:openrouter:free")

    assert resource is not None
    assert resource["metadata"]["intelligence_tier"] == "L1"
    assert resource["capabilities"] == ("text",)


def test_composition_routes_legacy_qualified_l1_without_model_evidence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-secret")
    qualification = _QualificationResolver("L1").resolve("openrouter", "openrouter:free", "openrouter/free")
    profile = profile_for("openrouter", "openrouter:free", "openrouter/free")
    request = ModelRequest(
        task_id=str(uuid4()),
        messages=[{"role": "user", "content": "bounded test"}],
        requested_capabilities=["text"],
        metadata={
            "intelligence_routing": "bounded",
            "allowed_intelligence_tiers": ["L1"],
            "allow_unknown_quota": True,
        },
    )

    with compose_resource_pool(
        ((_binding(), qualification, profile),),
        resolver=_QualificationResolver("L1"),
        model_admission_resolver=_missing_model_evidence_resolver(),
        provider_builder=lambda *, binding: _RespondingProvider(),
    ) as runtime:
        response = runtime.dispatcher.request_with_execution(
            request,
            execute=lambda provider, _request, _late: ModelResponse(
                provider=provider.provider_id,
                model=provider.model_id,
                parts=["ok"],
                usage={"cost_minor": 0},
            ),
        )

    assert response.provider == "openrouter"
    assert response.model == "openrouter/free"
    assert response.parts == ["ok"]


def test_composition_rejects_l2_when_model_evidence_disappears():
    qualification = _QualificationResolver("L2").resolve("gemini", "gemini:core", "gemini-3.8-flash")
    profile = SimpleNamespace(
        cost_minor=0,
        price_currency="JPY",
        billing_mode="free_fixed",
        overage_policy="hard_stop",
        no_charge_guaranteed=True,
        expires_at="2026-10-09T00:00:00+00:00",
        allowance_amount=None,
        allowance_currency=None,
        allowance_period=None,
    )
    binding = OperationProviderBinding(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model="gemini-3.8-flash",
        api_key_env="GEMINI_API_KEY",
        quota_domain="gemini:project:test",
    )

    with pytest.raises(ResourcePoolError, match="model evidence expired"):
        with compose_resource_pool(
            ((binding, qualification, profile),),
            resolver=_QualificationResolver("L2"),
            model_admission_resolver=_missing_model_evidence_resolver(),
            provider_builder=lambda *, binding: _Provider(),
        ):
            pass
