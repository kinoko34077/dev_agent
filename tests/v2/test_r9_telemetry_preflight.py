import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.openrouter.introspection import OpenRouterIntrospectionClient
from src.dev_agent.resources.google_monitoring_quota import (
    FREE_TIER_INPUT_TOKEN_LIMIT,
    FREE_TIER_INPUT_TOKEN_USAGE,
    FREE_TIER_REQUEST_LIMIT,
    FREE_TIER_REQUEST_USAGE,
    GoogleMonitoringQuotaClient,
    MonitoringAuthRequired,
    MonitoringQuotaError,
)


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n=-1):
        return self.payload if n < 0 else self.payload[:n]


def test_google_monitoring_preflight_stops_before_network_when_auth_is_missing(monkeypatch):
    monkeypatch.delenv("DEV_AGENT_GCP_MONITORING_ACCESS_TOKEN", raising=False)
    called = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called without Monitoring authorization")

    client = GoogleMonitoringQuotaClient(
        project_id="gemini-free-project",
        model_id="gemini-3.8-flash",
        opener=unexpected_open,
    )

    with pytest.raises(MonitoringAuthRequired) as caught:
        client.read_free_tier_snapshot(
            start_time=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 29, 0, 5, tzinfo=timezone.utc),
        )

    assert called is False
    assert caught.value.permission == "monitoring.timeSeries.list"
    assert caught.value.scope == "https://www.googleapis.com/auth/monitoring.read"
    assert caught.value.token_env == "DEV_AGENT_GCP_MONITORING_ACCESS_TOKEN"
    assert "token" not in str(caught.value).lower()


def test_google_monitoring_reads_bounded_first_party_series_without_deriving_remaining(monkeypatch):
    captured = []

    def fake_open(request, timeout):
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        captured.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "timeout": timeout,
                "query": query,
            }
        )
        metric_filter = query["filter"][0]
        metric_type = metric_filter.split('metric.type = "', 1)[1].split('"', 1)[0]
        value = "100" if metric_type.endswith("/limit") else "7"
        return _Response(
            {
                "timeSeries": [
                    {
                        "metric": {
                            "type": metric_type,
                            "labels": {"model": "gemini-3.8-flash", "limit_name": "example-limit"},
                        },
                        "resource": {"type": "generativelanguage.googleapis.com/Location", "labels": {"location": "global"}},
                        "points": [
                            {
                                "interval": {"endTime": "2026-09-29T00:04:00Z"},
                                "value": {"int64Value": value},
                            }
                        ],
                    }
                ]
            }
        )

    monkeypatch.setenv("DEV_AGENT_GCP_MONITORING_ACCESS_TOKEN", "secret-monitoring-token")
    client = GoogleMonitoringQuotaClient(
        project_id="gemini-free-project",
        model_id="gemini-3.8-flash",
        opener=fake_open,
        timeout_seconds=4,
    )
    snapshot = client.read_free_tier_snapshot(
        start_time=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 29, 0, 5, tzinfo=timezone.utc),
    )

    assert set(snapshot["metrics"]) == {
        FREE_TIER_REQUEST_LIMIT,
        FREE_TIER_REQUEST_USAGE,
        FREE_TIER_INPUT_TOKEN_LIMIT,
        FREE_TIER_INPUT_TOKEN_USAGE,
    }
    assert len(captured) == 4
    assert all(item["headers"]["Authorization"] == "Bearer secret-monitoring-token" for item in captured)
    assert all(item["timeout"] == 4.0 for item in captured)
    assert all('metric.labels.model = "gemini-3.8-flash"' in item["query"]["filter"][0] for item in captured)
    assert snapshot["authority"] == "google-cloud-monitoring"
    assert snapshot["admission_ready"] is False
    assert "remaining" not in json.dumps(snapshot).lower()
    assert "secret-monitoring-token" not in json.dumps(snapshot)


def test_google_monitoring_fails_closed_on_malformed_timeseries(monkeypatch):
    monkeypatch.setenv("DEV_AGENT_GCP_MONITORING_ACCESS_TOKEN", "secret")
    client = GoogleMonitoringQuotaClient(
        project_id="p",
        model_id="m",
        opener=lambda *_args, **_kwargs: _Response({"timeSeries": "not-a-list"}),
    )

    with pytest.raises(MonitoringQuotaError):
        client.read_metric(
            FREE_TIER_REQUEST_LIMIT,
            start_time=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 29, 0, 5, tzinfo=timezone.utc),
        )


def test_openrouter_introspection_keeps_spend_controls_separate_from_rate_quota(monkeypatch):
    captured = []

    def fake_open(request, timeout):
        captured.append((request.full_url, dict(request.header_items()), timeout))
        if request.full_url.endswith("/key"):
            return _Response(
                {
                    "data": {
                        "label": "dev-agent",
                        "limit": 10,
                        "limit_remaining": 8.5,
                        "limit_reset": "daily",
                        "usage": 1.5,
                        "is_free_tier": True,
                        "rate_limit": {"requests": 20, "interval": "10s"},
                    }
                }
            )
        if request.full_url.endswith("/credits"):
            return _Response({"data": {"total_credits": 5.0, "total_usage": 1.0}})
        if request.full_url.endswith("/models"):
            return _Response(
                {
                    "data": [
                        {
                            "id": "example/free-agent-model:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                            "supported_parameters": ["tools", "structured_outputs"],
                            "context_length": 131072,
                        },
                        {
                            "id": "example/paid-model",
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                            "supported_parameters": ["tools"],
                        },
                    ]
                }
            )
        raise AssertionError(request.full_url)

    client = OpenRouterIntrospectionClient(api_key="existing-key", opener=fake_open, timeout_seconds=3)
    state = client.read_state(required_parameters={"tools"})

    assert state["spend_control"]["limit"] == 10
    assert state["spend_control"]["limit_remaining"] == 8.5
    assert state["spend_control"]["limit_reset"] == "daily"
    assert state["spend_control"]["is_free_tier"] is True
    assert "quota_observation" not in state
    assert "rate_limit" not in state["spend_control"]
    assert state["credits"] == {"total_credits": 5.0, "total_usage": 1.0}
    assert [item["id"] for item in state["zero_price_candidates"]] == ["example/free-agent-model:free"]
    assert state["zero_price_candidates"][0]["supported_parameters"] == ["structured_outputs", "tools"]
    assert state["admission_ready"] is False
    assert len(captured) == 3
    assert all(headers["Authorization"] == "Bearer existing-key" for _url, headers, _timeout in captured)
    assert all(timeout == 3.0 for _url, _headers, timeout in captured)
    assert "existing-key" not in json.dumps(state)


def test_openrouter_introspection_requires_existing_key_before_network(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    called = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    client = OpenRouterIntrospectionClient(opener=unexpected_open)
    with pytest.raises(ProviderError) as caught:
        client.read_key_metadata()

    assert called is False
    assert caught.value.category == "authentication"
