from uuid import uuid4

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
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
        control.release(reservation)
    finally:
        ledger.close()
