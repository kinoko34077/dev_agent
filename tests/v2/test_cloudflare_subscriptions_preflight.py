"""Offline tests for the Cloudflare subscription plan *hint* read path."""

from __future__ import annotations

import json
from urllib.error import HTTPError
from io import BytesIO

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare.subscription_introspection import (
    CloudflareSubscriptionsClient,
    CloudflareSubscriptionsError,
)


class _Response:
    def __init__(self, payload):
        self.content = json.dumps(payload).encode()
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, n=-1):
        return self.content if n < 0 else self.content[:n]


def test_missing_existing_credentials_never_calls_network(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("network forbidden before auth")

    with pytest.raises(ProviderError) as raised:
        CloudflareSubscriptionsClient(opener=forbidden).read_subscriptions()

    assert raised.value.category == "authentication"
    assert attempts == []


def test_get_subscription_billing_read_redacts_identifiers_and_never_admits():
    calls = []

    def opener(request, timeout):
        calls.append({
            "url": request.full_url, "method": request.method,
            "auth": dict((k.lower(), v) for k, v in request.header_items()).get("authorization"),
            "body": request.data, "timeout": timeout,
        })
        return _Response({
            "success": True,
            "result_info": {"total_count": 2},
            "result": [
                {
                    "id": "super-sensitive-subscription",
                    "state": "Paid",
                    "rate_plan": {"public_name": "Workers Paid", "id": "private-plan", "scope": "account"},
                    "currency": "USD",
                    "price": 975,
                    "billing_email": "secret@example.test",
                },
                {"id": "different-id", "state": "Provisioned", "rate_plan": {"public_name": "Custom private plan"}},
            ],
        })

    result = CloudflareSubscriptionsClient(
        account_id="sensitive-account",
        api_token="sensitive-token",
        opener=opener, timeout_seconds=4,
    ).read_subscriptions()

    assert calls == [{
        "url": "https://api.cloudflare.com/client/v4/accounts/sensitive-account/subscriptions",
        "method": "GET",
        "auth": "Bearer sensitive-token",
        "body": None,
        "timeout": 4.0,
    }]
    assert result["evidence_type"] == "cloudflare_account_subscriptions_review_only"
    assert result["subscription_count_returned"] == 2
    assert result["listing_complete_by_count"] is True
    assert result["admission_ready"] is False
    assert result["trusted_no_charge"] is False
    assert result["plan_hints"] == [
        {"plan_label_hint": "WORKERS_PAID_LABEL_ONLY", "state_hint": "Paid"},
        {"plan_label_hint": "UNRECOGNIZED", "state_hint": "Provisioned"},
    ]
    printed = json.dumps(result)
    for secret in (
        "sensitive-account", "sensitive-token", "super-sensitive-subscription",
        "different-id", "private-plan", "secret@example.test", "975",
        "Custom private plan",
    ):
        assert secret not in printed


def test_empty_listing_never_proves_free_and_count_absent_is_incomplete():
    result = CloudflareSubscriptionsClient(
        account_id="account", api_token="token",
        opener=lambda *_args, **_kw: _Response({"success": True, "result": []}),
    ).read_subscriptions()
    assert result["subscription_count_returned"] == 0
    assert result["listing_complete_by_count"] is False
    assert result["trusted_no_charge"] is False
    assert result["admission_ready"] is False


@pytest.mark.parametrize("payload", (
    {"success": False, "result": []},
    {"success": True, "result": {}},
    {"success": True, "result": [{}] * 65},
    {"success": True, "result": ["not-a-subscription"]},
    {"success": True, "result": [{"rate_plan": "not-object"}]},
))
def test_malformed_or_excess_subscriptions_fail_closed(payload):
    client = CloudflareSubscriptionsClient(
        account_id="account", api_token="token",
        opener=lambda *_args, **_kw: _Response(payload),
    )
    with pytest.raises(CloudflareSubscriptionsError):
        client.read_subscriptions()


def test_unknown_labels_and_malformed_states_never_become_free():
    doc = {"success": True, "result_info": {"total_count": 1}, "result": [
        {"state": {"untrusted": "state"}, "rate_plan": {"public_name": "Workers Free unlimited"}},
    ]}
    result = CloudflareSubscriptionsClient(
        account_id="account", api_token="token",
        opener=lambda *_args, **_kw: _Response(doc),
    ).read_subscriptions()
    assert result["plan_hints"] == [{"plan_label_hint": "UNRECOGNIZED", "state_hint": "UNKNOWN"}]
    assert result["admission_ready"] is False


def test_billing_read_denial_has_no_automatic_fallback():
    def denied(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, BytesIO(b'{"error":{"message":"Forbidden"}}'))

    client = CloudflareSubscriptionsClient(
        account_id="account", api_token="token", opener=denied,
    )
    with pytest.raises(ProviderError) as raised:
        client.read_subscriptions()
    assert raised.value.category == "authorization"
    assert raised.value.retryable is False


def test_cli_missing_authority_returns_redacted_boundary(monkeypatch, capsys):
    from scripts import probe_cloudflare_subscriptions

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert probe_cloudflare_subscriptions.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "AUTH_REQUIRED"
    assert result["network_called"] is False
    assert "admission_ready" not in result


def test_cli_remote_401_is_not_misreported_as_no_network(monkeypatch, capsys):
    from scripts import probe_cloudflare_subscriptions

    class _Rejected:
        def read_subscriptions(self):
            raise ProviderError(
                "do not reflect upstream details",
                category="authentication",
                retryable=False,
                http_status=401,
            )

    monkeypatch.setattr(probe_cloudflare_subscriptions, "CloudflareSubscriptionsClient", _Rejected)
    assert probe_cloudflare_subscriptions.main() == 3
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "AUTH_REJECTED"
    assert result["network_called"] is True
    assert "upstream details" not in output


def test_cli_transport_failure_preserves_bounded_diagnostics_without_claiming_boundary(monkeypatch, capsys):
    from scripts import probe_cloudflare_subscriptions

    class _WinSockDenied(PermissionError):
        def __init__(self):
            super().__init__(13, "private socket detail")
            self.winerror = 10013

    class _TransportFailure:
        def read_subscriptions(self):
            failure = ProviderError(
                "private transport detail with token=never-print-this",
                category="transport",
                retryable=True,
            )
            failure.__cause__ = _WinSockDenied()
            raise failure

    monkeypatch.setattr(probe_cloudflare_subscriptions, "CloudflareSubscriptionsClient", _TransportFailure)
    assert probe_cloudflare_subscriptions.main() == 1
    output = capsys.readouterr().out
    result = json.loads(output)

    assert result["status"] == "TRANSPORT_FAILED"
    assert result["request_attempted"] is True
    assert result["provider_response_received"] is False
    assert result["transport_diagnostics"] == {
        "transport_failure_category": "transport_unclassified",
        "transport_stage": "unknown",
        "transport_exception_type": "_WinSockDenied",
        "transport_errno": 13,
        "transport_winerror": 10013,
    }
    assert "private socket detail" not in output
    assert "never-print-this" not in output
