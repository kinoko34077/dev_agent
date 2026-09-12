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
    APPROVED_PROVIDER_CLASSES as _APPROVED_CLASSES,
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


def validate_provider_instance_authority(provider: Any) -> None:
    """Re-validate an already-constructed Provider instance's authority.

    ``ProviderDefinition.__post_init__`` validates endpoint/credential
    authority only when a Provider is built through ``ProviderFactory``.  Two
    gaps remain closed by this function instead:

    1. A caller can inject an already-built ``ModelProvider`` instance
       directly (``ProviderRegistry(providers=[...])``, DevFarm
       ``WorkerAssignment``) without ever constructing a ``ProviderDefinition``.
    2. A caller can mutate ``base_url`` / ``api_key_env`` on a Provider
       instance *after* it passed construction-time or registration-time
       validation.

    Call this immediately before an instance is trusted: at registration
    (``ProviderRegistry.__init__``) and again immediately before dispatch
    (``ProviderDispatcher.request``), so a post-registration mutation is
    still caught. Every check here derives from the same
    provider_authority_constants.py SSOT as construction-time validation —
    no independent allowlist.

    A third gap this also closes: nothing previously stopped a hand-written
    class from claiming a network-capable ``provider_id`` (e.g. "gemini",
    "cloudflare") without exposing ``base_url``/``api_key_env`` at all —
    those two checks alone cannot catch an adapter that has no such
    attributes to inspect but embeds its own request logic. For a
    network-capable provider_id (one with a real endpoint concept — see
    NETWORK_CAPABLE_PROVIDER_IDS), the concrete class must be one of the
    approved adapter classes for that identity. ``FakeProvider`` and its
    subclasses are exempt from this class check regardless of the
    provider_id they simulate: this is this codebase's established
    provider-identity test-double convention (used throughout tests/v2 for
    routing/dispatch tests), and FakeProvider is a pure-Python stub with no
    base_url/api_key_env and no I/O of any kind, so it cannot reach an
    external endpoint no matter what provider_id it claims.
    """
    provider_id = getattr(provider, "provider_id", None)
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider instance must expose a non-empty provider_id")
    provider_id = provider_id.strip()

    if provider_id in _NETWORK_CAPABLE:
        from ..providers.fake.provider import FakeProvider

        if not isinstance(provider, FakeProvider):
            expected_classes = _APPROVED_CLASSES.get(provider_id)
            concrete_name = type(provider).__name__
            if expected_classes is not None and concrete_name not in expected_classes:
                raise ValueError(
                    f"provider_id {provider_id!r} is bound to adapter class "
                    f"{concrete_name!r}, which is not an approved adapter class "
                    f"for this provider identity: {sorted(expected_classes)}"
                )

    validate_endpoint_authority(provider_id, getattr(provider, "base_url", None))
    validate_api_key_env_authority(provider_id, getattr(provider, "api_key_env", None))
