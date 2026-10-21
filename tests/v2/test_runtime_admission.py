from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.resources.runtime_admission import (
    RUNTIME_ELIGIBLE,
    RUNTIME_UNKNOWN,
    RUNTIME_UNAVAILABLE,
    RuntimeAdmissionCandidate,
    RuntimeAdmissionEvaluator,
)


def _candidate() -> RuntimeAdmissionCandidate:
    return RuntimeAdmissionCandidate(
        provider_id="gemini",
        provider_binding_id="gemini:worker:free-2",
        model_id="gemini-3.8-flash",
    )


def test_exact_runtime_admission_reuses_existing_router_for_healthy_resource(tmp_path):
    ledger = ResourceLedger(tmp_path / "runtime-admission.sqlite3")
    ledger.register_resource(
        "gemini-free-2",
        provider_id="gemini",
        provider_binding_id="gemini:worker:free-2",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        sensitivity="normal",
        cost_minor=0,
        metadata={"provider_binding_id": "gemini:worker:free-2", "model_id": "gemini-3.8-flash"},
    )
    ledger.observe("gemini-free-2", available=1, health="healthy")

    observation = RuntimeAdmissionEvaluator(ResourceRouter(ledger)).evaluate(
        _candidate(),
        snapshot=ledger.routing_snapshot(),
        observed_at="2026-09-27T00:00:00+00:00",
    )

    assert observation.status == RUNTIME_ELIGIBLE
    assert observation.identity == (_candidate().provider_id, _candidate().provider_binding_id, _candidate().model_id)


def test_exact_runtime_admission_reports_missing_resource_as_unavailable(tmp_path):
    ledger = ResourceLedger(tmp_path / "runtime-admission-missing.sqlite3")

    observation = RuntimeAdmissionEvaluator(ResourceRouter(ledger)).evaluate(
        _candidate(),
        snapshot=ledger.routing_snapshot(),
        observed_at="2026-09-27T00:00:00+00:00",
    )

    assert observation.status == RUNTIME_UNAVAILABLE


def test_exact_runtime_admission_reports_missing_quota_observation_as_unknown(tmp_path):
    ledger = ResourceLedger(tmp_path / "runtime-admission-quota.sqlite3")
    ledger.register_resource(
        "gemini-free-2",
        provider_id="gemini",
        provider_binding_id="gemini:worker:free-2",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        sensitivity="normal",
        cost_minor=0,
        quota_domain="gemini:project:free-2",
        metadata={"provider_binding_id": "gemini:worker:free-2", "model_id": "gemini-3.8-flash"},
    )
    ledger.observe("gemini-free-2", available=1, health="healthy")

    observation = RuntimeAdmissionEvaluator(ResourceRouter(ledger)).evaluate(
        _candidate(),
        snapshot=ledger.routing_snapshot(),
        observed_at="2026-09-27T00:00:00+00:00",
    )

    assert observation.status == RUNTIME_UNKNOWN


def _trusted_free3_ledger(tmp_path, monkeypatch):
    from src.dev_agent.operation import OperationProviderBinding, OperationService
    from src.dev_agent import operation as operation_module
    from src.dev_agent.resources import router as router_module
    from src.dev_agent.resources.billing_catalog import TrustedResourceProfile
    from src.dev_agent.resources.qualification import QualificationResolver

    observed_at = datetime.now(timezone.utc)
    tested_at = (observed_at - timedelta(minutes=1)).isoformat()
    expires_at = (observed_at + timedelta(days=7)).isoformat()
    profile = TrustedResourceProfile(
        "gemini",
        "gemini:worker:free-3",
        "gemini-3.5-flash-lite",
        0,
        "JPY",
        True,
        "L1",
        source="runtime_admission_test_fixture",
        verified_at=tested_at,
        expires_at=expires_at,
        billing_mode="recurring_allowance",
        allowance_period="daily",
        overage_policy="hard_stop",
    )
    qualification = QualificationResolver(entries=[{
        "provider": "gemini",
        "provider_binding_id": "gemini:worker:free-3",
        "model": "gemini-3.5-flash-lite",
        "intelligence_tier": "L1",
        "tested_at": tested_at,
        "expires_at": expires_at,
        "confidence": "high",
        "capabilities": ["text"],
    }])

    def exact_test_profile(provider_id, provider_binding_id, model_id):
        identity = (provider_id, provider_binding_id, model_id)
        expected = ("gemini", "gemini:worker:free-3", "gemini-3.5-flash-lite")
        return profile if identity == expected else None

    # These tests exercise the admission path, not the live expiry dates of
    # production evidence. Keep exact test-only facts current without renewing
    # or changing the protected qualification/billing sources.
    monkeypatch.setattr(operation_module, "_operation_resource_profile", exact_test_profile)
    monkeypatch.setattr(router_module, "profile_for", exact_test_profile)

    ledger = ResourceLedger(tmp_path / "runtime-admission-bootstrap.sqlite3")
    binding = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        provider_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:free-3",
        api_key_env="UNUSED_TEST_KEY",
    )
    OperationService._ensure_resource(ledger, object(), binding, qualification_resolver=qualification)
    return ledger, ResourceRouter(ledger, qualification_resolver=qualification), observed_at


_FREE3 = RuntimeAdmissionCandidate(
    provider_id="gemini",
    provider_binding_id="gemini:worker:free-3",
    model_id="gemini-3.5-flash-lite",
)


def test_trusted_no_charge_route_without_quota_is_bootstrap_admitted_not_eligible(tmp_path, monkeypatch):
    from src.dev_agent.resources.runtime_admission import RUNTIME_BOOTSTRAP_ADMITTED

    ledger, router, observed_at = _trusted_free3_ledger(tmp_path, monkeypatch)

    observation = RuntimeAdmissionEvaluator(router).evaluate(
        _FREE3,
        snapshot=ledger.routing_snapshot(),
        observed_at=observed_at.isoformat(),
    )

    assert observation.status == RUNTIME_BOOTSTRAP_ADMITTED
    assert observation.status != RUNTIME_ELIGIBLE


def test_derived_conservative_quota_can_reach_formal_runtime_eligibility(tmp_path, monkeypatch):
    ledger, router, observed_at = _trusted_free3_ledger(tmp_path, monkeypatch)
    resource = ledger.get_resource("gemini:worker:free-3")
    ledger.observe(resource["resource_id"], available=1, health="healthy")
    reset_at = (observed_at + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    ledger.observe_quota(
        resource["resource_id"],
        unit="neurons",
        limit=10_000,
        remaining=9_823,
        consumed=177,
        authority="derived_conservative",
        metric="workers_ai_neurons",
        window="day",
        reset_source="cloudflare_daily_utc",
        reset_at=reset_at,
        observed_at=observed_at.isoformat(),
        source="cloudflare-neuron-estimate",
    )

    observation = RuntimeAdmissionEvaluator(router).evaluate(
        _FREE3,
        snapshot=ledger.routing_snapshot(),
        observed_at=observed_at.isoformat(),
    )

    assert observation.status == RUNTIME_ELIGIBLE


def test_cloudflare_derived_quota_reaches_admission_through_existing_resource_path(tmp_path):
    from src.dev_agent.operation import OperationProviderBinding, OperationService
    from src.dev_agent.resources.qualification import QualificationResolver

    ledger = ResourceLedger(tmp_path / "cloudflare-runtime-admission.sqlite3")
    resolver = QualificationResolver()
    binding = OperationProviderBinding(
        provider_id="cloudflare",
        model="@cf/zai-org/glm-4.7-flash",
        provider_binding_id="cloudflare:account",
        quota_domain="cloudflare:account:test",
        api_key_env="UNUSED_TEST_KEY",
    )

    # Reuse the same registration boundary that Operation opens for a real
    # provider resource; no network call or provider response is fabricated.
    OperationService._ensure_resource(ledger, object(), binding, qualification_resolver=resolver)
    ledger.observe("cloudflare:account", available=1, health="healthy")
    observed_at = datetime.now(timezone.utc).isoformat()
    ledger.observe_quota(
        "cloudflare:account",
        unit="neurons",
        limit=10_000,
        remaining=9_700,
        consumed=300,
        authority="derived_conservative",
        metric="workers_ai_neurons",
        window="day",
        reset_source="cloudflare_daily_utc",
        reset_at="2026-10-04T00:00:00+00:00",
        observed_at=observed_at,
        source="cloudflare-neuron-estimate",
    )

    router = ResourceRouter(ledger, qualification_resolver=resolver)
    observation = RuntimeAdmissionEvaluator(router).evaluate(
        RuntimeAdmissionCandidate(
            provider_id="cloudflare",
            provider_binding_id="cloudflare:account",
            model_id="@cf/zai-org/glm-4.7-flash",
        ),
        snapshot=ledger.routing_snapshot(),
        observed_at=observed_at,
    )

    assert observation.status == RUNTIME_ELIGIBLE


def test_blocked_quota_domain_is_not_bootstrap_admitted(tmp_path):
    ledger, router = _trusted_free3_ledger(tmp_path)
    snapshot = ledger.routing_snapshot()
    blocked = type(snapshot)(
        snapshot.resources,
        {"gemini:project:free-3": ({"quota_domain": "gemini:project:free-3", "block_reason": "rate_limit", "observed_at": "2026-09-27T00:00:00+00:00"},)},
    )

    observation = RuntimeAdmissionEvaluator(router).evaluate(_FREE3, snapshot=blocked)

    assert observation.status == RUNTIME_UNKNOWN
