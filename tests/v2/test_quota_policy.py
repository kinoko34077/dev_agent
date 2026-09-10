from datetime import datetime, timezone

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.resources.quota_policy import classify_provider_error, next_reset_at, provider_reset_window


def test_quota_policy_keeps_authorization_blocked_without_a_reset():
    decision = classify_provider_error(
        "groq",
        ProviderError("forbidden", category="authorization", retryable=False, http_status=403),
        now=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )

    assert decision.block_reason == "authorization"
    assert decision.blocked_until is None
    assert decision.reset_source == "none"


def test_unknown_rate_limit_uses_bounded_conservative_cooldown():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    decision = classify_provider_error(
        "openrouter",
        ProviderError("limited", category="rate_limit", retryable=True, http_status=429),
        now=now,
        conservative_cooldown_seconds=45,
    )

    assert decision.window == "unknown"
    assert decision.reset_source == "conservative-cooldown"
    assert decision.blocked_until == "2026-09-10T12:00:45+00:00"


def test_explicit_daily_provider_policy_is_timezone_aware_and_stored_as_day():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    decision = classify_provider_error(
        "gemini",
        ProviderError("daily quota", category="quota", retryable=True, quota_metric="rpd"),
        now=now,
    )

    assert provider_reset_window("gemini", "rpd") == "day_pacific"
    assert decision.window == "day"
    assert decision.reset_source == "provider-policy:day_pacific"
    assert decision.blocked_until.endswith("07:00:00+00:00")


def test_provider_reset_policy_does_not_assign_gemini_or_mistral_rules_to_other_providers():
    assert provider_reset_window("cloudflare", "daily") == "day_utc"
    assert provider_reset_window("mistral", "short") == "minute"
    assert provider_reset_window("mistral", "monthly") == "month"
    assert provider_reset_window("openrouter", "daily") is None


def test_provider_specific_monthly_policy_is_used_when_mistral_does_not_send_reset():
    decision = classify_provider_error(
        "mistral",
        ProviderError("included usage", category="quota", retryable=True, quota_metric="monthly"),
        now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert decision.window == "month"
    assert decision.reset_source == "provider-policy:month"
    assert decision.blocked_until == "2026-10-01T00:00:00+00:00"


def test_unknown_non_quota_error_does_not_create_quota_block():
    assert classify_provider_error("gemini", ProviderError("bad payload", category="provider_decode")) is None


def test_reset_helpers_return_known_boundaries_only():
    now = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
    assert next_reset_at("day", now=now) == "2027-01-01T00:00:00+00:00"
    assert next_reset_at("month", now=now) == "2027-01-01T00:00:00+00:00"
    assert next_reset_at("not-known", now=now) is None
