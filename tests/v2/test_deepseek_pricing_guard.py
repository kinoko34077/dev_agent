from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.dev_agent.resources.deepseek_pricing import (
    DeepSeekPricingCalendar,
    DeepSeekPricingGuard,
)


def _calendar(*, observed_at: str = "2026-11-02T00:00:00+00:00", expires_at: str = "2026-11-09T00:00:00+00:00") -> DeepSeekPricingCalendar:
    return DeepSeekPricingCalendar(
        source_url="https://api-docs.deepseek.com/quick_start/pricing/",
        observed_at=datetime.fromisoformat(observed_at),
        expires_at=datetime.fromisoformat(expires_at),
    )


def test_off_peak_interval_is_allowed_only_with_current_verified_calendar():
    guard = DeepSeekPricingGuard(_calendar(), now=lambda: datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc))

    decision = guard.evaluate(started_at=datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc), duration_seconds=30)

    assert decision.allowed is True
    assert decision.reason_code == "off_peak"


@pytest.mark.parametrize(
    ("started_at", "duration_seconds"),
    [
        (datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc), 1),
        (datetime(2026, 11, 2, 3, 59, 59, tzinfo=timezone.utc), 2),
        (datetime(2026, 11, 2, 9, 59, 59, tzinfo=timezone.utc), 2),
    ],
)
def test_peak_overlap_is_blocked_for_the_entire_possible_request_window(started_at: datetime, duration_seconds: int):
    guard = DeepSeekPricingGuard(_calendar(), now=lambda: started_at)

    decision = guard.evaluate(started_at=started_at, duration_seconds=duration_seconds)

    assert decision.allowed is False
    assert decision.reason_code == "deepseek_peak_pricing_blocked"
    assert decision.next_safe_at is not None


def test_exact_peak_end_and_weekend_are_not_peak():
    guard = DeepSeekPricingGuard(_calendar(), now=lambda: datetime(2026, 11, 2, 4, tzinfo=timezone.utc))

    end_boundary = guard.evaluate(
        started_at=datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc),
        duration_seconds=1,
    )
    weekend = guard.evaluate(
        started_at=datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc),
        duration_seconds=1,
    )

    assert end_boundary.allowed is True
    assert weekend.allowed is True


def test_stale_calendar_and_missing_window_fail_closed():
    stale = DeepSeekPricingGuard(
        _calendar(
            observed_at="2026-10-01T00:00:00+00:00",
            expires_at="2026-11-01T00:00:00+00:00",
        ),
        now=lambda: datetime(2026, 11, 2, 0, tzinfo=timezone.utc),
    )

    stale_decision = stale.evaluate(
        started_at=datetime(2026, 11, 2, 0, tzinfo=timezone.utc),
        duration_seconds=1,
    )
    missing_window = DeepSeekPricingGuard(_calendar(), now=lambda: datetime(2026, 11, 2, 0, tzinfo=timezone.utc)).evaluate(
        started_at=datetime(2026, 11, 2, 0, tzinfo=timezone.utc),
        duration_seconds=None,
    )

    assert stale_decision.allowed is False
    assert stale_decision.reason_code == "deepseek_pricing_calendar_stale"
    assert missing_window.allowed is False
    assert missing_window.reason_code == "deepseek_request_window_unknown"

