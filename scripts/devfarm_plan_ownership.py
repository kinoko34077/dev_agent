"""Read-only ownership projections for Commander plan coordination.

Commander remains the authority for ownership locks and cross-plan conflict
decisions.  This module only loads the bounded active-task projection used by
those decisions, including a fail-closed compatibility projection for plans
that became unreadable after a stricter manifest policy was introduced.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError
from scripts.devfarm_plan_validation import (
    ACTIVE_TASK_STATUSES,
    OWNERS,
    PLAN_SCHEMA_VERSION,
    PLAN_STATUSES,
    paths,
    plan_id,
    text,
    validate_plan,
)
from scripts.devfarm_repository import read_json


def _legacy_ownership_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project enough validated identity to keep historical paths reserved."""

    if raw.get("schema_version", PLAN_SCHEMA_VERSION) != PLAN_SCHEMA_VERSION:
        raise DevFarmError("legacy plan projection is invalid")
    run_id = plan_id(raw.get("run_id"))
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise DevFarmError("legacy plan projection has no tasks")
    projected_tasks: list[dict[str, Any]] = []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, Mapping):
            raise DevFarmError("legacy plan task projection is invalid")
        task_id = text(raw_task.get("task_id"), "legacy task_id", max_length=101)
        owner = text(raw_task.get("owner"), "legacy task owner", max_length=16).lower()
        if owner not in OWNERS:
            raise DevFarmError("legacy plan task owner is invalid")
        status = text(raw_task.get("status"), "legacy task status", max_length=32).upper()
        if status not in PLAN_STATUSES:
            raise DevFarmError("legacy plan task status is invalid")
        if status not in ACTIVE_TASK_STATUSES:
            continue
        projected_tasks.append(
            {
                "task_id": task_id,
                "owner": owner,
                "status": status,
                "ownership": paths(raw_task.get("ownership", []), "legacy ownership paths"),
            }
        )
    return {"run_id": run_id, "tasks": projected_tasks}


def load_ownership_projections(
    root: str | Path,
    plan_directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load active ownership projections without mutating plans or locks.

    A normally readable plan goes through the full plan validator.  If a
    historical plan is rejected by a tightened manifest/security policy, only
    its bounded raw ownership projection is retained; unreadable identity or
    ownership remains a hard error and never fails open.
    """

    root_path = Path(root).resolve()
    directory = (root_path / ".devfarm" / "plans") if plan_directory is None else Path(plan_directory).resolve()
    projections: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            continue
        try:
            projections.append(validate_plan(read_json(path), root=root_path))
        except DevFarmError as validation_error:
            try:
                raw = read_json(path)
                if not isinstance(raw, Mapping):
                    raise DevFarmError("legacy plan projection is invalid")
                projections.append(_legacy_ownership_projection(raw))
            except (TypeError, ValueError, DevFarmError):
                # An unreadable plan whose ownership cannot be safely
                # projected remains a hard error; never fail open.
                raise validation_error
    return projections


__all__ = ["load_ownership_projections"]
