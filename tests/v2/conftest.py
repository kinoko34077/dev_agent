"""Test-wide fixtures for the v2 test suite.

The stub qualification resolver lets tests that are NOT testing qualification
behavior (billing, dispatch, routing, capacity, etc.) create resources with
arbitrary provider IDs without setting up real qualification evidence.

Tests marked ``@pytest.mark.security`` opt out of the stub entirely — they
manage their own resolver (or use _NullQualificationResolver) so the gate is
exercised without an implicit bypass.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest

from src.dev_agent.resources.qualification import QualificationProjection


@pytest.fixture(autouse=True)
def _isolate_host_provider_configuration(monkeypatch):
    """Keep developer-host credentials from activating real routes in tests.

    Provider-pool activation is intentionally automatic when credentials are
    configured for normal operation. Tests must therefore set any provider
    fixture explicitly instead of inheriting API keys or route pins from the
    machine that happens to run pytest.
    """
    names = {
        "AI_GATEWAY_API_KEY", "AI_GATEWAY_MODEL",
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL",
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GEMINI_CORE_MODEL", "GEMINI_MODEL",
        "GEMINI_PROJECT_ID", "GROQ_API_KEY", "GROQ_MODEL", "MISTRAL_API_KEY",
        "MISTRAL_MODEL", "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_CLOUD_MODEL",
        "OLLAMA_CRITIC_BASE_URL", "OLLAMA_CRITIC_MODEL", "OLLAMA_MODEL", "OLLAMA_MODELS",
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
        "SAMBANOVA_API_KEY", "SAMBANOVA_MODEL", "ANTHROPIC_API_KEY",
        "DEV_AGENT_DISABLE_CONFIGURED_POOL", "DEV_AGENT_ENABLE_CONFIGURED_POOL",
        "DEV_AGENT_PROVIDER", "DEV_AGENT_MODEL", "DEV_AGENT_PROVIDER_BINDING_ID",
        "DEV_AGENT_PROVIDER_POOL", "DEV_AGENT_MODEL_EVIDENCE_DIR",
    }
    names.update(
        name for name in os.environ
        if name.startswith(("GEMINI_API_KEY_", "GEMINI_MODEL_"))
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)


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




def _historical_review_clock():
    # Test-only historical positive admission fixture; review and update when
    # the canonical fixture evidence changes. No real wall clock is modified.
    reviewed_at = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    class _ReviewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return reviewed_at.replace(tzinfo=None)
            return reviewed_at.astimezone(tz)

    return _ReviewedDatetime, reviewed_at


@pytest.fixture
def historical_qualification_review_clock(monkeypatch):
    """Opt-in for tests that explicitly model the 2026-10-05 qualification."""
    import src.dev_agent.resources.qualification as qualification_module
    historical_datetime, _ = _historical_review_clock()
    monkeypatch.setattr(qualification_module, "datetime", historical_datetime)


@pytest.fixture
def historical_repair_review_clock(monkeypatch):
    """Opt-in for test-only legacy resource migration at historical review time."""
    import src.dev_agent.resources.repair as repair_module
    historical_datetime, _ = _historical_review_clock()
    monkeypatch.setattr(repair_module, "datetime", historical_datetime)

@pytest.fixture(autouse=True)
def _historical_billing_review_clock(request, monkeypatch):
    """Keep non-security fixture admissions independent of the wall clock.

    Tests composing the published, dated trusted billing catalog as fixture
    input must not begin failing when those historical review windows expire.
    Production callers and security tests still use the real clock. This
    touches only the billing module's imported datetime name in the current
    pytest process; it does not change trusted profiles, expiry values,
    qualification evidence, actual quotas, or the Python/system clock.

    Expiry/security tests using explicit is_current(now=...) retain their
    exact boundary behavior. Security-marked tests opt out completely.
    """
    if request.node.get_closest_marker("security"):
        return
    import src.dev_agent.resources.billing_catalog as billing_catalog

    # Historical positive fixtures intentionally represent the Oct 5 review.
    # If canonical fixture profiles are renewed or replaced, revisit this
    # instant and the dependent synthetic expectations rather than extending
    # real billing authority to keep tests green.
    _HistoricalBillingDatetime, review_instant = _historical_review_clock()

    use_current_catalog = request.node.get_closest_marker("current_billing_catalog") is not None
    if not use_current_catalog:
        monkeypatch.setattr(billing_catalog, "datetime", _HistoricalBillingDatetime)

    # Routing independently rechecks trusted billing expiration at dispatch.
    # Its module-specific datetime is isolated for fixture-based admissions;
    # live quota/health age calculations use time.time() unchanged.
    import src.dev_agent.resources.router as router_module
    if not use_current_catalog:
        monkeypatch.setattr(router_module, "datetime", _HistoricalBillingDatetime)

    # The development Worker activation policy also captures a default
    # observation time in its constructor, separately from billing lookup.
    # Inject the same historical observation only when a non-security test
    # has not explicitly supplied a clock; preserve explicit expiry tests.
    from scripts.devfarm_worker_admission import DevFarmActivationPolicy

    original_activation_init = DevFarmActivationPolicy.__init__

    def _fixture_activation_init(self, *args, **kwargs):
        if kwargs.get("now") is None and not use_current_catalog:
            kwargs["now"] = review_instant
        return original_activation_init(self, *args, **kwargs)

    monkeypatch.setattr(DevFarmActivationPolicy, "__init__", _fixture_activation_init)


@pytest.fixture(autouse=True)
def _synthetic_worker_billing_profiles(request, monkeypatch):
    """Admit only the exact fake Cloudflare identity in opted-in DevFarm tests.

    Synthetic Worker tests exercise factory/lifecycle behavior, not real
    Provider billing. Keep their exact model identities explicitly bounded;
    billing/security tests opt out and exercise production policy.
    """
    if request.node.get_closest_marker("security"):
        return
    if request.node.get_closest_marker("synthetic_no_charge_worker_billing") is None:
        return

    from src.dev_agent.resources.billing_catalog import TrustedResourceProfile
    import scripts.devfarm_worker_admission as worker_admission

    test_profiles = {
        ("cloudflare", "cloudflare:account", "@cf/zai-org/glm-4.7-flash"): TrustedResourceProfile(
            provider_id="cloudflare",
            provider_binding_id="cloudflare:account",
            model_id="@cf/zai-org/glm-4.7-flash",
            cost_minor=0,
            price_currency="JPY",
            quota_required=True,
            intelligence_tier=None,
            source="test_only_synthetic_worker_fixture",
            verified_at="2026-10-01T00:00:00+00:00",
            expires_at="2026-12-01T00:00:00+00:00",
            billing_mode="recurring_allowance",
            allowance_period="daily",
            overage_policy="hard_stop",
        ),
        ("gemini", "gemini:worker", "gemini-3.5-flash-lite"): TrustedResourceProfile(
            provider_id="gemini",
            provider_binding_id="gemini:worker",
            model_id="gemini-3.5-flash-lite",
            cost_minor=0,
            price_currency="JPY",
            quota_required=True,
            intelligence_tier="L1",
            source="test_only_synthetic_worker_fixture",
            verified_at="2026-08-01T00:00:00+00:00",
            expires_at="2026-12-01T00:00:00+00:00",
            billing_mode="recurring_allowance",
            allowance_period="daily",
            overage_policy="hard_stop",
        ),
        ("openrouter", "openrouter:free", "openrouter/free"): TrustedResourceProfile(
            provider_id="openrouter",
            provider_binding_id="openrouter:free",
            model_id="openrouter/free",
            cost_minor=0,
            price_currency="JPY",
            quota_required=True,
            intelligence_tier="L1",
            source="test_only_synthetic_worker_fixture",
            verified_at="2026-10-01T00:00:00+00:00",
            expires_at="2026-12-01T00:00:00+00:00",
            billing_mode="recurring_allowance",
            allowance_period="daily",
            overage_policy="hard_stop",
        ),
    }
    original_profile_for = worker_admission.trusted_billing_profile_for

    def _profile_for(provider_id, provider_binding_id, model_id):
        profile = test_profiles.get((provider_id, provider_binding_id, model_id))
        if profile is not None:
            return profile
        return original_profile_for(provider_id, provider_binding_id, model_id)

    monkeypatch.setattr(worker_admission, "trusted_billing_profile_for", _profile_for)
