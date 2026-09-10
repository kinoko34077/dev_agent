from datetime import datetime, timezone

import pytest

from src.dev_agent.operation import OperationConfig, OperationProviderBinding, OperationService
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.ledger import ResourceLedger


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


def test_unknown_qualification_identity_does_not_fall_back_to_model_name():
    resolver = QualificationResolver(entries=[])

    assert resolver.resolve("gemini", "gemini:fast-fallback", "gemini-3.7-flash") is None


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
