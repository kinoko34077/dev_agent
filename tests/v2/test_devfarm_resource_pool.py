from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.devfarm_resource_pool import ResourcePoolError, compose_resource_pool
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
