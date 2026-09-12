"""Test-wide fixtures for the v2 test suite.

The stub qualification resolver lets tests that are NOT testing qualification
behavior (billing, dispatch, routing, capacity, etc.) create resources with
arbitrary provider IDs without setting up real qualification evidence.

Tests marked ``@pytest.mark.security`` opt out of the stub entirely — they
manage their own resolver (or use _NullQualificationResolver) so the gate is
exercised without an implicit bypass.
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
def _stub_qualification_resolver(request, monkeypatch):
    """Replace the default QualificationResolver in both the router and
    operation modules so tests without explicit resolver injection are not
    blocked by the P0-1 qualification gate.

    Tests marked @pytest.mark.security opt out of this stub — they own
    their resolver and must exercise the real gate.
    """
    if request.node.get_closest_marker("security"):
        return  # security tests manage their own qualification resolver
    stub_class = lambda **_kw: _StubQualificationResolver()  # noqa: E731
    import src.dev_agent.resources.router as _router_mod
    import src.dev_agent.operation as _operation_mod
    monkeypatch.setattr(_router_mod, "QualificationResolver", stub_class)
    monkeypatch.setattr(_operation_mod, "QualificationResolver", stub_class)


@pytest.fixture(autouse=True)
def _stub_provider_class_identity(request, monkeypatch):
    """Disable two Provider-instance Authority checks for non-security tests:
    the exact-type canonical-adapter check, and the HTTP-transport-opener
    identity check.

    validate_provider_class_identity() (in resources/provider_policy.py) is
    a production Authority boundary: it requires an already-constructed
    Provider instance claiming a network-capable provider_id (e.g. "gemini")
    to be the exact canonical adapter class ProviderFactory would have
    built. Many routing/dispatch tests throughout this suite construct a
    lightweight FakeProvider-based double with provider_id overridden to
    simulate a real identity purely to exercise routing/fallback/saturation
    logic — they never touch base_url/api_key_env and make no real network
    call, but they are not the canonical class either, so the real check
    would reject them.

    validate_transport_identity() requires the OpenAI-compatible family's
    ``provider._http._opener`` to be exactly the shared canonical
    ``urlopen_no_redirect`` function. Several existing integration tests
    (e.g. test_free_provider_qualification.py) legitimately simulate a live
    HTTP response by monkeypatching a provider module's
    ``urlopen_no_redirect`` name *before* constructing the real, canonical
    provider class through ProviderFactory — a different, well-established
    testing need (simulate the network) from what this check exists to
    catch (an already-trusted instance's opener silently swapped to
    something else after the fact). Both are opted out here, not by
    weakening the production validators themselves — an isinstance-based or
    "close enough" exemption inside the validator can be defeated by a
    determined subclass/wrapper, which is exactly the P0-3 re-audit's
    lesson. This fixture is the test-side composition: it swaps the real
    checks for no-ops only within test collection, following the same
    @pytest.mark.security opt-out convention as _stub_qualification_resolver
    above. Tests marked @pytest.mark.security exercise the real, unmodified
    checks.
    """
    if request.node.get_closest_marker("security"):
        return  # security tests must exercise the real checks
    import src.dev_agent.resources.provider_policy as _provider_policy_mod

    def _noop_class_identity_check(provider_id, provider):
        return None

    def _noop_transport_identity_check(provider_id, provider):
        return None

    monkeypatch.setattr(_provider_policy_mod, "validate_provider_class_identity", _noop_class_identity_check)
    monkeypatch.setattr(_provider_policy_mod, "validate_transport_identity", _noop_transport_identity_check)
