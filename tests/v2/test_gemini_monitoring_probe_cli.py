import json
import sys

import scripts.probe_gemini_monitoring_quota as probe
from src.dev_agent.resources.google_monitoring_quota import MonitoringAuthRequired


def test_probe_reports_remote_monitoring_authorization_rejection(monkeypatch, capsys):
    class RejectingClient:
        def __init__(self, **_kwargs):
            pass

        def read_free_tier_snapshot(self, **_kwargs):
            raise MonitoringAuthRequired(network_called=True, http_status=403)

    monkeypatch.setattr(probe, "GoogleMonitoringQuotaClient", RejectingClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe_gemini_monitoring_quota.py",
            "--project-id",
            "project-1",
            "--model",
            "gemini-3.8-flash",
        ],
    )

    assert probe.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ACCESS_REQUIRED"
    assert payload["network_called"] is True
    assert payload["http_status"] == 403
    assert payload["required_permission"] == "monitoring.timeSeries.list"
    assert payload["required_oauth_scope"] == "https://www.googleapis.com/auth/monitoring.read"


def test_probe_reports_missing_monitoring_authorization_before_network(monkeypatch, capsys):
    class MissingClient:
        def __init__(self, **_kwargs):
            pass

        def read_free_tier_snapshot(self, **_kwargs):
            raise MonitoringAuthRequired(network_called=False, http_status=None)

    monkeypatch.setattr(probe, "GoogleMonitoringQuotaClient", MissingClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe_gemini_monitoring_quota.py",
            "--project-id",
            "project-1",
            "--model",
            "gemini-3.8-flash",
        ],
    )

    assert probe.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "AUTH_REQUIRED"
    assert payload["network_called"] is False
    assert payload["http_status"] is None
