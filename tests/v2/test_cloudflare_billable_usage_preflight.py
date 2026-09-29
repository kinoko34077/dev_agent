import importlib
import json

import pytest

from src.dev_agent.providers.base import ProviderError


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


def _client_class():
    return importlib.import_module(
        "src.dev_agent.providers.cloudflare.billing_introspection"
    ).CloudflareBillableUsageClient


def test_cloudflare_billable_usage_requires_existing_credentials_before_network(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    called = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called without existing Cloudflare credentials")

    client = _client_class()(opener=unexpected_open)
    with pytest.raises(ProviderError) as caught:
        client.read_day("2026-09-29")

    assert called is False
    assert caught.value.category == "authentication"


def test_cloudflare_billable_usage_preserves_raw_neuron_records_without_deriving_headroom():
    captured = []

    def fake_open(request, timeout):
        captured.append(
            {
                "url": request.full_url,
                "method": request.method,
                "headers": {k.lower(): v for k, v in request.header_items()},
                "timeout": timeout,
                "data": request.data,
            }
        )
        return _Response(
            {
                "success": True,
                "result": [
                    {
                        "ChargePeriodStart": "2026-09-29T00:00:00Z",
                        "ChargePeriodEnd": "2026-09-30T00:00:00Z",
                        "ConsumedQuantity": 1234,
                        "ConsumedUnit": "Neurons",
                        "x_BillableMetricId": "workers_ai_neurons",
                        "x_BillableMetricName": "Workers AI Neurons",
                        "x_ProductFamilyName": "Workers AI",
                        "BilledCost": 0,
                    },
                    {
                        "ChargePeriodStart": "2026-09-29T00:00:00Z",
                        "ChargePeriodEnd": "2026-09-30T00:00:00Z",
                        "ConsumedQuantity": 500,
                        "ConsumedUnit": "Requests",
                        "x_BillableMetricId": "workers_standard_requests",
                        "x_ProductFamilyName": "Workers",
                    },
                ],
            }
        )

    state = _client_class()(
        account_id="account-123",
        api_token="existing-token",
        opener=fake_open,
        timeout_seconds=4,
    ).read_day("2026-09-29")

    assert state["authority"] == "cloudflare-billable-usage-alpha"
    assert state["restricted_endpoint"] is True
    assert state["admission_ready"] is False
    assert state["records"][0]["ConsumedQuantity"] == 1234
    assert state["neuron_records"][0]["x_BillableMetricId"] == "workers_ai_neurons"
    assert "remaining" not in state
    assert "headroom" not in state
    assert len(captured) == 1
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/account-123/billable/usage"
        "?from=2026-09-29&to=2026-09-29"
    )
    assert captured[0]["headers"]["authorization"] == "Bearer existing-token"
    assert captured[0]["timeout"] == 4.0
    assert captured[0]["data"] is None
    assert "existing-token" not in json.dumps(state)
