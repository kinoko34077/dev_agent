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


def test_provider_health_failure_is_scoped_to_selected_resource(tmp_path):
    ledger = ResourceLedger(tmp_path / "scoped-health.sqlite3")
    for resource_id, binding in (("gemini-worker", "gemini:worker"), ("gemini-core", "gemini:core")):
        ledger.register_resource(
            resource_id,
            provider_id="gemini",
            provider_binding_id=binding,
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            metadata={"provider_binding_id": binding, "model_id": resource_id},
        )
        ledger.observe(resource_id, available=1, health="healthy")

    ledger.record_provider_failure("gemini", resource_id="gemini-worker", threshold=1, cooldown_seconds=60)

    assert ledger.get_resource("gemini-worker")["consecutive_failures"] == 1
    assert ledger.get_resource("gemini-core")["consecutive_failures"] == 0
    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "gemini-core"


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


def test_router_allows_one_explicit_unknown_quota_bootstrap_for_trusted_free_resource(tmp_path):
    ledger = ResourceLedger(tmp_path / "unknown-quota-bootstrap.sqlite3")
    ledger.register_resource(
        "gemini:worker",
        provider_id="gemini",
        provider_binding_id="gemini:worker",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="google-project",
        cost_minor=0,
        metadata={
            "provider_binding_id": "gemini:worker",
            "model_id": "gemini-3.5-flash-lite",
            "billing_authority": "trusted_catalog",
            "billing_expires_at": "2026-12-31T00:00:00+00:00",
            "billing_mode": "recurring_allowance",
            "overage_policy": "hard_stop",
            "no_charge_guaranteed": True,
            "intelligence_tier": "L1",
        },
    )
    ledger.observe("gemini:worker", available=1, health="degraded", confidence=0.0)

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))

    selection = ResourceRouter(ledger).choose(
        RouteRequest(capabilities={"text"}, allow_unknown_quota=True)
    )
    assert selection.resource_id == "gemini:worker"


def test_router_never_bootstraps_unknown_quota_for_untrusted_zero_cost_resource(tmp_path):
    ledger = ResourceLedger(tmp_path / "unknown-quota-untrusted.sqlite3")
    ledger.register_resource(
        "unqualified",
        provider_id="remote",
        provider_binding_id="remote:unknown",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="remote-project",
        cost_minor=0,
    )
    ledger.observe("unqualified", available=1, health="degraded", confidence=0.0)

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, allow_unknown_quota=True))


def test_router_does_not_use_unknown_bootstrap_to_bypass_a_quota_block(tmp_path):
    ledger = ResourceLedger(tmp_path / "unknown-quota-blocked.sqlite3")
    ledger.register_resource(
        "blocked-free",
        provider_id="gemini",
        provider_binding_id="gemini:worker",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="gemini-project",
        cost_minor=0,
        metadata={
            "billing_authority": "trusted_catalog",
            "billing_mode": "free_fixed",
            "overage_policy": "hard_stop",
            "no_charge_guaranteed": True,
        },
    )
    ledger.observe("blocked-free", available=1, health="degraded", confidence=0.0)
    ledger.observe_quota(
        "blocked-free",
        unit="requests",
        metric="rpm",
        window="minute",
        request_limit=10,
        request_remaining=0,
        blocked_until="2099-01-01T00:00:00+00:00",
        block_reason="rate_limit",
    )

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, allow_unknown_quota=True))


def test_router_rejects_provider_block_until_a_new_observation_arrives(tmp_path):
    ledger = ResourceLedger(tmp_path / "blocked-quota.sqlite3")
    ledger.register_resource(
        "blocked",
        provider_id="gemini",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        quota_domain="google-project-123",
        cost_minor=0,
    )
    ledger.observe("blocked", available=10, health="healthy")
    ledger.observe_quota(
        "blocked",
        unit="requests",
        metric="rpm",
        window="minute",
        request_limit=100,
        request_remaining=100,
        blocked_until="2000-01-01T00:00:00+00:00",
        block_reason="rate_limit",
    )

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))

    ledger.observe_quota("blocked", unit="requests", metric="rpm", window="minute", request_limit=100, request_remaining=90)
    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "blocked"


def test_router_rejects_authorization_block_without_reset(tmp_path):
    ledger = ResourceLedger(tmp_path / "authorization-block.sqlite3")
    ledger.register_resource("blocked", provider_id="groq", native_unit="request", capacity=1, capabilities=["text"], quota_domain="groq-project", cost_minor=0)
    ledger.observe("blocked", available=1, health="healthy")
    ledger.observe_quota("blocked", unit="requests", request_limit=10, request_remaining=10, block_reason="authorization")
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))


def test_router_accepts_new_observation_as_explicit_quota_unblock(tmp_path):
    ledger = ResourceLedger(tmp_path / "quota-unblock.sqlite3")
    ledger.register_resource("cloud", provider_id="gemini", native_unit="request", capacity=1, capabilities=["text"], quota_domain="project", cost_minor=0)
    ledger.observe("cloud", available=1, health="healthy")
    ledger.observe_quota("cloud", unit="requests", metric="rpm", window="minute", request_limit=10, request_remaining=0, blocked_until="2099-01-01T00:00:00+00:00", block_reason="rate_limit")
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))
    ledger.observe_quota("cloud", unit="requests", metric="rpm", window="minute", request_limit=10, request_remaining=9)
    assert ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"})).resource_id == "cloud"


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


# P0-3 regression: billing authority expiry is re-evaluated at dispatch time
def test_router_rejects_resource_with_expired_billing(tmp_path):
    ledger = ResourceLedger(tmp_path / "billing-expiry.sqlite3")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ledger.register_resource(
        "expired",
        provider_id="gemini",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
        metadata={
            "billing_authority": "trusted_catalog",
            "billing_mode": "recurring_allowance",
            "no_charge_guaranteed": True,
            "billing_verified_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
            "billing_expires_at": past,
        },
    )
    ledger.observe("expired", available=10, health="healthy")

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))


# P0-1 regression: qualification fail-open — remote providers without a current
# qualification record must always be rejected regardless of persisted metadata.

class _NullQualificationResolver:
    """Returns None for every query — simulates no qualification record on file."""

    def resolve(self, *_args, **_kwargs):
        return None


def test_remote_provider_without_qualification_is_rejected(tmp_path):
    ledger = ResourceLedger(tmp_path / "no-qual.sqlite3")
    ledger.register_resource(
        "cloud",
        provider_id="gemini",
        provider_binding_id="gemini:worker",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
        metadata={"provider_binding_id": "gemini:worker", "model_id": "gemini-3.5-flash-lite"},
    )
    ledger.observe("cloud", available=10, health="healthy")

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger, qualification_resolver=_NullQualificationResolver()).choose(
            RouteRequest(capabilities={"text"})
        )


def test_persisted_resource_capabilities_alone_do_not_grant_routing(tmp_path):
    """Capability fields in the resource row are never authoritative without qualification."""
    ledger = ResourceLedger(tmp_path / "persisted-caps.sqlite3")
    ledger.register_resource(
        "cloud",
        provider_id="openrouter",
        provider_binding_id="openrouter:worker",
        native_unit="request",
        capacity=10,
        capabilities=["text", "tool_call"],
        cost_minor=0,
        metadata={"provider_binding_id": "openrouter:worker", "model_id": "some-model"},
    )
    ledger.observe("cloud", available=10, health="healthy")

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger, qualification_resolver=_NullQualificationResolver()).choose(
            RouteRequest(capabilities={"text"})
        )


def test_remote_provider_with_blank_binding_id_is_rejected(tmp_path):
    """A remote resource with no binding_id is rejected before the resolver is called."""
    ledger = ResourceLedger(tmp_path / "blank-binding.sqlite3")
    ledger.register_resource(
        "cloud",
        provider_id="groq",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
    )
    ledger.observe("cloud", available=10, health="healthy")

    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger, qualification_resolver=_NullQualificationResolver()).choose(
            RouteRequest(capabilities={"text"})
        )


def test_qualification_exempt_providers_route_without_qualification(tmp_path):
    """ollama and fake are exempt from the qualification gate."""
    ledger = ResourceLedger(tmp_path / "exempt-qual.sqlite3")
    for resource_id, provider_id in (("local", "ollama"), ("synthetic", "fake")):
        ledger.register_resource(
            resource_id,
            provider_id=provider_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            sensitivity="sensitive",
            cost_minor=0,
        )
        ledger.observe(resource_id, available=10, health="healthy")

    router = ResourceRouter(ledger, qualification_resolver=_NullQualificationResolver())
    selection = router.choose(RouteRequest(capabilities={"text"}, sensitivity="sensitive"))
    assert selection.provider_id in {"ollama", "fake"}


def test_router_accepts_resource_with_current_billing(tmp_path):
    ledger = ResourceLedger(tmp_path / "billing-current.sqlite3")
    future = (datetime.now(timezone.utc) + timedelta(days=28)).isoformat()
    ledger.register_resource(
        "current",
        provider_id="gemini",
        provider_binding_id="gemini:worker",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
        quota_domain="gemini-quota",
        metadata={
            "provider_binding_id": "gemini:worker",
            "model_id": "gemini-3.5-flash-lite",
            "billing_authority": "trusted_catalog",
            "billing_mode": "recurring_allowance",
            "overage_policy": "hard_stop",
            "no_charge_guaranteed": True,
            "billing_expires_at": future,
        },
    )
    ledger.observe("current", available=10, health="healthy")
    ledger.observe_quota("current", unit="requests", limit=100, remaining=80)

    selection = ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}))
    assert selection.resource_id == "current"
