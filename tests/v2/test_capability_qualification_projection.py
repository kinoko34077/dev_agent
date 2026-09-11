from datetime import datetime, timezone

import pytest

from src.dev_agent.operation import OperationConfig, OperationProviderBinding, OperationService
from src.dev_agent.resources.qualification import (
    CANONICAL_ROUTING_CAPABILITIES,
    QualificationCatalog,
    QualificationError,
    QualificationResolver,
)
from src.dev_agent.domain.capabilities import CANONICAL_EXECUTION_CAPABILITIES
from src.dev_agent.intelligence.capabilities import CANONICAL_EXECUTION_CAPABILITIES as INTELLIGENCE_EXECUTION_CAPABILITIES
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest


def test_qualified_tool_evidence_projects_to_tool_call_for_operation_resource(tmp_path):
    config = OperationProviderBinding(
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        provider_binding_id="cloudflare",
        quota_domain="account:test",
        intelligence_tier="L1",
    )
    provider = type(
        "QualifiedProvider",
        (),
        {
            "provider_binding_id": "cloudflare",
            "model_id": "@cf/meta/llama-3.1-8b-instruct",
            "intelligence_tier": "L1",
        },
    )()

    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        OperationService._ensure_resource(ledger, provider, config)
        resource = ledger.get_resource("cloudflare")

    assert "text" in resource["capabilities"]
    assert "tool_call" in resource["capabilities"]


def test_projection_keeps_integration_evidence_out_of_routing_capabilities():
    resolver = QualificationResolver(
        entries=[
            {
                "provider": "fixture",
                "provider_binding_id": "fixture:worker",
                "model": "fixture-model",
                "intelligence_tier": "L1",
                "tested_at": "2026-09-01T00:00:00+00:00",
                "expires_at": "2026-10-01T00:00:00+00:00",
                "confidence": "high",
                "capabilities": [
                    "text",
                    "model_generated_tool_call",
                    "tool_result_roundtrip",
                    "final_response",
                    "controller_e2e",
                    "durable_provider_audit",
                ],
            }
        ]
    )

    projection = resolver.resolve(
        "fixture",
        "fixture:worker",
        "fixture-model",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )

    assert projection is not None
    assert projection.routing_capabilities == frozenset({"text", "tool_call"})
    assert "durable_provider_audit" not in projection.routing_capabilities
    assert "durable_provider_audit" in projection.integration_evidence
    assert "controller_e2e" in projection.integration_evidence


def test_expired_qualification_is_not_projected():
    resolver = QualificationResolver(
        entries=[
            {
                "provider": "fixture",
                "provider_binding_id": "fixture:expired",
                "model": "fixture-model",
                "intelligence_tier": "L1",
                "tested_at": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "confidence": "high",
                "capabilities": ["text"],
            }
        ]
    )

    assert resolver.resolve(
        "fixture",
        "fixture:expired",
        "fixture-model",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    ) is None


@pytest.mark.parametrize("confidence", ["low", "medium"])
def test_non_high_confidence_is_not_admitted_to_production_routing(confidence):
    entry = _qualification_entry(expires_at="2026-10-01T00:00:00+00:00")
    entry["confidence"] = confidence
    resolver = QualificationResolver(entries=[entry])

    assert resolver.resolve(
        "fixture",
        "fixture:worker",
        "fixture-model",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    ) is None


def test_high_confidence_is_admitted_to_production_routing():
    resolver = QualificationResolver(entries=[_qualification_entry(expires_at="2026-10-01T00:00:00+00:00")])

    projection = resolver.resolve(
        "fixture",
        "fixture:worker",
        "fixture-model",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )

    assert projection is not None
    assert projection.confidence == "high"


def test_unknown_qualification_identity_does_not_fall_back_to_model_name():
    resolver = QualificationResolver(entries=[])

    assert resolver.resolve("gemini", "gemini:fast-fallback", "gemini-3.7-flash") is None


def test_qualification_catalog_indexes_exact_identity():
    entry = _qualification_entry(expires_at="2026-10-01T00:00:00+00:00")
    catalog = QualificationCatalog.from_entries([entry])

    assert catalog.lookup("fixture", "fixture:worker", "fixture-model") == entry
    assert catalog.lookup("fixture", "fixture:other", "fixture-model") is None


def test_qualification_catalog_rejects_duplicate_identity():
    entry = _qualification_entry(expires_at="2026-10-01T00:00:00+00:00")

    with pytest.raises(QualificationError, match="duplicate qualification identity"):
        QualificationCatalog.from_entries([entry, dict(entry)])


def test_qualification_catalog_rejects_unknown_confidence():
    entry = _qualification_entry(expires_at="2026-10-01T00:00:00+00:00")
    entry["confidence"] = "unverified"

    with pytest.raises(QualificationError, match="confidence"):
        QualificationCatalog.from_entries([entry])


def test_canonical_execution_capabilities_have_one_source_of_truth():
    assert CANONICAL_ROUTING_CAPABILITIES is CANONICAL_EXECUTION_CAPABILITIES
    assert INTELLIGENCE_EXECUTION_CAPABILITIES is CANONICAL_EXECUTION_CAPABILITIES


def test_operation_opens_one_shared_qualification_catalog(monkeypatch, tmp_path):
    loads = []
    original_load = QualificationCatalog.load

    def counted_load(cls, path=None):
        loads.append(path)
        return original_load(path)

    monkeypatch.setattr(QualificationCatalog, "load", classmethod(counted_load))
    service = OperationService.open(OperationConfig(data_dir=tmp_path))
    try:
        assert len(loads) == 1
        assert service.qualification_resolver is service.controller.resource_policy.router.qualification_resolver
    finally:
        service.close()


def test_unqualified_model_name_does_not_assign_production_tier(tmp_path):
    config = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.7-flash",
        provider_binding_id="gemini:fast-fallback",
        quota_domain="project:test",
    )
    provider = type(
        "UnqualifiedProvider",
        (),
        {
            "provider_binding_id": "gemini:fast-fallback",
            "model_id": "gemini-3.7-flash",
            "intelligence_tier": None,
        },
    )()

    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        OperationService._ensure_resource(ledger, provider, config)
        resource = ledger.get_resource("gemini:fast-fallback")

    assert resource["metadata"].get("intelligence_tier") is None


def _qualification_entry(*, expires_at: str, capabilities: list[str] | None = None):
    return {
        "provider": "fixture",
        "provider_binding_id": "fixture:worker",
        "model": "fixture-model",
        "intelligence_tier": "L1",
        "tested_at": "2026-09-01T00:00:00+00:00",
        "expires_at": expires_at,
        "confidence": "high",
        "capabilities": capabilities or [
            "text",
            "model_generated_tool_call",
            "tool_result_roundtrip",
            "final_response",
        ],
    }


def _qualification_resource(ledger, *, capabilities=("text", "tool_call")):
    ledger.register_resource(
        "fixture:worker",
        provider_id="fixture",
        provider_binding_id="fixture:worker",
        native_unit="request",
        capacity=1,
        capabilities=capabilities,
        cost_minor=0,
        metadata={
            "provider_binding_id": "fixture:worker",
            "model_id": "fixture-model",
            "qualification_required": True,
            "billing_authority": "trusted_catalog",
        },
        intelligence_tier="L1",
    )
    ledger.observe("fixture:worker", available=1, health="healthy", concurrency_limit=1)


def test_router_uses_current_qualification_for_existing_resource(tmp_path):
    resolver = QualificationResolver(entries=[_qualification_entry(expires_at="2026-10-01T00:00:00+00:00")])
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        _qualification_resource(ledger)
        router = ResourceRouter(ledger, qualification_resolver=resolver)

        selection = router.choose(RouteRequest(capabilities={"tool_call"}, allowed_intelligence_tiers={"L1"}))

    assert selection.provider_binding_id == "fixture:worker"


def test_router_rejects_expired_qualification_without_mutating_resource(tmp_path):
    resolver = QualificationResolver(entries=[_qualification_entry(expires_at="2026-09-01T00:00:00+00:00")])
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        _qualification_resource(ledger)
        before = ledger.get_resource("fixture:worker")

        with pytest.raises(NoRoute):
            ResourceRouter(ledger, qualification_resolver=resolver).choose(
                RouteRequest(capabilities={"tool_call"}, allowed_intelligence_tiers={"L1"})
            )

        after = ledger.get_resource("fixture:worker")

    assert after["capabilities"] == before["capabilities"]
    assert after["metadata"] == before["metadata"]


def test_router_does_not_accept_unqualified_exact_production_resource(tmp_path):
    resolver = QualificationResolver(entries=[])
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        _qualification_resource(ledger)

        with pytest.raises(NoRoute):
            ResourceRouter(ledger, qualification_resolver=resolver).choose(RouteRequest(capabilities={"text"}))
