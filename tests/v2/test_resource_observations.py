import pytest

from src.dev_agent.resources import ledger as ledger_module
from src.dev_agent.resources.ledger import ResourceLedger
from tests.v2.resource_test_support import ledger


def test_resource_ledger_persists_native_unit_observations(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_ledger.observe("local-qwen", available=87, health="degraded", confidence=0.8)
    reopened = ResourceLedger(tmp_path / "resources.sqlite3")
    resource = reopened.get_resource("local-qwen")
    assert resource["native_unit"] == "request"
    assert resource["available"] == 87
    assert resource["health"] == "degraded"
    assert resource["confidence"] == pytest.approx(0.8)


def test_resource_ledger_persists_quota_domain_identity(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-domain.sqlite3")
    spec = resource_ledger.register_resource(
        "gemini-free",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
        quota_domain="google-project-123",
    )

    assert spec.quota_domain == "google-project-123"
    assert resource_ledger.get_resource("gemini-free")["quota_domain"] == "google-project-123"
    assert resource_ledger.connection.execute("SELECT value FROM resource_schema_meta WHERE key='schema_version'").fetchone()[0] == "9"

    reopened = ResourceLedger(tmp_path / "quota-domain.sqlite3")
    assert reopened.get_resource("gemini-free")["quota_domain"] == "google-project-123"


def test_resource_ledger_persists_quota_observation_and_reloads_it(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-observation.sqlite3")
    resource_ledger.register_resource(
        "gemini-free",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
        quota_domain="google-project-123",
    )

    resource_ledger.observe_quota(
        "gemini-free",
        request_limit=100,
        request_remaining=80,
        token_limit=10000,
        token_remaining=8000,
        reset_at="2026-09-10T00:00:00+00:00",
        daily_remaining=500,
        concurrency_limit=4,
        confidence=0.9,
        source="provider-header",
    )

    observation = resource_ledger.get_quota_observation("gemini-free")
    assert observation["quota_domain"] == "google-project-123"
    assert observation["request_remaining"] == 80
    assert observation["token_remaining"] == 8000
    assert observation["concurrency_limit"] == pytest.approx(4)
    assert observation["confidence"] == pytest.approx(0.9)
    assert observation["source"] == "provider-header"

    reopened = ResourceLedger(tmp_path / "quota-observation.sqlite3")
    assert reopened.get_quota_observation("gemini-free")["request_remaining"] == 80


def test_resource_ledger_persists_generic_quota_units_and_authority(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "generic-quota.sqlite3")
    resource_ledger.register_resource(
        "cloudflare-free",
        provider_id="cloudflare",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        cost_minor=0,
        quota_domain="cloudflare-account",
    )

    resource_ledger.observe_quota(
        "cloudflare-free",
        unit="neurons",
        consumed=1234,
        authority="estimated",
        confidence=0.25,
        source="cloudflare-neuron-estimate",
    )

    observation = resource_ledger.get_quota_observation("cloudflare-free")
    assert observation["unit"] == "neurons"
    assert observation["consumed"] == 1234
    assert observation["limit"] is None
    assert observation["remaining"] is None
    assert observation["authority"] == "estimated"
    assert observation["source"] == "cloudflare-neuron-estimate"


def test_resource_ledger_persists_quota_window_and_block_state(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-block.sqlite3")
    resource_ledger.register_resource(
        "gemini-free",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
        quota_domain="google-project-123",
    )
    resource_ledger.observe_quota(
        "gemini-free",
        metric="rpm",
        unit="requests",
        window="minute",
        request_limit=10,
        request_remaining=0,
        reset_at="2026-09-10T01:00:00+00:00",
        reset_source="provider-header",
        blocked_until="2026-09-10T01:00:00+00:00",
        block_reason="rate_limit",
    )

    observation = resource_ledger.get_quota_observation("gemini-free")
    assert observation["metric"] == "rpm"
    assert observation["window"] == "minute"
    assert observation["reset_source"] == "provider-header"
    assert observation["blocked_until"] == "2026-09-10T01:00:00+00:00"
    assert observation["block_reason"] == "rate_limit"
    reopened = ResourceLedger(tmp_path / "quota-block.sqlite3")
    assert reopened.get_quota_observation("gemini-free")["block_reason"] == "rate_limit"


def test_resource_ledger_rejects_unknown_generic_quota_unit(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "generic-quota-validation.sqlite3")
    resource_ledger.register_resource(
        "resource",
        provider_id="provider",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        cost_minor=0,
        quota_domain="domain",
    )

    with pytest.raises(ValueError, match="unit"):
        resource_ledger.observe_quota("resource", unit="credits", consumed=1)


def test_resource_ledger_records_quota_block_without_losing_known_facts(tmp_path):
    from src.dev_agent.resources.quota_policy import QuotaBlockDecision

    resource_ledger = ResourceLedger(tmp_path / "quota-block-record.sqlite3")
    resource_ledger.register_resource(
        "cloud",
        provider_id="gemini",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        quota_domain="project",
    )
    resource_ledger.observe_quota("cloud", unit="requests", metric="rpm", window="minute", request_limit=10, request_remaining=2)
    assert resource_ledger.record_quota_block(
        "cloud",
        QuotaBlockDecision(
            block_reason="rate_limit",
            metric="rpm",
            window="minute",
            blocked_until="2099-01-01T00:00:00+00:00",
            reset_source="provider",
        ),
    )
    observation = resource_ledger.get_quota_observation("cloud")
    assert observation["request_remaining"] == 2
    assert observation["block_reason"] == "rate_limit"
    assert observation["blocked_until"] == "2099-01-01T00:00:00+00:00"


def test_resource_ledger_ingests_quota_block_fields_from_provider_response(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-block-ingest.sqlite3")
    resource_ledger.register_resource("cloud", provider_id="gemini", native_unit="request", capacity=1, capabilities=["text"], quota_domain="project")
    assert resource_ledger.ingest_quota_observation(
        "cloud",
        {
            "quota_observation": {
                "metric": "rpd",
                "unit": "requests",
                "window": "day",
                "request_limit": 100,
                "request_remaining": 0,
                "blocked_until": "2099-01-01T00:00:00+00:00",
                "block_reason": "quota",
                "reset_source": "provider",
            }
        },
    )
    observation = resource_ledger.get_quota_observation("cloud")
    assert observation["metric"] == "rpd"
    assert observation["window"] == "day"
    assert observation["block_reason"] == "quota"


def test_resource_ledger_lists_latest_quota_observation_per_resource_in_domain(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-domain-observations.sqlite3")
    for resource_id in ("credential-a", "credential-b"):
        resource_ledger.register_resource(
            resource_id,
            provider_id="provider",
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            quota_domain="shared-domain",
        )
    resource_ledger.observe_quota("credential-a", request_limit=100, request_remaining=90, source="old")
    resource_ledger.observe_quota("credential-a", request_limit=100, request_remaining=70, source="new")
    resource_ledger.observe_quota("credential-b", request_limit=100, request_remaining=80, source="other")

    observations = resource_ledger.list_quota_observations(quota_domain="shared-domain")

    assert [(item["resource_id"], item["request_remaining"]) for item in observations] == [
        ("credential-a", 70),
        ("credential-b", 80),
    ]


def test_quota_observation_timestamp_ties_use_ingestion_order(tmp_path, monkeypatch):
    identifiers = iter(("00000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000001"))
    monkeypatch.setattr(ledger_module, "uuid4", lambda: next(identifiers))
    resource_ledger = ResourceLedger(tmp_path / "quota-timestamp-ties.sqlite3")
    resource_ledger.register_resource(
        "credential",
        provider_id="provider",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        quota_domain="shared-domain",
    )

    for remaining in (90, 70, 80):
        resource_ledger.observe_quota(
            "credential",
            request_limit=100,
            request_remaining=remaining,
            observed_at="2026-01-01T00:00:00+00:00",
        )

    assert resource_ledger.get_quota_observation("credential")["request_remaining"] == 80


def test_quota_observation_rejects_invalid_limits_and_missing_domain(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "quota-validation.sqlite3")
    resource_ledger.register_resource(
        "gemini-free",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
        quota_domain="google-project-123",
    )
    with pytest.raises(ValueError, match="request_remaining"):
        resource_ledger.observe_quota("gemini-free", request_limit=10, request_remaining=11)

    resource_ledger.register_resource(
        "local",
        provider_id="ollama",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
    )
    with pytest.raises(ValueError, match="quota_domain"):
        resource_ledger.observe_quota("local", request_remaining=1)


def test_resource_observation_persists_operational_metrics(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "operational-observation.sqlite3")
    resource_ledger.register_resource(
        "gemini-free",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text"],
        quota_domain="google-project-123",
    )

    resource_ledger.observe(
        "gemini-free",
        available=8,
        health="healthy",
        quota_remaining_ratio=0.8,
        quota_reset_at="2026-09-10T00:00:00+00:00",
        latency_ewma_ms=42.5,
        failure_ewma=0.1,
        inflight=2,
        concurrency_limit=4,
    )

    resource = resource_ledger.get_resource("gemini-free")
    assert resource["quota_remaining_ratio"] == pytest.approx(0.8)
    assert resource["quota_reset_at"] == "2026-09-10T00:00:00+00:00"
    assert resource["latency_ewma_ms"] == pytest.approx(42.5)
    assert resource["failure_ewma"] == pytest.approx(0.1)
    assert resource["inflight"] == pytest.approx(2)
    assert resource["concurrency_limit"] == pytest.approx(4)


def test_resource_observation_cannot_exceed_registered_capacity(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    resource_ledger.register_resource("small", provider_id="local", native_unit="request", capacity=1, capabilities=["text"])
    with pytest.raises(ValueError, match="exceeds resource capacity"):
        resource_ledger.observe("small", available=2, health="healthy")
