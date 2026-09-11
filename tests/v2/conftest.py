"""Test-wide fixtures for the v2 test suite.

The stub qualification resolver lets tests that are NOT testing qualification
behavior (billing, dispatch, routing, capacity, etc.) create resources with
arbitrary provider IDs without setting up real qualification evidence.  Tests
that exercise the qualification security boundary inject their own resolver
explicitly, so the autouse fixture does not affect them.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.dev_agent.resources.qualification import QualificationProjection


class _StubQualificationResolver:
    """Returns a valid qualification projection for any provider/binding/model.

    This stub is intentionally permissive — it exists to satisfy the
    routing gate in tests that are not testing the qualification boundary
    itself.  Security regression tests for P0-1 inject a resolver that
    returns None (e.g. a QualificationResolver with no entries) to verify
    that unqualified providers are rejected.
    """

    def resolve(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        **_kwargs,
    ) -> QualificationProjection:
        now = datetime.now(timezone.utc)
        return QualificationProjection(
            provider_id=provider_id,
            provider_binding_id=provider_binding_id,
            model_id=model_id or "_stub_",
            routing_capabilities=frozenset({"text", "tool_call"}),
            qualification_evidence=frozenset({
                "text",
                "model_generated_tool_call",
                "tool_result_roundtrip",
                "final_response",
                "controller_e2e",
                "thought_signature_roundtrip",
                "durable_provider_audit",
                "budget_reconciliation",
            }),
            integration_evidence=frozenset({
                "controller_e2e",
                "thought_signature_roundtrip",
                "durable_provider_audit",
                "budget_reconciliation",
            }),
            intelligence_tier=None,
            tested_at=(now - timedelta(hours=1)).isoformat(),
            expires_at=(now + timedelta(days=30)).isoformat(),
            confidence="high",
        )

    def resolve_observed(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        **_kwargs,
    ) -> QualificationProjection | None:
        return self.resolve(provider_id, provider_binding_id, model_id)


@pytest.fixture(autouse=True)
def _stub_qualification_resolver(monkeypatch):
    """Replace the default QualificationResolver in both the router and
    operation modules so tests without explicit resolver injection are not
    blocked by the P0-1 qualification gate.

    Tests that need to verify the gate rejects unqualified providers must
    create a ResourceRouter with an explicit resolver that returns None.
    """
    stub_class = lambda **_kw: _StubQualificationResolver()  # noqa: E731
    import src.dev_agent.resources.router as _router_mod
    import src.dev_agent.operation as _operation_mod
    monkeypatch.setattr(_router_mod, "QualificationResolver", stub_class)
    monkeypatch.setattr(_operation_mod, "QualificationResolver", stub_class)
