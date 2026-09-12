from datetime import datetime, timezone

import pytest

from src.dev_agent.resources.billing_catalog import TrustedResourceProfile, profile_for


_NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("verified_at", "expires_at"),
    (
        # Non-ISO strings are now rejected at construction time.
        ("not-a-timestamp", "2026-10-09T00:00:00+00:00"),
        ("2026-09-09T00:00:00+00:00", "not-a-timestamp"),
    ),
)
def test_trusted_billing_profile_rejects_invalid_iso_timestamps(verified_at, expires_at):
    with pytest.raises(ValueError, match="ISO datetime"):
        TrustedResourceProfile(
            provider_id="example",
            provider_binding_id="example:free",
            model_id="example-model",
            cost_minor=0,
            price_currency="JPY",
            quota_required=True,
            verified_at=verified_at,
            expires_at=expires_at,
        )


@pytest.mark.parametrize(
    ("verified_at", "expires_at"),
    (
        # Valid ISO timestamps but outside the review window → is_current() is False.
        ("2026-09-12T00:00:00+00:00", "2026-10-09T00:00:00+00:00"),
        ("2026-09-09T00:00:00+00:00", "2026-09-08T00:00:00+00:00"),
    ),
)
def test_trusted_billing_profile_is_not_current_outside_review_window(verified_at, expires_at):
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


@pytest.mark.parametrize(
    "billing_mode",
    ("free_fixed", "recurring_allowance", "recurring_credit", "paid", "unknown"),
)
def test_trusted_billing_profile_exposes_explicit_billing_mode(billing_mode):
    profile = TrustedResourceProfile(
        provider_id="example",
        provider_binding_id="example:binding",
        model_id="example-model",
        cost_minor=None if billing_mode != "free_fixed" else 0,
        price_currency=None if billing_mode != "free_fixed" else "JPY",
        quota_required=billing_mode != "free_fixed",
        billing_mode=billing_mode,
        allowance_amount=500 if billing_mode == "recurring_credit" else None,
        allowance_currency="USD" if billing_mode == "recurring_credit" else None,
        allowance_period="30d" if billing_mode == "recurring_credit" else None,
    )

    assert profile.billing_mode == billing_mode


def test_trusted_billing_profile_rejects_unknown_billing_mode():
    with pytest.raises(ValueError, match="billing_mode"):
        TrustedResourceProfile(
            provider_id="example",
            provider_binding_id="example:binding",
            model_id="example-model",
            cost_minor=None,
            price_currency=None,
            quota_required=True,
            billing_mode="maybe_free",
        )


def test_trusted_billing_profile_validates_credit_allowance_metadata():
    with pytest.raises(ValueError, match="allowance_currency"):
        TrustedResourceProfile(
            provider_id="example",
            provider_binding_id="example:binding",
            model_id="example-model",
            cost_minor=None,
            price_currency=None,
            quota_required=True,
            billing_mode="recurring_credit",
            allowance_amount=500,
            allowance_currency="dollars",
            allowance_period="30d",
        )


def test_gemini_additional_binding_profiles_are_allowance_backed_not_fixed_free():
    profile = profile_for("gemini", "gemini:worker:free-2", "gemini-3.5-flash-lite")

    assert profile is not None
    assert profile.billing_mode == "recurring_allowance"
    assert profile.allowance_period == "daily"
