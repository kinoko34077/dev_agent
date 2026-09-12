"""Single source of truth for provider classification.

Router, Operation, and Repair must derive every local/remote decision from
this module.  Inline ``provider_id == "fake"`` or ``!= "ollama"`` comparisons
are prohibited; add a new function here instead.

Authority: local providers run under operator control and are exempt from
cloud-facing qualification, privacy, and sensitivity constraints.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .provider_authority_constants import (
    APPROVED_API_KEY_ENVS as _APPROVED_KEY_ENVS,
    APPROVED_CLOUD_ORIGINS as _APPROVED_ORIGINS,
    LOCAL_PROVIDER_IDS as _LOCAL_PROVIDERS,
    LOOPBACK_HOSTNAMES as _LOOPBACK,
    NETWORK_CAPABLE_PROVIDER_IDS as _NETWORK_CAPABLE,
    REAL_LOCAL_PROVIDER_IDS as _REAL_LOCAL_PROVIDERS,
)


def is_local_provider(provider_id: str) -> bool:
    """True for providers that run under operator control (no cloud endpoint)."""
    return provider_id in _LOCAL_PROVIDERS


def requires_qualification(provider_id: str) -> bool:
    """True when a QualificationResolver record is required before routing."""
    return not is_local_provider(provider_id)


def max_sensitivity(provider_id: str) -> str:
    """Maximum sensitivity level when registering a resource for this provider."""
    return "sensitive" if provider_id in _REAL_LOCAL_PROVIDERS else "normal"


def privacy_profile(provider_id: str) -> str:
    """Privacy profile for a newly registered resource."""
    return "local_only" if provider_id in _REAL_LOCAL_PROVIDERS else "remote_cloud"


def validate_endpoint_authority(provider_id: str, base_url: str | None) -> None:
    """Raise ValueError if the endpoint/credential pairing violates authority rules.

    Rules (fail-closed):
    - ollama (real local): base_url must resolve to a loopback address only.
      Non-loopback addresses are rejected; LAN/private IP ranges are rejected.
      Cross-origin redirects cannot escape because the URL itself is validated.
    - fake: no endpoint restriction (test stub, no real network).
    - Cloud providers: if base_url is supplied it must match the approved origin
      prefix from APPROVED_CLOUD_ORIGINS exactly (same scheme + host).
    """
    if base_url is None:
        return

    if provider_id == "fake":
        return

    if provider_id in _REAL_LOCAL_PROVIDERS:
        # Ollama must only connect to loopback.
        try:
            parsed = urlparse(base_url)
        except Exception:
            raise ValueError(
                f"ollama base_url is not a valid URL: {base_url!r}"
            )
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"ollama base_url scheme must be http or https, got {parsed.scheme!r}"
            )
        hostname = (parsed.hostname or "").lower()
        if hostname not in _LOOPBACK:
            raise ValueError(
                f"ollama base_url must resolve to a loopback address "
                f"(127.0.0.1, ::1, or localhost); got hostname {hostname!r}. "
                "Non-loopback endpoints are rejected to prevent local-provider "
                "classification from routing traffic to external hosts."
            )
        return

    # Cloud provider: validate against approved origin.
    approved_origin = _APPROVED_ORIGINS.get(provider_id)
    if approved_origin is None:
        # Unknown provider with a base_url override — reject.
        raise ValueError(
            f"provider {provider_id!r} is not in the approved endpoint registry; "
            "base_url overrides are not permitted for unknown cloud providers."
        )
    try:
        parsed = urlparse(base_url.rstrip("/"))
        supplied_origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        raise ValueError(f"base_url is not a valid URL: {base_url!r}")
    if not supplied_origin or supplied_origin != approved_origin:
        raise ValueError(
            f"base_url for provider {provider_id!r} must match the approved "
            f"origin {approved_origin!r}; got {supplied_origin!r}. "
            "Endpoint overrides that deviate from the approved origin are rejected."
        )


def validate_api_key_env_authority(provider_id: str, api_key_env: str | None) -> None:
    """Raise ValueError if api_key_env deviates from the approved set for this provider.

    Prevents an attacker from redirecting credential resolution to an
    environment variable they control while keeping the approved provider_id.
    """
    if api_key_env is None:
        return
    approved = _APPROVED_KEY_ENVS.get(provider_id)
    if approved is None:
        # Local or unknown provider — no api_key_env restriction.
        return
    if api_key_env not in approved:
        raise ValueError(
            f"api_key_env {api_key_env!r} is not in the approved set for "
            f"provider {provider_id!r}: {sorted(approved)}. "
            "Override to a non-canonical credential variable is rejected."
        )


def validate_provider_class_identity(provider_id: str, provider: Any) -> None:
    """Raise ValueError unless provider is exactly the canonical adapter type.

    For a network-capable provider_id (one with a real endpoint concept —
    see NETWORK_CAPABLE_PROVIDER_IDS), ``type(provider)`` must be identical
    (``is``, not name equality and not ``isinstance``) to the one canonical
    adapter class ProviderFactory constructs for that identity (see
    providers/canonical_types.py — the same SSOT ProviderFactory itself
    uses, so the two cannot diverge).

    This is deliberately unconditional and fail-closed, with no exemption
    for any base class: a name-string comparison can be defeated by a
    same-named class in a different module; an isinstance-based exemption
    (e.g. "FakeProvider and its subclasses are always allowed") can be
    defeated by subclassing the exempted class and overriding its request()
    method to do real I/O. Only exact type identity against a fixed,
    Factory-verified class closes both. Production code must never call
    this with a test double claiming a real provider_id — tests that need
    to simulate a network-capable identity without constructing the real
    adapter must opt out of this check via test-side composition (see
    tests/v2/conftest.py's ``@pytest.mark.security`` convention), not by
    weakening this function.

    A network-capable provider_id with no canonical class registered at all
    (nothing in providers/canonical_types.py) is rejected, never silently
    permitted — an unmapped identity is not evidence that no restriction is
    needed.
    """
    if provider_id not in _NETWORK_CAPABLE:
        return
    from ..providers.canonical_types import canonical_class

    expected_type = canonical_class(provider_id)
    if expected_type is None:
        raise ValueError(
            f"provider_id {provider_id!r} is network-capable but has no "
            "canonical adapter type registered in providers/canonical_types.py; "
            "an unmapped network-capable identity is rejected, not permitted."
        )
    if type(provider) is not expected_type:
        actual = f"{type(provider).__module__}.{type(provider).__qualname__}"
        expected = f"{expected_type.__module__}.{expected_type.__qualname__}"
        raise ValueError(
            f"provider_id {provider_id!r} instance is of type {actual!r}, "
            f"which is not the canonical adapter {expected!r} for this "
            "provider identity."
        )


def validate_provider_instance_authority(provider: Any) -> None:
    """Re-validate an already-constructed Provider instance's authority.

    ``ProviderDefinition.__post_init__`` validates endpoint/credential
    authority only when a Provider is built through ``ProviderFactory``.
    This function closes the same gaps for an instance that bypassed that
    path entirely:

    1. A caller can inject an already-built ``ModelProvider`` instance
       directly (``ProviderRegistry(providers=[...])``, DevFarm
       ``WorkerAssignment``) without ever constructing a ``ProviderDefinition``.
    2. A caller can mutate ``base_url`` / ``api_key_env`` on a Provider
       instance *after* it passed construction-time or registration-time
       validation.
    3. A hand-written class can claim a network-capable ``provider_id``
       (e.g. "gemini") without exposing ``base_url``/``api_key_env`` at all
       — see validate_provider_class_identity().

    Call this immediately before an instance is trusted: at registration
    (``ProviderRegistry.__init__``) and again immediately before dispatch
    (``ProviderDispatcher.request``), so a post-registration mutation is
    still caught. Every check here derives from the same
    provider_authority_constants.py / canonical_types.py SSOT as
    construction-time validation — no independent allowlist.
    """
    provider_id = getattr(provider, "provider_id", None)
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider instance must expose a non-empty provider_id")
    provider_id = provider_id.strip()

    validate_provider_class_identity(provider_id, provider)
    validate_endpoint_authority(provider_id, getattr(provider, "base_url", None))
    validate_api_key_env_authority(provider_id, getattr(provider, "api_key_env", None))
    validate_no_inline_credential(provider_id, provider)
    validate_transport_identity(provider_id, provider)


def validate_no_inline_credential(provider_id: str, provider: Any) -> None:
    """Raise ValueError if the instance embeds a credential value directly.

    ``ProviderDefinition``'s own docstring states the design intent
    explicitly: "API keys are deliberately resolved by each adapter from
    its external environment; they cannot be embedded in this definition."
    Every HTTP adapter's constructor nonetheless accepts an optional
    ``api_key``/``api_token`` positional/keyword argument (used for
    non-production embedding scenarios such as direct unit tests of the
    adapter itself) that, if set, is used INSTEAD of resolving from
    ``os.environ`` at call time. exact-type and endpoint/api_key_env
    checks alone do not catch this: an instance can be the exact canonical
    class, pointed at the exact approved origin, with a canonical
    api_key_env value recorded -- and still send a hardcoded secret with
    every request because ``api_key`` (or ``api_token``) was set at
    construction. Reject any network-capable provider instance that has
    either attribute set to a truthy value.
    """
    if provider_id not in _NETWORK_CAPABLE:
        return
    for attribute in ("api_key", "api_token"):
        value = getattr(provider, attribute, None)
        if value:
            raise ValueError(
                f"provider_id {provider_id!r} instance has an inline {attribute!r} "
                "value set; credentials must be resolved from an approved "
                "environment variable at call time, never embedded on the "
                "instance."
            )


def validate_transport_identity(provider_id: str, provider: Any) -> None:
    """Raise ValueError if the instance's underlying HTTP opener was swapped.

    ``base_url`` and ``api_key_env`` are plain string attributes checked by
    ``validate_endpoint_authority``/``validate_api_key_env_authority``, and
    ``validate_provider_class_identity`` confirms the instance is the exact
    canonical class -- but for the OpenAI-compatible family of adapters
    (groq, mistral, openrouter, sambanova, ollama_cloud, vercel), the
    object that actually performs the HTTP call is a separate,
    independently mutable attribute: ``provider._http._opener``. All three
    prior checks can pass while a caller has replaced that opener with an
    arbitrary callable, silently redirecting every request regardless of
    what ``base_url`` says. This checks that, when a ``_http`` attribute
    with an ``_opener`` is present, the opener is exactly the shared
    canonical ``urlopen_no_redirect`` function -- not merely non-None, not
    merely callable, but the exact expected object.
    """
    if provider_id not in _NETWORK_CAPABLE:
        return
    http_transport = getattr(provider, "_http", None)
    if http_transport is None:
        return
    opener = getattr(http_transport, "_opener", None)
    if opener is None:
        return
    from ..providers.openai_compatible.http import urlopen_no_redirect

    if opener is not urlopen_no_redirect:
        raise ValueError(
            f"provider_id {provider_id!r} instance's HTTP transport opener "
            "has been replaced with a non-canonical callable; the opener "
            "must be exactly urlopen_no_redirect."
        )
