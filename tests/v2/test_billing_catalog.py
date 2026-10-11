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


@pytest.mark.parametrize(
    ("binding_id", "model_id"),
    (
        ("ollama:local:qwen3.5-4b", "qwen3.5:4b"),
        ("ollama:local:qwen3.5-9b", "qwen3.5:9b"),
        ("ollama:local:gemma4-12b", "gemma4:12b"),
    ),
)
def test_local_ollama_candidates_have_exact_zero_cost_catalog_profiles(binding_id, model_id):
    profile = profile_for("ollama", binding_id, model_id)

    assert profile is not None
    assert profile.cost_minor == 0
    assert profile.price_currency == "JPY"
    assert profile.quota_required is False
    assert profile.no_charge_guaranteed is True



def test_historical_fixture_clock_preserves_explicit_expiry_denial():
    # The fixture can model an earlier accepted test setup without changing
    # how the production profile evaluates an explicit future observation.
    profile = TrustedResourceProfile(
        provider_id="fixture",
        provider_binding_id="fixture:binding",
        model_id="fixture-model",
        cost_minor=0,
        price_currency="JPY",
        quota_required=False,
        verified_at="2026-10-01T00:00:00+00:00",
        expires_at="2026-10-09T00:00:00+00:00",
    )
    assert profile.is_current() is True
    assert profile.is_current(now=datetime(2026, 10, 11, tzinfo=timezone.utc)) is False


@pytest.mark.security
def test_security_billing_clock_remains_real():
    # The broad test-only fixture cannot alter security test expiry decisions.
    profile = TrustedResourceProfile(
        provider_id="fixture",
        provider_binding_id="fixture:binding",
        model_id="fixture-model",
        cost_minor=0,
        price_currency="JPY",
        quota_required=False,
        verified_at="2026-10-01T00:00:00+00:00",
        expires_at="2026-10-09T00:00:00+00:00",
    )
    assert profile.is_current() is False
    assert profile.is_current(now=datetime.now(timezone.utc)) is False
    # The current published Gemini free billing claim expired on Oct 9.
    # Security-marked tests never receive the historical synthetic clock.
    assert profile_for("gemini", "gemini:core", "gemini-3.8-flash") is None

def test_cloudflare_public_free_policy_review_is_bounded_and_expires():
    # Public plan pricing is a time-limited billing fact; it is not account
    # quota, runtime health, capability or qualification evidence.
    from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG

    wildcard = TRUSTED_RESOURCE_CATALOG[("cloudflare", "cloudflare:account", "*")]
    assert wildcard.source == "cloudflare_workers_ai_pricing_2026-10-09_reviewed_2026-10-11"
    assert wildcard.is_current(now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert not wildcard.is_current(now=datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert wildcard.no_charge_guaranteed is True
    assert wildcard.billing_mode == "recurring_allowance"
    assert wildcard.overage_policy == "hard_stop"


@pytest.mark.parametrize(
    "paid_model",
    (
        "@cf/moonshotai/kimi-k2.6",
        "@cf/moonshotai/kimi-k2.7-code",
        "@cf/zai-org/glm-5.2",
        "@cf/zai-org/glm-5.3",
        "@cf/zai-org/glm-5.3-flash",
        "@cf/deepseek-ai/deepseek-v4-flash-0731",
        "@cf/deepseek-ai/deepseek-v4-pro-0813",
    ),
)
def test_cloudflare_paid_only_models_never_inherit_reviewed_free_wildcard(paid_model):
    from src.dev_agent.resources.billing_catalog import BillingResolver, TRUSTED_RESOURCE_CATALOG

    resolver = BillingResolver(TRUSTED_RESOURCE_CATALOG)
    assert resolver.profile_for("cloudflare", "cloudflare:account", paid_model) is None
