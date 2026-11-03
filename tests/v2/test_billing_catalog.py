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


@pytest.mark.current_billing_catalog
def test_gemini_additional_binding_profiles_are_allowance_backed_not_fixed_free():
    profile = profile_for("gemini", "gemini:worker:free-2", "gemini-3.5-flash-lite")

    assert profile is not None
    assert profile.billing_mode == "recurring_allowance"
    assert profile.allowance_period == "daily"


@pytest.mark.current_billing_catalog
@pytest.mark.parametrize(
    ("provider_id", "binding_id", "model_id"),
    (
        # Groq inventory refresh is currently unavailable; this checks that
        # future exact-account discoveries use the explicitly reviewed Free
        # binding without claiming that this synthetic model was discovered.
        ("groq", "groq:account", "future-discovered-model"),
        ("mistral", "mistral:account", "ministral-3b-latest"),
    ),
)
def test_user_confirmed_free_provider_bindings_use_hard_stop_profiles(provider_id, binding_id, model_id):
    profile = profile_for(provider_id, binding_id, model_id)

    assert profile is not None
    assert profile.cost_minor == 0
    assert profile.billing_mode == "recurring_allowance"
    assert profile.overage_policy == "hard_stop"
    assert profile.no_charge_guaranteed is True
    assert profile.is_current()
    assert profile_for(provider_id, f"{provider_id}:other", model_id) is None


@pytest.mark.current_billing_catalog
def test_openrouter_free_model_variant_is_no_charge_on_account_and_qualification_lanes_only():
    model_id = "google/gemma-4-26b-a4b-it:free"

    profile = profile_for("openrouter", "openrouter:account", model_id)
    qualification_profile = profile_for("openrouter", "openrouter:free", model_id)

    assert profile is not None
    assert qualification_profile is not None
    assert profile.model_id == model_id
    assert profile.cost_minor == 0
    assert profile.billing_mode == "recurring_allowance"
    assert profile.overage_policy == "hard_stop"
    assert profile.no_charge_guaranteed is True
    assert qualification_profile.provider_binding_id == "openrouter:free"
    assert qualification_profile.model_id == model_id
    assert qualification_profile.cost_minor == 0
    assert qualification_profile.overage_policy == "hard_stop"
    assert qualification_profile.no_charge_guaranteed is True
    assert profile_for("openrouter", "openrouter:account", "google/gemma-4-26b-a4b-it") is None
    assert profile_for("openrouter", "openrouter:qualification", model_id) is None
    from src.dev_agent.resources.billing_catalog import default_binding_id

    assert default_binding_id("openrouter", model_id) == "openrouter:account"


@pytest.mark.current_billing_catalog
@pytest.mark.parametrize(
    ("provider_id", "binding_id", "model_id"),
    (
        (
            "vercel",
            "vercel:account",
            "reflection/beam-free",
        ),
        (
            "vercel",
            "vercel:account",
            "nvidia/nemotron-3.5-lightning-free",
        ),
        (
            "vercel",
            "vercel:account",
            "stealth/glyph-cluster",
        ),
    ),
)
def test_vercel_explicit_zero_price_models_are_no_charge_candidates(provider_id, binding_id, model_id):
    profile = profile_for(provider_id, binding_id, model_id)

    assert profile is not None
    assert profile.model_id == model_id
    assert profile.cost_minor == 0
    assert profile.billing_mode == "free_fixed"
    assert profile.overage_policy == "hard_stop"
    assert profile.no_charge_guaranteed is True
    assert profile_for(provider_id, "vercel:unreviewed", model_id) is None


@pytest.mark.parametrize(
    "model_id",
    ("stealth/glyph-cluster",),
)
@pytest.mark.current_billing_catalog
def test_vercel_current_free_directory_models_are_no_charge_for_unpaid_account(model_id):
    profile = profile_for("vercel", "vercel:account", model_id)

    assert profile is not None
    assert profile.cost_minor == 0
    assert profile.no_charge_guaranteed is True
    assert profile.billing_mode == "free_fixed"
    assert profile.overage_policy == "hard_stop"


@pytest.mark.current_billing_catalog
def test_vercel_ling_promotion_starting_charge_date_is_not_admitted_as_free():
    observed_now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    profile = profile_for("vercel", "vercel:account", "inclusionai/ling-3.1-flash-1019")

    assert profile is not None
    assert profile.cost_minor is None
    assert profile.billing_mode == "unknown"
    assert profile.overage_policy == "unknown"
    assert profile.no_charge_guaranteed is False
    assert profile.is_current(now=observed_now)


@pytest.mark.current_billing_catalog
@pytest.mark.parametrize(
    "model_id",
    ("google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5", "unlisted/model"),
)
def test_vercel_paid_or_unlisted_models_do_not_inherit_free_tier(model_id):
    assert profile_for("vercel", "vercel:account", model_id) is None


@pytest.mark.current_billing_catalog
def test_gemini_free_projects_admit_only_explicitly_reviewed_models():
    profile = profile_for("gemini", "gemini:worker:free-4", "gemini-3.8-flash")

    assert profile is not None
    assert profile.model_id == "gemini-3.8-flash"
    assert profile.no_charge_guaranteed is True
    assert profile_for("gemini", "gemini:worker:free-4", "gemini-exp-unlisted") is None


def test_sambanova_purchase_required_route_is_not_marked_free():
    assert profile_for("sambanova", "sambanova:account", "Meta-Llama-3.3-70B-Instruct") is None


@pytest.mark.current_billing_catalog
def test_ollama_cloud_paid_models_are_not_admitted_from_undifferentiated_free_plan():
    assert profile_for("ollama_cloud", "ollama_cloud:account", "deepseek-v4.1-flash") is None


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


def test_loopback_ollama_fixed_zero_cost_profile_does_not_expire():
    profile = TrustedResourceProfile(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        model_id="qwen3.5:9b",
        cost_minor=0,
        price_currency="JPY",
        quota_required=False,
        verified_at="2026-09-09T00:00:00+00:00",
        expires_at="2026-10-09T00:00:00+00:00",
        billing_mode="free_fixed",
    )

    assert profile.is_current(now=datetime(2026, 11, 2, tzinfo=timezone.utc)) is True


def test_discovered_loopback_ollama_models_match_the_local_no_charge_binding():
    profile = profile_for("ollama", "ollama:local", "qwen3.5:9b")

    assert profile is not None
    assert profile.cost_minor == 0
    assert profile.billing_mode == "free_fixed"
    assert profile.no_charge_guaranteed is True
    assert profile_for("ollama", "ollama:remote", "qwen3.5:9b") is None



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
    # The reviewed free-tier catalog was refreshed. Security-marked tests use
    # the real clock and must observe the current exact model/binding fact.
    profile = profile_for("gemini", "gemini:core", "gemini-3.8-flash")
    assert profile is not None
    assert profile.is_current()
    assert profile.no_charge_guaranteed is True

def test_cloudflare_public_pricing_review_cannot_renew_account_billing_authority():
    # Independently reading a Free-plan public pricing page cannot prove the
    # configured Cloudflare account is Free or that paid overage is disabled.
    from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG

    wildcard = TRUSTED_RESOURCE_CATALOG[("cloudflare", "cloudflare:account", "*")]
    assert wildcard.expires_at == "2026-12-03T00:00:00+09:00"
    assert wildcard.is_current(now=datetime(2026, 10, 11, tzinfo=timezone.utc)) is False
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
        "@cf/deepseek-ai/deepseek-v4.1-flash-1017",
        "@cf/deepseek-ai/deepseek-v4-pro-0813",
    ),
)
def test_cloudflare_paid_only_models_never_inherit_reviewed_free_wildcard(paid_model, monkeypatch):
    from src.dev_agent.resources.billing_catalog import BillingResolver, TRUSTED_RESOURCE_CATALOG, TrustedResourceProfile

    # Exercise the paid-model denial even after the public-policy review window
    # expires; an expired wildcard must not make this security regression vacuous.
    monkeypatch.setattr(TrustedResourceProfile, "is_current", lambda self, **_: True)
    resolver = BillingResolver(TRUSTED_RESOURCE_CATALOG)
    assert resolver.profile_for("cloudflare", "cloudflare:account", paid_model) is None
