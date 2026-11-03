"""Trusted binding/model billing facts for operational entry points.

Provider names are not billing identities.  A normal runtime may treat a
resource as free only when its concrete provider binding and model are present
in this catalog, including an explicitly reviewed binding-scoped policy.
Qualification fixtures may add an exact, temporary entry in their own test
process; production code has no provider-wide wildcard entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType


_DEFAULT_VERIFIED_AT = "2026-11-02T00:00:00+09:00"
_DEFAULT_EXPIRES_AT = "2026-12-02T00:00:00+09:00"
_LOCAL_FIXED_VERIFIED_AT = "2026-09-09T00:00:00+00:00"
_LOCAL_FIXED_EXPIRES_AT = "9999-12-31T23:59:59+00:00"
_BILLING_MODES = frozenset({"free_fixed", "recurring_allowance", "recurring_credit", "paid", "unknown"})
_OVERAGE_POLICIES = frozenset({"hard_stop", "billable", "unknown"})

# Current Cloudflare Workers AI pricing policy (reviewed 2026-10-03 against
# the provider's 2026-10-01 pricing page). Workers Free receives the shared
# 10,000 Neuron/day allowance, but these exact frontier models require a paid
# billing method and must never inherit the free binding-level wildcard.
_CLOUDFLARE_PAID_BILLING_REQUIRED_MODELS = frozenset({
    "@cf/moonshotai/kimi-k2.6",
    "@cf/moonshotai/kimi-k2.7-code",
    "@cf/zai-org/glm-5.2",
    "@cf/zai-org/glm-5.3",
    "@cf/zai-org/glm-5.3-flash",
    "@cf/deepseek-ai/deepseek-v4-flash-0731",
    "@cf/deepseek-ai/deepseek-v4-pro-0813",
})
_CLOUDFLARE_FREE_POLICY_VERIFIED_AT = "2026-11-02T00:00:00+09:00"
_CLOUDFLARE_FREE_POLICY_EXPIRES_AT = "2026-12-02T00:00:00+09:00"


@dataclass(frozen=True)
class TrustedResourceProfile:
    provider_id: str
    provider_binding_id: str
    model_id: str
    cost_minor: int | None
    price_currency: str | None
    quota_required: bool
    intelligence_tier: str | None = None
    source: str = "reviewed_code_catalog"
    verified_at: str = _DEFAULT_VERIFIED_AT
    expires_at: str = _DEFAULT_EXPIRES_AT
    billing_mode: str = "free_fixed"
    allowance_amount: int | None = None
    allowance_currency: str | None = None
    allowance_period: str | None = None
    overage_policy: str = "unknown"

    def __post_init__(self) -> None:
        # verified_at and expires_at must be non-empty ISO 8601 datetime strings.
        # bool is a subclass of int and must be rejected first.
        for field_name in ("verified_at", "expires_at"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty ISO datetime string")
            try:
                datetime.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError(f"{field_name} must be a valid ISO datetime string, got {value!r}")
        if self.billing_mode not in _BILLING_MODES:
            raise ValueError(f"billing_mode must be one of {sorted(_BILLING_MODES)}")
        if self.overage_policy not in _OVERAGE_POLICIES:
            raise ValueError(f"overage_policy must be one of {sorted(_OVERAGE_POLICIES)}")
        if self.allowance_amount is not None and (
            isinstance(self.allowance_amount, bool)
            or not isinstance(self.allowance_amount, int)
            or self.allowance_amount < 0
        ):
            raise ValueError("allowance_amount must be a non-negative integer or None")
        if self.allowance_currency is not None:
            if not isinstance(self.allowance_currency, str) or len(self.allowance_currency.strip()) != 3 or not self.allowance_currency.strip().isalpha():
                raise ValueError("allowance_currency must be a three-letter code or None")
            object.__setattr__(self, "allowance_currency", self.allowance_currency.strip().upper())
        if self.allowance_period is not None:
            if not isinstance(self.allowance_period, str) or not self.allowance_period.strip():
                raise ValueError("allowance_period must be a non-empty string or None")
            object.__setattr__(self, "allowance_period", self.allowance_period.strip())
        if self.billing_mode == "recurring_credit" and (self.allowance_amount is None or self.allowance_currency is None or self.allowance_period is None):
            raise ValueError("recurring_credit requires allowance amount, currency, and period")

    def is_current(self, *, now: datetime | None = None) -> bool:
        """Return whether this no-charge fact is still within its review window."""

        if not isinstance(self.source, str) or not self.source.strip():
            return False
        if not isinstance(self.verified_at, str) or not self.verified_at.strip():
            return False
        if not isinstance(self.expires_at, str) or not self.expires_at.strip():
            return False
        try:
            verified = datetime.fromisoformat(self.verified_at)
            expiry = datetime.fromisoformat(self.expires_at)
        except (TypeError, ValueError):
            return False
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        verified_utc = verified.astimezone(timezone.utc)
        expiry_utc = expiry.astimezone(timezone.utc)
        if self.non_expiring_no_charge:
            # Deterministic Fake and loopback Ollama have no external billing
            # clock. The endpoint authority check still requires Ollama to be
            # local; these code-owned facts do not expire like remote quotas.
            return verified_utc <= current_utc
        return verified_utc <= current_utc < expiry_utc

    @property
    def non_expiring_no_charge(self) -> bool:
        """Whether this exact code-owned local profile has no billing clock."""
        return bool(
            self.provider_id in {"fake", "ollama"}
            and self.source == "reviewed_code_catalog"
            and self.cost_minor == 0
            and self.quota_required is False
            and self.billing_mode == "free_fixed"
        )

    @property
    def no_charge_guaranteed(self) -> bool:
        """Whether this reviewed profile guarantees zero overage charge."""
        return self.cost_minor == 0 and (
            self.billing_mode == "free_fixed"
            or (self.billing_mode == "recurring_allowance" and self.overage_policy == "hard_stop")
            or (
                self.billing_mode == "recurring_credit"
                and self.allowance_amount is not None
                and self.allowance_amount > 0
                and self.overage_policy == "hard_stop"
            )
        )


# This is deliberately a concrete binding/model catalog, not a provider-level
# free list.  ``:qualification`` bindings are isolated temporary resources
# used by the opt-in live qualification script; they do not activate a model
# in the normal Operation Layer or DevFarm.
_TRUSTED_RESOURCE_CATALOG: dict[tuple[str, str, str], TrustedResourceProfile] = {
    ("fake", "fake:default", "deterministic"): TrustedResourceProfile(
        "fake", "fake:default", "deterministic", 0, "JPY", False, "L1",
        verified_at=_LOCAL_FIXED_VERIFIED_AT, expires_at=_LOCAL_FIXED_EXPIRES_AT,
    ),
    ("cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    # Dynamic Cloudflare execution uses the account credential lane.  This
    # wildcard is a short-lived, reviewed Workers-Free billing fact; the
    # resolver below denies the provider's explicit paid-only exceptions
    # before applying it. Discovery alone still grants no routing authority.
    ("cloudflare", "cloudflare:account", "*"): TrustedResourceProfile(
        "cloudflare",
        "cloudflare:account",
        "*",
        0,
        "JPY",
        True,
        None,
        source="cloudflare_workers_ai_pricing_2026-10-01",
        verified_at=_CLOUDFLARE_FREE_POLICY_VERIFIED_AT,
        expires_at=_CLOUDFLARE_FREE_POLICY_EXPIRES_AT,
        billing_mode="recurring_allowance",
        allowance_period="daily",
        overage_policy="hard_stop",
    ),
    # The operator confirmed these configured account lanes are currently on
    # non-paid plans. Vendor Free-mode policy is binding-scoped and reviewed
    # for this 30-day window; it does not apply to another binding or override
    # discovery, capability, quota, privacy, health, or runtime admission.
    ("mistral", "mistral:account", "*"): TrustedResourceProfile(
        "mistral",
        "mistral:account",
        "*",
        0,
        "JPY",
        True,
        None,
        source="mistral_studio_free_mode_operator_confirmed_2026-11-02",
        billing_mode="recurring_allowance",
        allowance_period="provider_rate_limits",
        overage_policy="hard_stop",
    ),
    ("groq", "groq:account", "*"): TrustedResourceProfile(
        "groq",
        "groq:account",
        "*",
        0,
        "JPY",
        True,
        None,
        source="groq_free_tier_operator_confirmed_2026-11-02",
        billing_mode="recurring_allowance",
        allowance_period="provider_rate_limits",
        overage_policy="hard_stop",
    ),
    ("openrouter", "openrouter:free", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:free", "openrouter/free", 0, "JPY", True, "L1",
        source="openrouter_free_models_router_pricing_2026-11-02",
    ),
    ("openrouter", "openrouter:account", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:account", "openrouter/free", 0, "JPY", True, "L1",
        source="openrouter_free_models_router_pricing_2026-11-02",
    ),
    ("openrouter", "openrouter:qualification", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:qualification", "openrouter/free", 0, "JPY", True, "L1",
        source="openrouter_free_models_router_pricing_2026-11-02",
    ),
    ("gemini", "gemini:compat", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:compat", "gemini-2.5-flash", 0, "JPY", True, None,
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:worker", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:worker", "gemini-3.5-flash-lite", 0, "JPY", True, "L1",
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:core", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:core", "gemini-3.8-flash", 0, "JPY", True, "L2",
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-2.5-flash", 0, "JPY", True, None,
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.5-flash-lite", 0, "JPY", True, "L1",
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.8-flash", 0, "JPY", True, "L2",
        source="google_gemini_api_pricing_2026-11-02", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("ollama", "ollama", "qwen3:8b"): TrustedResourceProfile(
        "ollama", "ollama", "qwen3:8b", 0, "JPY", False, None,
        verified_at=_LOCAL_FIXED_VERIFIED_AT, expires_at=_LOCAL_FIXED_EXPIRES_AT, billing_mode="free_fixed"
    ),
    # Discovery uses the generic loopback binding, while execution may derive
    # a model-specific local resource binding.  This binding-scoped fixed-free
    # profile lets the diagnostic/admission layer recognize installed local
    # models without changing which model is selected by routing policy.
    ("ollama", "ollama:local", "*"): TrustedResourceProfile(
        "ollama",
        "ollama:local",
        "*",
        0,
        "JPY",
        False,
        None,
        source="reviewed_code_catalog",
        verified_at=_LOCAL_FIXED_VERIFIED_AT,
        expires_at=_LOCAL_FIXED_EXPIRES_AT,
        billing_mode="free_fixed",
    ),
    ("ollama", "ollama:local:qwen3.5-9b", "qwen3.5:9b"): TrustedResourceProfile(
        "ollama", "ollama:local:qwen3.5-9b", "qwen3.5:9b", 0, "JPY", False, None,
        verified_at=_LOCAL_FIXED_VERIFIED_AT, expires_at=_LOCAL_FIXED_EXPIRES_AT, billing_mode="free_fixed"
    ),
    ("ollama", "ollama:local:qwen3.5-4b", "qwen3.5:4b"): TrustedResourceProfile(
        "ollama", "ollama:local:qwen3.5-4b", "qwen3.5:4b", 0, "JPY", False, None,
        verified_at=_LOCAL_FIXED_VERIFIED_AT, expires_at=_LOCAL_FIXED_EXPIRES_AT, billing_mode="free_fixed"
    ),
    ("ollama", "ollama:local:gemma4-12b", "gemma4:12b"): TrustedResourceProfile(
        "ollama", "ollama:local:gemma4-12b", "gemma4:12b", 0, "JPY", False, None,
        verified_at=_LOCAL_FIXED_VERIFIED_AT, expires_at=_LOCAL_FIXED_EXPIRES_AT, billing_mode="free_fixed"
    ),
}

# Vercel's free-tier model directory explicitly labels these exact model IDs
# as Free.  The $5 monthly AI Gateway credit is a different billing mechanism
# and must not turn the provider's paid catalog into a no-charge wildcard.
for _model in (
    "inclusionai/ling-3.1-flash-1019",
    "nvidia/nemotron-3.5-lightning-free",
    "reflection/beam-free",
    "stealth/glyph-cluster",
):
    _TRUSTED_RESOURCE_CATALOG[("vercel", "vercel:account", _model)] = TrustedResourceProfile(
        "vercel",
        "vercel:account",
        _model,
        0,
        "USD",
        True,
        "L1",
        source="vercel_ai_gateway_free_model_catalog_2026-11-02",
        billing_mode="free_fixed",
        overage_policy="hard_stop",
    )

for _slot in ("2", "3", "4", "5"):
    _TRUSTED_RESOURCE_CATALOG[("gemini", f"gemini:worker:free-{_slot}", "gemini-3.5-flash-lite")] = TrustedResourceProfile(
        "gemini",
        f"gemini:worker:free-{_slot}",
        "gemini-3.5-flash-lite",
        0,
        "JPY",
        True,
        "L1",
        source="google_gemini_api_pricing_2026-11-02",
        verified_at=_DEFAULT_VERIFIED_AT,
        expires_at=_DEFAULT_EXPIRES_AT,
        billing_mode="recurring_allowance",
        allowance_period="daily",
        overage_policy="hard_stop",
    )
    # The Human-provided, separately-accounted Gemini free projects may use
    # the discovered core model as an *unqualified* candidate.  Billing
    # admission is exact and explicit, but intelligence tier remains absent:
    # the separate benchmark catalog, not the billing table, decides L1/L2/L3.
    _TRUSTED_RESOURCE_CATALOG[("gemini", f"gemini:worker:free-{_slot}", "gemini-3.8-flash")] = TrustedResourceProfile(
        "gemini",
        f"gemini:worker:free-{_slot}",
        "gemini-3.8-flash",
        0,
        "JPY",
        True,
        None,
        source="google_gemini_api_pricing_2026-11-02",
        verified_at=_DEFAULT_VERIFIED_AT,
        expires_at=_DEFAULT_EXPIRES_AT,
        billing_mode="recurring_allowance",
        allowance_period="daily",
        overage_policy="hard_stop",
    )

# These are explicit operator-reviewed free-project/model pairs for the
# bounded L2 qualification probe.  Model discovery never creates these rows:
# a discovered model remains BILLING_UNKNOWN until its exact project lane and
# model are deliberately admitted here.  Keep this list narrow so a future
# paid-only model cannot inherit a provider-wide free assumption.
for _slot, _model in (("2", "gemini-3.7-flash"), ("3", "gemini-3.6-flash")):
    _TRUSTED_RESOURCE_CATALOG[("gemini", f"gemini:worker:free-{_slot}", _model)] = TrustedResourceProfile(
        "gemini",
        f"gemini:worker:free-{_slot}",
        _model,
        0,
        "JPY",
        True,
        None,
        source="operator_reviewed_free_project_model_probe",
        verified_at=_DEFAULT_VERIFIED_AT,
        expires_at=_DEFAULT_EXPIRES_AT,
        billing_mode="recurring_allowance",
        allowance_period="daily",
        overage_policy="hard_stop",
    )

# Runtime callers receive an immutable view.  Adding or changing a billing
# fact is a reviewed code/configuration change, not a mutation available to a
# running Agent.  Tests can replace the lookup function in their own process
# without weakening this production boundary.
TRUSTED_RESOURCE_CATALOG: Mapping[tuple[str, str, str], TrustedResourceProfile] = MappingProxyType(_TRUSTED_RESOURCE_CATALOG)


class BillingResolver:
    """Resolve billing facts for an exact provider/binding/model identity."""

    def __init__(self, catalog: Mapping[tuple[str, str, str], TrustedResourceProfile] | None = None) -> None:
        self._catalog = TRUSTED_RESOURCE_CATALOG if catalog is None else catalog

    def profile_for(self, provider_id: str, provider_binding_id: str, model_id: str) -> TrustedResourceProfile | None:
        """Return current facts only for an exact provider/binding/model identity."""

        if (
            provider_id == "cloudflare"
            and provider_binding_id == "cloudflare:account"
            and model_id in _CLOUDFLARE_PAID_BILLING_REQUIRED_MODELS
        ):
            return None
        profile = self._catalog.get((provider_id, provider_binding_id, model_id))
        if (
            profile is None
            and self._catalog is TRUSTED_RESOURCE_CATALOG
            and provider_id == "openrouter"
            and provider_binding_id in {"openrouter:account", "openrouter:free"}
            and model_id.endswith(":free")
        ):
            # OpenRouter's explicit :free variant denotes a zero-priced model
            # on its account/free credential lane. Keep the exact discovered
            # model ID and the caller's billing lane; stop at the free request cap.
            # The staged/current model inventory and all non-billing admission
            # checks remain independent.
            profile = TrustedResourceProfile(
                "openrouter",
                provider_binding_id,
                model_id,
                0,
                "JPY",
                True,
                None,
                source="openrouter_free_models_router_pricing_2026-11-02",
                billing_mode="recurring_allowance",
                allowance_period="free_account_request_limits",
                overage_policy="hard_stop",
            )
        if profile is None:
            # A wildcard is an explicit binding-level billing policy, never a
            # provider-wide or model-name inference. Qualification and model
            # evidence remain exact downstream gates. Provider-specific paid
            # exceptions are rejected above before the wildcard is considered.
            profile = self._catalog.get((provider_id, provider_binding_id, "*"))
        return profile if profile is not None and profile.is_current() else None

    def default_binding_id(self, provider_id: str, model_id: str) -> str:
        """Return a catalog binding for an exact model, or a non-free fallback."""

        if provider_id == "openrouter" and model_id.endswith(":free"):
            return "openrouter:account"
        for provider, binding, model in self._catalog:
            if provider == provider_id and model == model_id and model != "*" and ":qualification" not in binding:
                return binding
        return "fake:default" if provider_id == "fake" else provider_id


_default_resolver = BillingResolver()


def profile_for(provider_id: str, provider_binding_id: str, model_id: str) -> TrustedResourceProfile | None:
    """Return current facts only for an exact provider/binding/model identity."""
    return _default_resolver.profile_for(provider_id, provider_binding_id, model_id)


def default_binding_id(provider_id: str, model_id: str) -> str:
    """Return a catalog binding for an exact model, or a non-free fallback."""
    return _default_resolver.default_binding_id(provider_id, model_id)


__all__ = ["TRUSTED_RESOURCE_CATALOG", "TrustedResourceProfile", "BillingResolver", "default_binding_id", "profile_for"]
