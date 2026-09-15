"""Host-owned Worker manifest loading and plan-ownership validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_repository import read_json, repository_path


def load_worker_manifest(root: str | Path, task: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Load one validated Worker manifest and bind it to plan ownership."""

    repository = Path(root).resolve()
    relative = task.get("manifest_path")
    task_id = task.get("task_id")
    if not isinstance(relative, str):
        raise DevFarmError(f"worker task has no manifest path: {task_id}")
    if not isinstance(task_id, str) or not task_id.strip():
        raise DevFarmError("worker task has no task_id")
    path = repository_path(repository, relative, required_parent=".devfarm/tasks")
    manifest = validate_manifest(read_json(path))
    if manifest["task_id"] != task_id:
        raise DevFarmError(f"manifest task_id does not match plan task: {task_id}")
    ownership = task.get("ownership")
    if not isinstance(ownership, list):
        raise DevFarmError(f"worker task ownership is missing: {task_id}")
    if not set(manifest["allowed_files"]).issubset(set(ownership)):
        raise DevFarmError(f"manifest allowed_files exceed plan ownership: {task_id}")
    return path, manifest


__all__ = ["load_worker_manifest"]
