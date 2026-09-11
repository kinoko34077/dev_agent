from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import DispatchDenied, ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger, unknown_quota_wake_reason
from src.dev_agent.resources.router import ResourceRouter


def _control(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
    return ledger, ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))


def test_legacy_resource_reservation_honors_exact_intelligence_tier(tmp_path):
    ledger, control = _control(tmp_path)
    try:
        for resource_id, tier in (("a-vendor-worker", "L1"), ("z-vendor-core", "L2")):
            ledger.register_resource(
                resource_id,
                provider_id="vendor",
                provider_binding_id=resource_id,
                native_unit="request",
                capacity=1,
                capabilities=["text"],
                cost_minor=0,
                intelligence_tier=tier,
                metadata={"intelligence_tier": tier},
            )
            ledger.observe(resource_id, available=1, health="healthy")

        request = ModelRequest(
            task_id=str(uuid4()),
            messages=[{"role": "user", "content": "use the core tier"}],
            metadata={
                "intelligence_routing": "bounded",
                "allowed_intelligence_tiers": ["L2"],
            },
        )

        reservation = control.reserve_for_provider(request.task_id, "vendor", request)

        assert reservation.budget.resource_id == "z-vendor-core"
        control.release(reservation)
    finally:
        ledger.close()


def test_legacy_resource_reservation_can_use_explicit_unknown_quota_bootstrap(tmp_path):
    ledger, control = _control(tmp_path)
    try:
        ledger.register_resource(
            "cloud:free",
            provider_id="cloud",
            provider_binding_id="cloud:free",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            quota_domain="cloud-project",
            metadata={
                "billing_authority": "trusted_catalog",
                "billing_mode": "free_fixed",
                "overage_policy": "hard_stop",
                "no_charge_guaranteed": True,
                "model_id": "free-model",
                "intelligence_tier": "L1",
            },
            intelligence_tier="L1",
        )
        ledger.observe("cloud:free", available=1, health="degraded")
        request = ModelRequest(
            task_id=str(uuid4()),
            messages=[{"role": "user", "content": "one bounded request"}],
            metadata={"allow_unknown_quota": True},
        )

        reservation = control.reserve_for_provider(request.task_id, "cloud", request)

        assert reservation.budget.resource_id == "cloud:free"
        control.mark_dispatching(reservation)
        control.uncertain(reservation)

        with pytest.raises(DispatchDenied, match="unknown quota admission") as denied:
            control.reserve_for_provider(str(uuid4()), "cloud", request)
        assert denied.value.category == "quota_unknown"
    finally:
        ledger.close()


def test_unknown_quota_admission_is_domain_scoped_and_expires(tmp_path):
    ledger = ResourceLedger(tmp_path / "unknown-quota-admission.sqlite3")
    try:
        first = ledger.claim_unknown_quota_admission("cloud-free", now_epoch=100.0)
        second = ledger.claim_unknown_quota_admission("cloud-free", now_epoch=100.1)
        other_domain = ledger.claim_unknown_quota_admission("other-cloud", now_epoch=100.1)

        assert first.admitted is True
        assert second.admitted is False
        assert second.retry_at_epoch == 160.0
        assert other_domain.admitted is True
        assert ledger.due_unknown_quota_domains(now_epoch=100.1) == ()
        assert ledger.due_unknown_quota_domains(now_epoch=161.0) == ("cloud-free", "other-cloud")
        after_window = ledger.claim_unknown_quota_admission("cloud-free", now_epoch=161.0)
        assert after_window.admitted is True
        assert unknown_quota_wake_reason("cloud-free") == "quota_unknown:cloud-free"
    finally:
        ledger.close()


def test_recurring_allowance_without_no_charge_authority_cannot_settle_missing_cost_as_zero(tmp_path):
    ledger, control = _control(tmp_path)
    try:
        ledger.register_resource(
            "allowance",
            provider_id="cloud",
            provider_binding_id="cloud:allowance",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            quota_domain="cloud-project",
            metadata={
                "billing_authority": "trusted_catalog",
                "billing_mode": "recurring_allowance",
                "overage_policy": "unknown",
                "no_charge_guaranteed": False,
                "model_id": "cloud-model",
            },
        )
        ledger.observe("allowance", available=1, health="degraded")
        request = ModelRequest(
            task_id=str(uuid4()),
            messages=[{"role": "user", "content": "admit only under explicit unknown policy"}],
            metadata={"allow_unknown_quota": True},
        )
        with pytest.raises(DispatchDenied, match="no eligible resource"):
            control.reserve_for_provider(request.task_id, "cloud", request)
    finally:
        ledger.close()
