from datetime import datetime, timezone

import pytest

from src.dev_agent.resources.billing_catalog import TrustedResourceProfile


_NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("verified_at", "expires_at"),
    (
        ("not-a-timestamp", "2026-10-09T00:00:00+00:00"),
        ("2026-09-09T00:00:00+00:00", "not-a-timestamp"),
        ("2026-09-12T00:00:00+00:00", "2026-10-09T00:00:00+00:00"),
        ("2026-09-09T00:00:00+00:00", "2026-09-08T00:00:00+00:00"),
    ),
)
def test_trusted_billing_profile_requires_coherent_review_timestamps(verified_at, expires_at):
    profile = TrustedResourceProfile(
        provider_id="example",
        provider_binding_id="example:free",
        model_id="example-model",
        cost_minor=0,
        price_currency="JPY",
        quota_required=True,
        verified_at=verified_at,
        expires_at=expires_at,
    )

    assert profile.is_current(now=_NOW) is False


def test_trusted_billing_profile_is_current_inside_review_window():
    profile = TrustedResourceProfile(
        provider_id="example",
        provider_binding_id="example:free",
        model_id="example-model",
        cost_minor=0,
        price_currency="JPY",
        quota_required=True,
        verified_at="2026-09-09T00:00:00+00:00",
        expires_at="2026-10-09T00:00:00+00:00",
    )

    assert profile.is_current(now=_NOW) is True
