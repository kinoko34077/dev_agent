"""Host-owned Provider construction for development Worker roles.

Provider construction is separate from the Worker prompt, patch, and
verification lifecycle.  This module still uses the existing activation,
qualification, ProviderFactory, and authority validation boundaries; it does
not add routing, retry, or scheduler behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker_admission import DevFarmActivationPolicy
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory


ProviderBuilder = Callable[[str, str, float, str | None], ModelProvider]


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


def build_assigned_providers(
    plan: Mapping[str, object],
    timeout_seconds: float,
    *,
    provider_builder: ProviderBuilder | None = None,
) -> dict[str, ModelProvider]:
    """Materialize providers for the resumable Worker assignments in a plan.

    This is a provider-composition helper only.  It does not dispatch work,
    change task state, or retry failed providers.  The optional builder keeps
    the Host/Supervisor seam injectable for deterministic tests while the
    default still uses the existing admission-bound construction path.
    """

    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise DevFarmError("plan tasks must be a list")
    builder = provider_builder or build_worker_provider
    providers: dict[str, ModelProvider] = {}
    for task in tasks:
        if not isinstance(task, Mapping):
            raise DevFarmError("plan tasks must be objects")
        if task.get("owner") != "worker" or task.get("status") not in {"PLANNED", "READY"}:
            continue
        task_id = task.get("task_id")
        assignment = task.get("assignment")
        if not isinstance(task_id, str) or not task_id.strip():
            raise DevFarmError("worker task_id must be a non-empty string")
        if not isinstance(assignment, Mapping):
            raise DevFarmError(f"worker assignment is missing: {task_id}")
        provider_id = assignment.get("provider_id")
        model_id = assignment.get("model_id")
        binding_id = assignment.get("provider_binding_id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise DevFarmError(f"worker assignment provider_id is missing: {task_id}")
        if not isinstance(model_id, str) or not model_id.strip():
            raise DevFarmError(f"worker assignment model_id is missing: {task_id}")
        if binding_id is not None and not isinstance(binding_id, str):
            raise DevFarmError(f"worker assignment provider_binding_id is invalid: {task_id}")
        providers[task_id] = builder(
            provider_id,
            model_id,
            timeout_seconds,
            binding_id,
        )
    return providers


__all__ = ["build_assigned_providers", "build_worker_provider"]
