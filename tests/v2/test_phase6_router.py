from datetime import datetime, timedelta, timezone
import math

import pytest

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest
from src.dev_agent.resources.survival import SurvivalGovernor, SurvivalMode, SurvivalSnapshot


def _ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "router.sqlite3")
    ledger.register_resource("private", provider_id="ollama", native_unit="request", capacity=10, capabilities=["text", "tool_call"], sensitivity="sensitive", cost_minor=0)
    ledger.register_resource("cloud", provider_id="gemini", native_unit="request", capacity=10, capabilities=["text", "tool_call"], sensitivity="internal", cost_minor=20)
    ledger.observe("private", available=8, health="healthy")
    ledger.observe("cloud", available=8, health="healthy")
    return ledger


def test_router_prioritizes_privacy_over_cost_and_filters_capabilities(tmp_path):
    ledger = _ledger(tmp_path)
    router = ResourceRouter(ledger)
    selection = router.choose(RouteRequest(capabilities={"tool_call"}, sensitivity="sensitive", max_cost_minor=100))
    assert selection.resource_id == "private"
    assert selection.provider_id == "ollama"


def test_router_exposes_provider_binding_and_model_from_resource_metadata(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "mistral-small",
        provider_id="mistral",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        sensitivity="normal",
        cost_minor=0,
        metadata={"provider_binding_id": "mistral:small", "model_id": "mistral-small-latest"},
    )
    ledger.observe("mistral-small", available=1, health="healthy")

    selection = ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, sensitivity="normal"))

    assert selection.provider_id == "mistral"
    assert selection.provider_binding_id == "mistral:small"
    assert selection.model_id == "mistral-small-latest"


def test_router_excludes_open_circuit_and_reports_no_route(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_provider_failure("ollama", threshold=1, cooldown_seconds=60)
    router = ResourceRouter(ledger)
    with pytest.raises(NoRoute, match="no eligible resource"):
        router.choose(RouteRequest(capabilities={"tool_call"}, sensitivity="sensitive", max_cost_minor=100))


def test_survival_mode_is_deterministic_and_not_model_selected():
    governor = SurvivalGovernor()
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=100, recovery_remaining_minor=20, healthy_resources=2)).mode is SurvivalMode.NORMAL
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=10, recovery_remaining_minor=20, healthy_resources=1)).mode is SurvivalMode.CONSERVE
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=0, recovery_remaining_minor=20, healthy_resources=0)).mode is SurvivalMode.SURVIVAL
def test_router_rejects_stale_observation_when_max_age_is_set(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource("old", provider_id="old", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    ledger.observe("old", available=1, health="healthy", observed_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, max_observation_age_seconds=60))


def test_router_rejects_future_dated_observation_when_max_age_is_set(tmp_path):
    ledger = ResourceLedger(tmp_path / "future-observation.sqlite3")
    ledger.register_resource("future", provider_id="future", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    ledger.observe("future", available=1, health="healthy", observed_at=future)
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, max_observation_age_seconds=60))


def test_router_rejects_future_dated_observation_even_without_age_override(tmp_path):
    ledger = ResourceLedger(tmp_path / "future-observation-no-age.sqlite3")
    ledger.register_resource("future", provider_id="future", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    ledger.observe("future", available=1, health="healthy", observed_at=future)
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, max_observation_age_seconds=None))


def test_router_rejects_stale_quota_observation_for_quota_domain(tmp_path):
    ledger = ResourceLedger(tmp_path / "stale-quota.sqlite3")
    ledger.register_resource(
        "cloud",
        provider_id="gemini",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        quota_domain="google-project-123",
        cost_minor=0,
    )
    ledger.observe("cloud", available=10, health="healthy")
    ledger.observe_quota(
        "cloud",
        request_limit=100,
        request_remaining=90,
        observed_at="2020-01-01T00:00:00+00:00",
    )

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))


def test_router_prefers_higher_fresh_quota_before_cost(tmp_path):
    ledger = ResourceLedger(tmp_path / "quota-priority.sqlite3")
    for resource_id, remaining in (("a", 10), ("b", 90)):
        ledger.register_resource(
            resource_id,
            provider_id=resource_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            quota_domain=f"domain-{resource_id}",
            cost_minor=0,
        )
        ledger.observe(resource_id, available=10, health="healthy")
        ledger.observe_quota(
            resource_id,
            request_limit=100,
            request_remaining=remaining,
        )

    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "b"


def test_router_uses_conservative_shared_domain_headroom_without_summing_credentials(tmp_path):
    ledger = ResourceLedger(tmp_path / "shared-quota-priority.sqlite3")
    for resource_id, cost_minor, remaining in (("credential-a", 10, 90), ("credential-b", 0, 80)):
        ledger.register_resource(
            resource_id,
            provider_id=resource_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=cost_minor,
            quota_domain="shared-domain",
        )
        ledger.observe(resource_id, available=10, health="healthy")
        ledger.observe_quota(resource_id, request_limit=100, request_remaining=remaining)

    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "credential-b"


def test_router_snapshot_batches_resource_and_quota_reads(tmp_path):
    ledger = ResourceLedger(tmp_path / "routing-snapshot.sqlite3")
    for resource_id, remaining in (("a", 80), ("b", 60)):
        ledger.register_resource(
            resource_id,
            provider_id=resource_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=0,
            quota_domain="shared-domain",
        )
        ledger.observe(resource_id, available=10, health="healthy")
        ledger.observe_quota(resource_id, request_limit=100, request_remaining=remaining)

    statements = []
    ledger.connection.set_trace_callback(statements.append)
    snapshot = ResourceRouter(ledger).snapshot()
    ledger.connection.set_trace_callback(None)

    assert len(snapshot.resources) == 2
    assert {row["resource_id"] for row in snapshot.quota_observations_by_domain["shared-domain"]} == {"a", "b"}
    assert sum("FROM resources" in statement for statement in statements) == 1
    assert sum("FROM quota_observations" in statement for statement in statements) == 1


def test_router_selection_can_use_snapshot_without_reloading_ledger(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    router = ResourceRouter(ledger)
    snapshot = router.snapshot()

    def unexpected_read():
        raise AssertionError("selection must use the supplied snapshot")

    monkeypatch.setattr(ledger, "list_resources", unexpected_read)
    monkeypatch.setattr(ledger, "list_quota_observations", lambda **_: unexpected_read())

    selection = router.choose(RouteRequest(capabilities={"tool_call"}, sensitivity="sensitive"), snapshot=snapshot)
    assert selection.resource_id == "private"


def test_router_uses_generic_quota_headroom_for_neurons(tmp_path):
    ledger = ResourceLedger(tmp_path / "generic-quota-routing.sqlite3")
    for resource_id, remaining in (("low", 10), ("high", 80)):
        ledger.register_resource(
            resource_id,
            provider_id=resource_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=0,
            quota_domain=f"domain-{resource_id}",
        )
        ledger.observe(resource_id, available=10, health="healthy")
        ledger.observe_quota(resource_id, unit="neurons", limit=100, remaining=remaining)

    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "high"


def test_router_rejects_resource_at_concurrency_limit(tmp_path):
    ledger = ResourceLedger(tmp_path / "concurrency-limit.sqlite3")
    ledger.register_resource("busy", provider_id="busy", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("busy", available=10, health="healthy", inflight=2, concurrency_limit=2)

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))


def test_router_enforces_max_latency_from_resource_metadata(tmp_path):
    ledger = ResourceLedger(tmp_path / "latency-filter.sqlite3")
    ledger.register_resource("fast", provider_id="fast", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0, metadata={"latency_ms": 40})
    ledger.register_resource("slow", provider_id="slow", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0, metadata={"latency_ms": 200})
    ledger.observe("fast", available=1, health="healthy")
    ledger.observe("slow", available=1, health="healthy")
    router = ResourceRouter(ledger)
    assert router.choose(RouteRequest(capabilities={"text"}, max_latency_ms=100)).resource_id == "fast"
    with pytest.raises(NoRoute, match="no eligible resource"):
        router.choose(RouteRequest(capabilities={"text"}, max_latency_ms=10))


@pytest.mark.parametrize(
    ("field", "value"),
    (("max_cost_minor", -1), ("max_latency_ms", True), ("max_observation_age_seconds", math.nan)),
)
def test_route_request_rejects_invalid_limits(field, value):
    with pytest.raises(ValueError, match=field):
        RouteRequest(**{field: value})
