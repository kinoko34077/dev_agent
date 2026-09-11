"""Trusted binding/model billing facts for operational entry points.

Provider names are not billing identities.  A normal runtime may treat a
resource as free only when its concrete provider binding and model are present
in this catalog.  Qualification fixtures may add an exact, temporary entry in
their own test process; production code has no wildcard provider entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType


_DEFAULT_VERIFIED_AT = "2026-09-09T00:00:00+00:00"
_DEFAULT_EXPIRES_AT = "2026-10-09T00:00:00+00:00"
_BILLING_MODES = frozenset({"free_fixed", "recurring_allowance", "recurring_credit", "paid", "unknown"})
_OVERAGE_POLICIES = frozenset({"hard_stop", "billable", "unknown"})


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
        return verified_utc <= current_utc < expiry_utc

    @property
    def no_charge_guaranteed(self) -> bool:
        """Whether this reviewed profile guarantees zero overage charge."""
        return self.cost_minor == 0 and (
            self.billing_mode == "free_fixed"
            or (self.billing_mode == "recurring_allowance" and self.overage_policy == "hard_stop")
        )


# This is deliberately a concrete binding/model catalog, not a provider-level
# free list.  ``:qualification`` bindings are isolated temporary resources
# used by the opt-in live qualification script; they do not activate a model
# in the normal Operation Layer or DevFarm.
_TRUSTED_RESOURCE_CATALOG: dict[tuple[str, str, str], TrustedResourceProfile] = {
    ("fake", "fake:default", "deterministic"): TrustedResourceProfile(
        "fake", "fake:default", "deterministic", 0, "JPY", False, "L1"
    ),
    ("cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("openrouter", "openrouter:free", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:free", "openrouter/free", 0, "JPY", True, "L1"
    ),
    ("openrouter", "openrouter:qualification", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:qualification", "openrouter/free", 0, "JPY", True, "L1"
    ),
    ("gemini", "gemini:compat", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:compat", "gemini-2.5-flash", 0, "JPY", True, None, billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:worker", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:worker", "gemini-3.5-flash-lite", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:core", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:core", "gemini-3.8-flash", 0, "JPY", True, "L2", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-2.5-flash", 0, "JPY", True, None, billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.5-flash-lite", 0, "JPY", True, "L1", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("gemini", "gemini:qualification", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.8-flash", 0, "JPY", True, "L2", billing_mode="recurring_allowance", allowance_period="daily", overage_policy="hard_stop"
    ),
    ("ollama", "ollama", "qwen3:8b"): TrustedResourceProfile(
        "ollama", "ollama", "qwen3:8b", 0, "JPY", False, None, billing_mode="free_fixed"
    ),
}

for _slot in ("2", "3", "4", "5"):
    _TRUSTED_RESOURCE_CATALOG[("gemini", f"gemini:worker:free-{_slot}", "gemini-3.5-flash-lite")] = TrustedResourceProfile(
        "gemini",
        f"gemini:worker:free-{_slot}",
        "gemini-3.5-flash-lite",
        0,
        "JPY",
        True,
        "L1",
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
        self._catalog = catalog or TRUSTED_RESOURCE_CATALOG

    def profile_for(self, provider_id: str, provider_binding_id: str, model_id: str) -> TrustedResourceProfile | None:
        """Return current facts only for an exact provider/binding/model identity."""

        profile = self._catalog.get((provider_id, provider_binding_id, model_id))
        return profile if profile is not None and profile.is_current() else None

    def default_binding_id(self, provider_id: str, model_id: str) -> str:
        """Return a catalog binding for an exact model, or a non-free fallback."""

        for provider, binding, model in self._catalog:
            if provider == provider_id and model == model_id and ":qualification" not in binding:
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
