"""Shared Host-side repository and Git primitives for the development plane.

The Commander, integration, and other development-only compositions need the
same path containment and Git error semantics.  Keeping these primitives here
prevents each service from growing a slightly different repository boundary.
This module has no task scheduling, provider, approval, or integration policy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError


def read_json(path: str | Path) -> Any:
    """Read one local JSON artifact without leaking parser details."""

    target = Path(path)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevFarmError(f"could not read JSON file {target}") from exc


def repository_path(
    root: str | Path,
    relative: str,
    *,
    required_parent: str | None = None,
) -> Path:
    """Resolve a repository-relative path and enforce optional containment."""

    root_path = Path(root).resolve()
    if not isinstance(relative, str) or not relative.strip():
        raise DevFarmError("repository path must be a non-empty relative string")
    candidate = (root_path / relative).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise DevFarmError("plan path resolves outside repository") from exc
    if required_parent is not None:
        if not isinstance(required_parent, str) or not required_parent.strip():
            raise DevFarmError("required_parent must be a non-empty relative string")
        parent = (root_path / required_parent).resolve()
        try:
            candidate.relative_to(parent)
        except ValueError as exc:
            raise DevFarmError(f"plan path must stay under {required_parent}") from exc
    return candidate


def git(root: str | Path, *arguments: str) -> str:
    """Run one bounded Git command in a checked repository."""

    import subprocess

    root_path = Path(root).resolve()
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root_path.as_posix()}", *arguments],
        cwd=root_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DevFarmError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def resolved_revision(root: str | Path, revision: str) -> str:
    """Resolve a revision to a commit object or fail closed."""

    try:
        return git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    except DevFarmError as exc:
        raise DevFarmError(f"plan base_revision cannot be resolved: {revision}") from exc


def git_diff_digest(root: str | Path, revision: str) -> str:
    """Return the bounded SHA-256 digest of one commit's binary diff."""

    import subprocess

    root_path = Path(root).resolve()
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root_path.as_posix()}",
            "diff-tree",
            "--root",
            "--binary",
            "--no-commit-id",
            "-r",
            revision,
            "--",
        ],
        cwd=root_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise DevFarmError(error or "could not read integration revision diff")
    return hashlib.sha256(result.stdout).hexdigest()


__all__ = [
    "git",
    "git_diff_digest",
    "read_json",
    "repository_path",
    "resolved_revision",
]
