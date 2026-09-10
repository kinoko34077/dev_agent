"""Trusted binding/model billing facts for operational entry points.

Provider names are not billing identities.  A normal runtime may treat a
resource as free only when its concrete provider binding and model are present
in this catalog.  Qualification fixtures may add an exact, temporary entry in
their own test process; production code has no wildcard provider entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class TrustedResourceProfile:
    provider_id: str
    provider_binding_id: str
    model_id: str
    cost_minor: int | None
    price_currency: str | None
    quota_required: bool
    intelligence_tier: str | None = None


# This is deliberately a concrete binding/model catalog, not a provider-level
# free list.  ``:qualification`` bindings are isolated temporary resources
# used by the opt-in live qualification script; they do not activate a model
# in the normal Operation Layer or DevFarm.
_TRUSTED_RESOURCE_CATALOG: dict[tuple[str, str, str], TrustedResourceProfile] = {
    ("fake", "fake:default", "deterministic"): TrustedResourceProfile(
        "fake", "fake:default", "deterministic", 0, "JPY", False, "L1"
    ),
    ("cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1"
    ),
    ("cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct"): TrustedResourceProfile(
        "cloudflare", "cloudflare:qualification", "@cf/meta/llama-3.1-8b-instruct", 0, "JPY", True, "L1"
    ),
    ("openrouter", "openrouter:free", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:free", "openrouter/free", 0, "JPY", True, "L1"
    ),
    ("openrouter", "openrouter:qualification", "openrouter/free"): TrustedResourceProfile(
        "openrouter", "openrouter:qualification", "openrouter/free", 0, "JPY", True, "L1"
    ),
    ("gemini", "gemini:compat", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:compat", "gemini-2.5-flash", 0, "JPY", True, None
    ),
    ("gemini", "gemini:worker", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:worker", "gemini-3.5-flash-lite", 0, "JPY", True, "L1"
    ),
    ("gemini", "gemini:core", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:core", "gemini-3.8-flash", 0, "JPY", True, "L2"
    ),
    ("gemini", "gemini:qualification", "gemini-2.5-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-2.5-flash", 0, "JPY", True, None
    ),
    ("gemini", "gemini:qualification", "gemini-3.5-flash-lite"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.5-flash-lite", 0, "JPY", True, "L1"
    ),
    ("gemini", "gemini:qualification", "gemini-3.8-flash"): TrustedResourceProfile(
        "gemini", "gemini:qualification", "gemini-3.8-flash", 0, "JPY", True, "L2"
    ),
    ("ollama", "ollama", "qwen3:8b"): TrustedResourceProfile(
        "ollama", "ollama", "qwen3:8b", 0, "JPY", False, None
    ),
}

# Runtime callers receive an immutable view.  Adding or changing a billing
# fact is a reviewed code/configuration change, not a mutation available to a
# running Agent.  Tests can replace the lookup function in their own process
# without weakening this production boundary.
TRUSTED_RESOURCE_CATALOG: Mapping[tuple[str, str, str], TrustedResourceProfile] = MappingProxyType(_TRUSTED_RESOURCE_CATALOG)


def profile_for(provider_id: str, provider_binding_id: str, model_id: str) -> TrustedResourceProfile | None:
    """Return facts only for an exact provider/binding/model identity."""

    return TRUSTED_RESOURCE_CATALOG.get((provider_id, provider_binding_id, model_id))


def default_binding_id(provider_id: str, model_id: str) -> str:
    """Return a catalog binding for an exact model, or a non-free fallback."""

    for provider, binding, model in TRUSTED_RESOURCE_CATALOG:
        if provider == provider_id and model == model_id and ":qualification" not in binding:
            return binding
    return "fake:default" if provider_id == "fake" else provider_id


__all__ = ["TRUSTED_RESOURCE_CATALOG", "TrustedResourceProfile", "default_binding_id", "profile_for"]
