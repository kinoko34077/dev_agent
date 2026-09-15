"""Host-owned Provider construction for development Worker roles.

Provider construction is separate from the Worker prompt, patch, and
verification lifecycle.  This module still uses the existing activation,
qualification, ProviderFactory, and authority validation boundaries; it does
not add routing, retry, or scheduler behavior.
"""

from __future__ import annotations

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker_admission import DevFarmActivationPolicy
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory


def build_worker_provider(
    name: str,
    model: str,
    timeout_seconds: float,
    provider_binding_id: str | None = None,
) -> ModelProvider:
    """Construct one already-admitted development Provider identity."""

    policy = DevFarmActivationPolicy()
    policy.ensure_active(name, model, provider_binding_id=provider_binding_id)
    binding_id, intelligence_tier = policy.binding_for(
        name,
        model,
        provider_binding_id=provider_binding_id,
    )
    try:
        return ProviderFactory().create(
            ProviderDefinition(
                provider_id=name,
                model=model,
                timeout_seconds=timeout_seconds,
                provider_binding_id=binding_id,
                intelligence_tier=intelligence_tier,
            )
        )
    except (TypeError, ValueError) as exc:
        raise DevFarmError(f"unsupported development worker provider: {name}") from exc


__all__ = ["build_worker_provider"]
