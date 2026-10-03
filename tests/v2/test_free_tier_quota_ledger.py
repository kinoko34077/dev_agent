from datetime import datetime, timezone

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import DispatchReservation, ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest


def _ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "cloudflare-free",
        provider_id="cloudflare",
        provider_binding_id="cloudflare:free",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="cloudflare-account",
        cost_minor=0,
        metadata={
            "model_id": "@cf/example/model",
            "billing_mode": "recurring_allowance",
            "overage_policy": "hard_stop",
            "no_charge_guaranteed": True,
        },
    )
    ledger.observe("cloudflare-free", available=1, health="healthy")
    return ledger


def test_conservative_quota_debit_survives_restart_and_is_explicitly_derived(tmp_path):
    reset_at = "2026-10-04T00:00:00+00:00"
    ledger = _ledger(tmp_path)
    ledger.configure_conservative_quota(
        "cloudflare-free",
        unit="neurons",
        allowance_limit=10_000,
        period_id="2026-10-03",
        reset_at=reset_at,
        reset_source="cloudflare_daily_utc",
        observed_at="2026-10-03T12:00:00+00:00",
    )
    ledger.debit_conservative_quota(
        "cloudflare-free",
        consumed=177,
        period_id="2026-10-03",
        observed_at="2026-10-03T12:01:00+00:00",
        source="cloudflare-neuron-estimate",
    )
    ledger.close()

    reopened = ResourceLedger(tmp_path / "resources.sqlite3")
    state = reopened.get_conservative_quota("cloudflare-free", period_id="2026-10-03")
    assert state["consumed"] == 177
    assert state["remaining"] == 9_823
    assert state["quota_authority"] == "derived_conservative"
    assert state["evidence_mode"] == "derived_conservative"
    reopened.close()


def test_conservative_quota_reset_only_creates_new_period_at_reset_boundary(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.configure_conservative_quota(
        "cloudflare-free",
        unit="neurons",
        allowance_limit=10_000,
        period_id="2026-10-03",
        reset_at="2026-10-04T00:00:00+00:00",
        reset_source="cloudflare_daily_utc",
    )
    ledger.debit_conservative_quota("cloudflare-free", consumed=9_900, period_id="2026-10-03")

    before = ledger.reset_conservative_quota_if_due(
        "cloudflare-free",
        period_id="2026-10-03",
        next_period_id="2026-10-04",
        next_reset_at="2026-10-05T00:00:00+00:00",
        now=datetime(2026, 10, 3, 23, 59, tzinfo=timezone.utc),
    )
    assert before["period_id"] == "2026-10-03"
    assert before["consumed"] == 9_900

    after = ledger.reset_conservative_quota_if_due(
        "cloudflare-free",
        period_id="2026-10-03",
        next_period_id="2026-10-04",
        next_reset_at="2026-10-05T00:00:00+00:00",
        now=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert after["period_id"] == "2026-10-04"
    assert after["consumed"] == 0
    assert after["remaining"] == 10_000


def test_provider_exhaustion_hard_stops_current_period(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.configure_conservative_quota(
        "cloudflare-free",
        unit="neurons",
        allowance_limit=10_000,
        period_id="2026-10-03",
        reset_at="2026-10-04T00:00:00+00:00",
        reset_source="cloudflare_daily_utc",
    )
    state = ledger.mark_conservative_quota_exhausted(
        "cloudflare-free",
        period_id="2026-10-03",
        blocked_until="2026-10-04T00:00:00+00:00",
        reason="provider_daily_allocation_exhausted",
    )
    assert state["exhausted"] is True
    assert state["blocked_until"] == "2026-10-04T00:00:00+00:00"
    assert ledger.get_conservative_quota("cloudflare-free", period_id="2026-10-03")["admittable"] is False


def test_conservative_quota_rejects_route_without_independent_no_charge_policy(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "paid",
        provider_id="cloudflare",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="paid-account",
        cost_minor=0,
        metadata={"billing_mode": "unknown", "overage_policy": "unknown"},
    )
    with pytest.raises(ValueError, match="no-charge"):
        ledger.configure_conservative_quota(
            "paid",
            unit="neurons",
            allowance_limit=10_000,
            period_id="2026-10-03",
            reset_at="2026-10-04T00:00:00+00:00",
            reset_source="cloudflare_daily_utc",
        )


def test_provider_observation_debits_ledger_projects_remaining_and_is_idempotent(tmp_path):
    ledger = _ledger(tmp_path)
    payload = {
        "quota_observation": {
            "unit": "neurons",
            "limit": 10_000,
            "consumed": 177,
            "quota_authority": "derived_conservative",
            "evidence_mode": "derived_conservative",
            "period_id": "2026-10-03",
            "reset_at": "2026-10-04T00:00:00+00:00",
            "reset_source": "cloudflare_daily_utc",
            "metric": "workers_ai_neurons",
            "window": "day",
        }
    }
    assert ledger.ingest_quota_observation("cloudflare-free", payload, accounting_key="reservation-1")
    assert ledger.ingest_quota_observation("cloudflare-free", payload, accounting_key="reservation-1")
    state = ledger.get_conservative_quota("cloudflare-free", period_id="2026-10-03")
    assert state["consumed"] == 177
    assert ledger.get_quota_observation("cloudflare-free")["remaining"] == 9_823


def test_control_plane_quota_error_hard_stops_latest_conservative_period(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.configure_conservative_quota(
        "cloudflare-free",
        unit="neurons",
        allowance_limit=10_000,
        period_id="2026-10-03",
        reset_at="2026-10-04T00:00:00+00:00",
        reset_source="cloudflare_daily_utc",
    )
    policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
    BudgetAuthority.configure(ledger, policy)
    governor = BudgetGovernor(ledger, policy)
    budget = governor.reserve("task-1", "cloudflare-free", estimated_cost_minor=0)
    reservation = DispatchReservation(
        budget,
        "cloudflare",
        provider_binding_id="cloudflare:free",
        model_id="@cf/example/model",
        billing_mode="recurring_allowance",
        overage_policy="hard_stop",
        no_charge_guaranteed=True,
    )

    ResourceControlPlane(ResourceRouter(ledger), governor).record_provider_error(
        "cloudflare",
        reservation,
        ProviderError(
            "daily allocation exhausted",
            category="quota",
            retryable=False,
            quota_metric="daily_neurons",
            quota_window="day_utc",
            quota_reset_at="2026-10-04T00:00:00+00:00",
            quota_reset_source="cloudflare_daily_utc",
        ),
    )

    state = ledger.get_conservative_quota("cloudflare-free", period_id="2026-10-03")
    assert state["exhausted"] is True
    assert state["blocked_until"] == "2026-10-04T00:00:00+00:00"


def test_local_conservative_exhaustion_projects_blocked_quota_observation(tmp_path):
    ledger = _ledger(tmp_path)
    payload = {
        "quota_observation": {
            "unit": "neurons",
            "limit": 100,
            "consumed": 90,
            "quota_authority": "derived_conservative",
            "evidence_mode": "derived_conservative",
            "period_id": "2026-10-03",
            "reset_at": "2026-10-04T00:00:00+00:00",
            "reset_source": "cloudflare_daily_utc",
            "metric": "workers_ai_neurons",
            "window": "day",
        }
    }
    assert ledger.ingest_quota_observation("cloudflare-free", payload, accounting_key="response-1")

    payload["quota_observation"]["consumed"] = 20
    assert not ledger.ingest_quota_observation("cloudflare-free", payload, accounting_key="response-2")

    observation = ledger.get_quota_observation("cloudflare-free")
    assert observation["remaining"] == 0
    assert observation["block_reason"] == "local_conservative_limit"
    assert observation["blocked_until"] == "2026-10-04T00:00:00+00:00"


def test_router_rejects_route_after_local_conservative_exhaustion(tmp_path):
    ledger = ResourceLedger(tmp_path / "router-quota.sqlite3")
    ledger.register_resource(
        "fake-free",
        provider_id="fake",
        provider_binding_id="fake:free",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="fake-account",
        cost_minor=0,
        metadata={
            "model_id": "fake-model",
            "billing_mode": "recurring_allowance",
            "overage_policy": "hard_stop",
            "no_charge_guaranteed": True,
        },
    )
    ledger.observe("fake-free", available=1, health="healthy")
    payload = {
        "quota_observation": {
            "unit": "requests",
            "limit": 1,
            "consumed": 1,
            "quota_authority": "derived_conservative",
            "evidence_mode": "derived_conservative",
            "period_id": "2026-10-03",
            "reset_at": "2026-10-04T00:00:00+00:00",
            "reset_source": "fake_daily_utc",
            "metric": "fake_requests",
            "window": "day",
        }
    }
    assert ledger.ingest_quota_observation("fake-free", payload, accounting_key="request-1")

    with pytest.raises(NoRoute):
        ResourceRouter(ledger).choose(
            RouteRequest(capabilities={"text"}, allowed_providers={"fake"})
        )
