"""Host composition for reconstructing assigned Provider resources.

This module owns only the read/compose boundary needed to resume one durable
Commander run.  It does not advance a plan, dispatch a Worker, retry a
request, or grant any authority.  Supervisor and MCP callers share this
boundary instead of importing each other's private helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.devfarm_plan_state import CommanderPlanStore
from scripts.devfarm_provider_runtime import build_assigned_providers, build_worker_provider


def providers_for_resume(
    root: str | Path,
    run_id: str,
    timeout_seconds: float,
    *,
    local_trial: bool = False,
) -> dict[str, Any]:
    """Construct only the already assigned providers needed for one pass."""

    if not isinstance(local_trial, bool):
        raise TypeError("local_trial must be a boolean")
    repository = Path(root).resolve()
    plan = CommanderPlanStore(repository).load(run_id)

    def build_assigned_provider(
        provider_id: str,
        model_id: str,
        provider_timeout_seconds: float,
        provider_binding_id: str | None,
    ) -> Any:
        return build_worker_provider(
            provider_id,
            model_id,
            provider_timeout_seconds,
            provider_binding_id,
            allow_local=local_trial,
        )

    return build_assigned_providers(
        plan,
        timeout_seconds,
        provider_builder=build_assigned_provider,
    )


__all__ = ["providers_for_resume"]
