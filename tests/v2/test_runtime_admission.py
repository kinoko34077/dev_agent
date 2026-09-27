from __future__ import annotations

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


def _trusted_free3_ledger(tmp_path):
    from src.dev_agent.operation import OperationProviderBinding, OperationService
    from src.dev_agent.resources.qualification import QualificationResolver

    ledger = ResourceLedger(tmp_path / "runtime-admission-bootstrap.sqlite3")
    binding = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        provider_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:free-3",
        api_key_env="UNUSED_TEST_KEY",
    )
    resolver = QualificationResolver()
    OperationService._ensure_resource(ledger, object(), binding, qualification_resolver=resolver)
    return ledger, ResourceRouter(ledger, qualification_resolver=resolver)


_FREE3 = RuntimeAdmissionCandidate(
    provider_id="gemini",
    provider_binding_id="gemini:worker:free-3",
    model_id="gemini-3.5-flash-lite",
)


def test_trusted_no_charge_route_without_quota_is_bootstrap_admitted_not_eligible(tmp_path):
    from src.dev_agent.resources.runtime_admission import RUNTIME_BOOTSTRAP_ADMITTED

    ledger, router = _trusted_free3_ledger(tmp_path)

    observation = RuntimeAdmissionEvaluator(router).evaluate(_FREE3, snapshot=ledger.routing_snapshot())

    assert observation.status == RUNTIME_BOOTSTRAP_ADMITTED
    assert observation.status != RUNTIME_ELIGIBLE


def test_blocked_quota_domain_is_not_bootstrap_admitted(tmp_path):
    ledger, router = _trusted_free3_ledger(tmp_path)
    snapshot = ledger.routing_snapshot()
    blocked = type(snapshot)(
        snapshot.resources,
        {"gemini:project:free-3": ({"quota_domain": "gemini:project:free-3", "block_reason": "rate_limit", "observed_at": "2026-09-27T00:00:00+00:00"},)},
    )

    observation = RuntimeAdmissionEvaluator(router).evaluate(_FREE3, snapshot=blocked)

    assert observation.status == RUNTIME_UNKNOWN
