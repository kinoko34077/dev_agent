from datetime import datetime, timezone

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.scheduler import MaintenanceMode, QuotaWakeScheduler, WorkerRunner
from src.dev_agent.scheduler.quota import QuotaWakeScheduler


def test_quota_scheduler_returns_earliest_reset_without_reviving_provider(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "gemini",
        provider_id="gemini",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="google-project",
    )
    ledger.observe_quota(
        "gemini",
        metric="rpd",
        window="day",
        unit="requests",
        request_limit=100,
        request_remaining=0,
        blocked_until="2026-09-10T12:00:00+00:00",
        block_reason="quota",
    )
    scheduler = QuotaWakeScheduler(ledger)

    assert scheduler.next_wake_at(now=datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)) == datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    assert scheduler.due_domains(now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)) == ("google-project",)
    # The scheduler only exposes a due reset.  A provider probe must publish
    # a fresh unblocked observation before normal routing can use it again.
    assert ledger.get_quota_observation("gemini")["block_reason"] == "quota"


def test_quota_scheduler_ignores_unknown_and_authorization_blocks(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    for resource_id, reason, blocked_until in (
        ("unknown", "transport", None),
        ("auth", "authorization", "2026-09-10T12:00:00+00:00"),
    ):
        ledger.register_resource(
            resource_id,
            provider_id="provider",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            quota_domain=resource_id,
        )
        ledger.observe_quota(
            resource_id,
            metric="quota",
            window="unknown",
            unit="requests",
            block_reason=reason,
            blocked_until=blocked_until,
        )

    scheduler = QuotaWakeScheduler(ledger)
    assert scheduler.next_wake_at(now=datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)) is None
    assert scheduler.due_domains(now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)) == ()


def test_scheduler_package_exports_are_consistent():
    assert MaintenanceMode.__name__ == "MaintenanceMode"
    assert QuotaWakeScheduler.__name__ == "QuotaWakeScheduler"
    assert WorkerRunner.__name__ == "WorkerRunner"
