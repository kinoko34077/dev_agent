"""Fail-closed timing guard for direct DeepSeek API billing windows.

The guard is deliberately independent from model discovery and from the
normal billing/quota authorities.  A current, verified pricing calendar is
required before a direct DeepSeek generation can be considered for dispatch;
missing or stale timing evidence blocks the route rather than guessing that a
request is free or off-peak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse


_OFFICIAL_SOURCE_HOST = "api-docs.deepseek.com"
_PEAK_WINDOWS_UTC: tuple[tuple[time, time], ...] = (
    (time(1, 0), time(4, 0)),
    (time(6, 0), time(10, 0)),
)


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DeepSeekPricingCalendar:
    """Authoritative pricing-calendar observation for one bounded interval."""

    source_url: str
    observed_at: datetime
    expires_at: datetime
    peak_windows_utc: tuple[tuple[time, time], ...] = _PEAK_WINDOWS_UTC

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must be non-empty")
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname != _OFFICIAL_SOURCE_HOST:
            raise ValueError("source_url must use the official DeepSeek documentation host")
        observed = _utc(self.observed_at, "observed_at")
        expires = _utc(self.expires_at, "expires_at")
        if expires <= observed:
            raise ValueError("expires_at must be after observed_at")
        if not isinstance(self.peak_windows_utc, tuple) or not self.peak_windows_utc:
            raise ValueError("peak_windows_utc must be a non-empty tuple")
        for window in self.peak_windows_utc:
            if not isinstance(window, tuple) or len(window) != 2:
                raise ValueError("peak windows must contain start/end pairs")
            start, end = window
            if not isinstance(start, time) or not isinstance(end, time) or start >= end:
                raise ValueError("peak windows must be ordered time pairs")
        object.__setattr__(self, "source_url", self.source_url.strip())
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)

    def is_current(self, at: datetime) -> bool:
        current = _utc(at, "at")
        return self.observed_at <= current < self.expires_at


@dataclass(frozen=True)
class DeepSeekPricingDecision:
    """Non-secret, auditable result of one timing evaluation."""

    allowed: bool
    reason_code: str
    next_safe_at: datetime | None
    source_url: str | None
    observed_at: datetime | None
    expires_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "next_safe_at": self.next_safe_at.isoformat() if self.next_safe_at is not None else None,
            "source_url": self.source_url,
            "observed_at": self.observed_at.isoformat() if self.observed_at is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }


class DeepSeekPricingGuard:
    """Evaluate the complete possible direct-API billing interval.

    A weekday peak window is treated as blocking even when a holiday might
    exempt it: no holiday calendar is silently inferred.  Callers must pass a
    finite request window; an unknown or over-large window fails closed.
    """

    def __init__(
        self,
        calendar: DeepSeekPricingCalendar | None,
        *,
        now: Callable[[], datetime] | None = None,
        max_request_seconds: float = 300.0,
    ) -> None:
        if isinstance(max_request_seconds, bool) or not isinstance(max_request_seconds, (int, float)) or max_request_seconds < 0:
            raise ValueError("max_request_seconds must be a finite non-negative number")
        self.calendar = calendar
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.max_request_seconds = float(max_request_seconds)

    def current_time(self) -> datetime:
        """Return the guard's trustworthy UTC clock for a dispatch start."""

        return _utc(self._now(), "clock")

    def evaluate(
        self,
        *,
        started_at: datetime,
        duration_seconds: float | int | None,
    ) -> DeepSeekPricingDecision:
        try:
            started = _utc(started_at, "started_at")
            current = self.current_time()
        except ValueError:
            return self._blocked("deepseek_clock_unknown")
        calendar = self.calendar
        if calendar is None:
            return self._blocked("deepseek_pricing_calendar_unavailable")
        if not calendar.is_current(current):
            return self._blocked(
                "deepseek_pricing_calendar_stale",
                calendar=calendar,
            )
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
            return self._blocked("deepseek_request_window_unknown", calendar=calendar)
        if duration_seconds < 0 or duration_seconds > self.max_request_seconds:
            return self._blocked("deepseek_request_window_unknown", calendar=calendar)
        end = started + timedelta(seconds=float(duration_seconds))
        next_safe: datetime | None = None
        cursor = started.date()
        last_day = end.date()
        while cursor <= last_day:
            if cursor.weekday() < 5:
                for window_start, window_end in calendar.peak_windows_utc:
                    peak_start = datetime.combine(cursor, window_start, tzinfo=timezone.utc)
                    peak_end = datetime.combine(cursor, window_end, tzinfo=timezone.utc)
                    if started < peak_end and end > peak_start:
                        next_safe = peak_end
                        break
            if next_safe is not None:
                break
            cursor += timedelta(days=1)
        if next_safe is not None:
            return self._blocked(
                "deepseek_peak_pricing_blocked",
                calendar=calendar,
                next_safe_at=next_safe,
            )
        return DeepSeekPricingDecision(
            True,
            "off_peak",
            None,
            calendar.source_url,
            calendar.observed_at,
            calendar.expires_at,
        )

    @staticmethod
    def _blocked(
        reason_code: str,
        *,
        calendar: DeepSeekPricingCalendar | None = None,
        next_safe_at: datetime | None = None,
    ) -> DeepSeekPricingDecision:
        return DeepSeekPricingDecision(
            False,
            reason_code,
            next_safe_at,
            calendar.source_url if calendar is not None else None,
            calendar.observed_at if calendar is not None else None,
            calendar.expires_at if calendar is not None else None,
        )


__all__ = [
    "DeepSeekPricingCalendar",
    "DeepSeekPricingDecision",
    "DeepSeekPricingGuard",
]
