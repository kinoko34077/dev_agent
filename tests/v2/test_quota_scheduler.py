from datetime import datetime, timezone

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.scheduler import (
    MaintenanceMode,
    QuotaProbeStatus,
    QuotaRequalificationCoordinator,
    QuotaWakeScheduler,
    WorkerRunner,
)
from src.dev_agent.scheduler.queue import DurableQueue


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


def _blocked_ledger(tmp_path, *, reason="rate_limit", blocked_until="2026-09-10T11:59:00+00:00"):
    ledger = ResourceLedger(tmp_path / "requalification.sqlite3")
    ledger.register_resource(
        "cloud",
        provider_id="gemini",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        quota_domain="project",
    )
    ledger.observe_quota(
        "cloud",
        unit="requests",
        metric="rpm",
        window="minute",
        request_limit=10,
        request_remaining=0,
        blocked_until=blocked_until,
        block_reason=reason,
        observed_at="2026-09-10T11:00:00+00:00",
    )
    return ledger


def test_quota_requalification_persists_fresh_observation_and_wakes_due_tasks(tmp_path):
    ledger = _blocked_ledger(tmp_path)
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("quota-task")
    item = queue.claim("quota-worker", lease_seconds=30)
    scheduler = QuotaWakeScheduler(ledger, queue)
    scheduler.park(
        item.task_id,
        worker_id="quota-worker",
        state_version=item.state_version,
        wake_at=datetime(2026, 9, 10, 11, 59, tzinfo=timezone.utc),
        quota_domain="project",
    )
    calls = []

    def probe(resource_id, quota_domain):
        calls.append((resource_id, quota_domain))
        return {"unit": "requests", "metric": "rpm", "window": "minute", "request_limit": 10, "request_remaining": 9}

    result = QuotaRequalificationCoordinator(ledger, scheduler).probe_once(
        "cloud",
        probe,
        now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert result.status is QuotaProbeStatus.REQUALIFIED
    assert result.observation_persisted is True
    assert result.woken_tasks == 1
    assert calls == [("cloud", "project")]
    assert ledger.get_quota_observation("cloud")["block_reason"] is None
    assert queue.snapshot("quota-task").state == "queued"


def test_quota_requalification_wakes_only_the_requalified_domain(tmp_path):
    ledger = ResourceLedger(tmp_path / "domain-scoped-wake.sqlite3")
    queue = DurableQueue(tmp_path / "domain-scoped-wake-queue.sqlite3")
    for resource_id, task_id, domain in (("cloud-a", "task-a", "project-a"), ("cloud-b", "task-b", "project-b")):
        ledger.register_resource(
            resource_id,
            provider_id="gemini",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            quota_domain=domain,
        )
        ledger.observe_quota(
            resource_id,
            unit="requests",
            metric="rpd",
            window="day",
            request_limit=10,
            request_remaining=0,
            blocked_until="2026-09-10T11:59:00+00:00",
            block_reason="quota",
            observed_at="2026-09-10T11:00:00+00:00",
        )
        queue.enqueue(task_id)
        item = queue.claim("quota-worker", lease_seconds=30)
        QuotaWakeScheduler(ledger, queue).park(
            task_id,
            worker_id="quota-worker",
            state_version=item.state_version,
            wake_at=datetime(2026, 9, 10, 11, 59, tzinfo=timezone.utc),
            quota_domain=domain,
        )

    scheduler = QuotaWakeScheduler(ledger, queue)
    calls = []

    def probe(resource_id, quota_domain):
        calls.append((resource_id, quota_domain))
        return {"unit": "requests", "metric": "rpd", "window": "day", "request_limit": 10, "request_remaining": 9}

    result = QuotaRequalificationCoordinator(ledger, scheduler).probe_once(
        "cloud-a",
        probe,
        now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert result.woken_tasks == 1
    assert calls == [("cloud-a", "project-a")]
    assert queue.snapshot("task-a").state == "queued"
    assert queue.snapshot("task-b").state == "waiting"


def test_quota_requalification_does_not_probe_before_reset_or_authorization_block(tmp_path):
    future = _blocked_ledger(tmp_path / "future", blocked_until="2026-09-10T13:00:00+00:00")
    calls = []
    result = QuotaRequalificationCoordinator(future).probe_once("cloud", lambda *_: calls.append(True), now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert result.status is QuotaProbeStatus.NOT_DUE
    assert calls == []

    auth = _blocked_ledger(tmp_path / "auth", reason="authorization", blocked_until=None)
    result = QuotaRequalificationCoordinator(auth).probe_once("cloud", lambda *_: calls.append(True), now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert result.status is QuotaProbeStatus.BLOCKED_EXTERNAL
    assert calls == []


def test_quota_requalification_keeps_provider_blocked_after_one_failed_probe(tmp_path):
    ledger = _blocked_ledger(tmp_path)
    calls = []

    def probe(*_):
        calls.append(True)
        return {"unit": "requests", "metric": "rpm", "window": "minute", "request_limit": 10, "request_remaining": 0, "block_reason": "rate_limit", "blocked_until": "2026-09-10T12:05:00+00:00"}

    result = QuotaRequalificationCoordinator(ledger).probe_once("cloud", probe, now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert result.status is QuotaProbeStatus.STILL_BLOCKED
    assert calls == [True]
    assert ledger.get_quota_observation("cloud")["block_reason"] == "rate_limit"


def test_quota_requalification_persists_conservative_cooldown_for_typed_probe_failure(tmp_path):
    ledger = _blocked_ledger(tmp_path)

    def probe(*_):
        raise ProviderError(
            "temporary rate limit",
            category="rate_limit",
            quota_metric="rpm",
            quota_window="minute",
        )

    result = QuotaRequalificationCoordinator(ledger).probe_once(
        "cloud",
        probe,
        now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert result.status is QuotaProbeStatus.PROBE_FAILED
    assert result.error_category == "rate_limit"
    latest = ledger.get_quota_observation("cloud")
    assert latest["block_reason"] == "rate_limit"
    assert latest["blocked_until"] == "2026-09-10T12:01:00+00:00"
