"""Provider-neutral quota blocking and reset-boundary policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


_PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class QuotaBlockDecision:
    """Safe routing consequence of one provider outcome."""

    block_reason: str
    metric: str
    window: str
    blocked_until: str | None
    reset_source: str
    authority: str = "derived"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_reason": self.block_reason,
            "metric": self.metric,
            "window": self.window,
            "blocked_until": self.blocked_until,
            "reset_source": self.reset_source,
            "authority": self.authority,
            "confidence": self.confidence,
        }


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def next_reset_at(window: str, *, now: datetime | None = None) -> str | None:
    """Return a known reset boundary; unknown windows intentionally return None."""

    current = _as_utc(now)
    if window == "minute":
        return _iso(current + timedelta(minutes=1))
    if window == "day":
        next_day = (current + timedelta(days=1)).date()
        return _iso(datetime(next_day.year, next_day.month, next_day.day, tzinfo=timezone.utc))
    if window == "month":
        year, month = current.year, current.month + 1
        if month == 13:
            year, month = year + 1, 1
        return _iso(datetime(year, month, 1, tzinfo=timezone.utc))
    if window == "day_pacific":
        pacific = current.astimezone(_PACIFIC)
        next_day = pacific.date() + timedelta(days=1)
        boundary = datetime(next_day.year, next_day.month, next_day.day, tzinfo=_PACIFIC)
        return boundary.astimezone(timezone.utc).isoformat()
    if window == "day_utc":
        return next_reset_at("day", now=current)
    return None


_KNOWN_WINDOWS = {
    "rpm": "minute",
    "tpm": "minute",
    "short": "minute",
    "rpd": "day_pacific",
    "daily": "day_utc",
    "daily_allocation": "day_utc",
    "tpd": "day_utc",
    "monthly": "month",
    "monthly_allocation": "month",
}


def provider_reset_window(provider_id: str, metric: str) -> str | None:
    """Map only explicit metric labels to a documented reset family.

    The provider id is kept in the API for future provider-specific policy,
    but an unknown metric never receives an invented reset time.
    """

    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id must be a non-empty string")
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError("metric must be a non-empty string")
    return _KNOWN_WINDOWS.get(metric.strip().lower())


def classify_provider_error(
    provider_id: str,
    error: Exception,
    *,
    now: datetime | None = None,
    conservative_cooldown_seconds: int = 60,
) -> QuotaBlockDecision | None:
    """Classify an error without treating an unknown 429 as a known reset.

    Adapters may attach ``quota_metric``, ``quota_window``, ``quota_reset_at``
    and ``quota_reset_source`` to a typed ProviderError.  Without those facts,
    rate-limit/quota errors use a bounded conservative cooldown.  Authorization
    errors have no automatic reset and remain blocked until a newer successful
    observation is written.
    """

    category = str(getattr(error, "category", "")).strip().lower()
    if category in {"authentication", "authorization"}:
        return QuotaBlockDecision(
            block_reason="authorization",
            metric="authorization",
            window="unknown",
            blocked_until=None,
            reset_source="none",
        )
    if category not in {"rate_limit", "quota", "transport"}:
        return None
    if isinstance(conservative_cooldown_seconds, bool) or not isinstance(conservative_cooldown_seconds, int) or conservative_cooldown_seconds <= 0:
        raise ValueError("conservative_cooldown_seconds must be a positive integer")
    current = _as_utc(now)
    metric = getattr(error, "quota_metric", None)
    if not isinstance(metric, str) or not metric.strip():
        metric = "transport" if category == "transport" else "quota"
    metric = metric.strip().lower()
    policy_window = getattr(error, "quota_window", None)
    if not isinstance(policy_window, str) or policy_window.strip() not in {"unknown", "minute", "day", "day_utc", "day_pacific", "month"}:
        policy_window = provider_reset_window(provider_id, metric) or "unknown"
    else:
        policy_window = policy_window.strip()
    reset_at = getattr(error, "quota_reset_at", None)
    if isinstance(reset_at, str) and reset_at.strip():
        blocked_until = reset_at.strip()
        reset_source = str(getattr(error, "quota_reset_source", None) or "provider").strip()
    else:
        blocked_until = next_reset_at(policy_window, now=current) if policy_window != "unknown" else _iso(current + timedelta(seconds=conservative_cooldown_seconds))
        reset_source = f"provider-policy:{policy_window}" if policy_window != "unknown" else "conservative-cooldown"
    # The durable schema keeps the public window vocabulary small.  The
    # timezone-specific policy is retained in reset_source and the exact
    # blocked_until timestamp.
    window = "day" if policy_window in {"day_utc", "day_pacific"} else policy_window
    return QuotaBlockDecision(
        block_reason=category,
        metric=metric,
        window=window,
        blocked_until=blocked_until,
        reset_source=reset_source or "conservative-cooldown",
        confidence=1.0 if window != "unknown" else 0.5,
    )


__all__ = ["QuotaBlockDecision", "classify_provider_error", "next_reset_at", "provider_reset_window"]
