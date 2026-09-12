"""Single source of truth for provider classification.

Router, Operation, and Repair must derive every local/remote decision from
this module.  Inline ``provider_id == "fake"`` or ``!= "ollama"`` comparisons
are prohibited; add a new function here instead.

Authority: local providers run under operator control and are exempt from
cloud-facing qualification, privacy, and sensitivity constraints.
"""

from __future__ import annotations

from .provider_authority_constants import (
    LOCAL_PROVIDER_IDS as _LOCAL_PROVIDERS,
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
