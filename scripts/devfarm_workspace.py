"""Host-owned DevFarm workspace and result persistence operations.

This boundary owns development-only farm directories, immutable attempt
projections, and isolated Git worktree preparation.  It does not choose a
provider, verify a patch, schedule a task, or integrate the official branch.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
from uuid import uuid4

from scripts.devfarm_contracts import validate_manifest, validate_result
from scripts.devfarm_errors import DevFarmError


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_BRANCH = re.compile(r"^agent/[A-Za-z0-9._/-]+$")


def init_farm(root: str | Path) -> Path:
    """Create the bounded development-farm directory layout."""

    repository = Path(root).resolve()
    farm = repository / ".devfarm"
    for name in ("tasks", "results", "shared", "status", "worktrees", "plans"):
        (farm / name).mkdir(parents=True, exist_ok=True)
    return farm


def write_manifest(root: str | Path, value: Mapping[str, Any]) -> Path:
    """Validate and persist one immutable task manifest."""

    manifest = validate_manifest(value)
    path = init_farm(root) / "tasks" / f"{manifest['task_id']}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_result(root: str | Path, value: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> Path:
    """Validate and persist one attempt plus the latest result projection."""

    normalized_manifest = validate_manifest(manifest)
    result = validate_result(value, manifest=normalized_manifest)
    directory = init_farm(root) / "results" / normalized_manifest["task_id"]
    directory.mkdir(parents=True, exist_ok=True)
    attempt_id = result.get("attempt_id") or uuid4().hex
    result["attempt_id"] = attempt_id
    attempt_directory = directory / "attempts" / attempt_id
    attempt_directory.mkdir(parents=True, exist_ok=False)
    attempt_path = attempt_directory / "result.json"
    attempt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="strict")
    path = directory / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    history_path = directory / "attempts.jsonl"
    with history_path.open("a", encoding="utf-8", newline="\n") as history:
        history.write(
            json.dumps(
                {
                    "attempt_id": attempt_id,
                    "status": result["status"],
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return path


def prepare_worktree(
    root: str | Path,
    *,
    task_id: str,
    branch: str,
    revision: str | None = None,
) -> Path:
    """Prepare one isolated Worker worktree from a bounded Git identity."""

    repository = Path(root).resolve()
    if not _TASK_ID.fullmatch(task_id):
        raise DevFarmError("task_id contains unsafe characters")
    if not _BRANCH.fullmatch(branch) or branch.endswith("/"):
        raise DevFarmError("worker branch must use the agent/<provider>/<task> form")
    if revision is not None and (
        not isinstance(revision, str)
        or not revision.strip()
        or revision.lstrip().startswith("-")
        or any(char in revision for char in "\r\n")
    ):
        raise DevFarmError("revision must be a safe Git revision")
    worktree = init_farm(repository) / "worktrees" / task_id
    if worktree.exists():
        raise FileExistsError(worktree)
    command = [
        "git",
        "-c",
        f"safe.directory={repository.as_posix()}",
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
    ]
    if revision:
        command.append(revision.strip())
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git worktree add failed")
    return worktree


__all__ = ["init_farm", "prepare_worktree", "write_manifest", "write_result"]
