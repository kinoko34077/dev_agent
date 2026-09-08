"""Read-only Git health and explicitly gated recovery operations.

The default APIs only inspect state or produce a plan.  Mutating operations
require an explicit keyword so a recovery diagnostic cannot accidentally reset
the checkout or create a branch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any


@dataclass(frozen=True)
class GitSnapshot:
    root: str
    branch: str | None
    commit: str
    dirty: bool


@dataclass(frozen=True)
class RollbackPlan:
    root: str
    current_commit: str
    target_commit: str
    commands: tuple[tuple[str, ...], ...]


def _git(root: Path, *arguments: str, timeout: float = 5.0) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git command failed: {' '.join(arguments)}")
    return result.stdout.strip()


def inspect_git(root: str | Path) -> GitSnapshot:
    path = Path(root).expanduser().resolve()
    if not path.is_dir() or not (path / ".git").exists():
        raise ValueError(f"not a Git checkout: {path}")
    top_level = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    if top_level != path:
        raise ValueError(f"Git root mismatch: {top_level}")
    commit = _git(path, "rev-parse", "--verify", "HEAD")
    try:
        branch = _git(path, "symbolic-ref", "--short", "-q", "HEAD") or None
    except RuntimeError:
        branch = None
    dirty = bool(_git(path, "status", "--porcelain=v1"))
    return GitSnapshot(root=str(path), branch=branch, commit=commit, dirty=dirty)


def _resolve_commit(root: Path, commit: str) -> str:
    if not isinstance(commit, str) or not commit.strip() or commit.startswith("-"):
        raise ValueError("a non-empty Git commit is required")
    return _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")


def record_last_known_good(root: str | Path, metadata_path: str | Path, *, commit: str | None = None, require_clean: bool = False, test_report: str | None = None) -> Path:
    """Record an exact commit for later recovery without changing Git refs."""
    path = Path(root).expanduser().resolve()
    snapshot = inspect_git(path)
    if require_clean and snapshot.dirty:
        raise RuntimeError("cannot record a last-known-good commit from a dirty checkout")
    resolved_commit = _resolve_commit(path, commit or snapshot.commit)
    metadata = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "root": str(path),
        "branch": snapshot.branch,
        "commit": resolved_commit,
        "test_report": test_report,
    }
    destination = Path(metadata_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_last_known_good(metadata_path: str | Path) -> dict[str, Any]:
    path = Path(metadata_path).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid last-known-good metadata: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("commit"), str) or not value["commit"].strip():
        raise ValueError("invalid last-known-good metadata schema")
    return value


def plan_rollback(root: str | Path, metadata_path: str | Path) -> RollbackPlan:
    path = Path(root).expanduser().resolve()
    snapshot = inspect_git(path)
    metadata = load_last_known_good(metadata_path)
    target = _resolve_commit(path, metadata["commit"])
    return RollbackPlan(
        root=str(path),
        current_commit=snapshot.commit,
        target_commit=target,
        commands=(("git", "switch", "--detach", target), ("git", "reset", "--hard", target)),
    )


def rollback_to_last_known_good(root: str | Path, metadata_path: str | Path, *, allow_destructive: bool = False, allow_dirty: bool = False) -> GitSnapshot:
    """Apply a planned rollback only after explicit destructive and dirty-tree opt-ins."""
    if not allow_destructive:
        raise PermissionError("rollback requires allow_destructive=True")
    path = Path(root).expanduser().resolve()
    snapshot = inspect_git(path)
    if snapshot.dirty and not allow_dirty:
        raise RuntimeError("rollback refuses a dirty worktree; pass allow_dirty=True to discard local changes")
    plan = plan_rollback(path, metadata_path)
    if snapshot.dirty:
        _git(path, "switch", "--detach", "--discard-changes", plan.target_commit)
    else:
        _git(path, "switch", "--detach", plan.target_commit)
    _git(path, "reset", "--hard", plan.target_commit)
    return inspect_git(path)


_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_PROTECTED_BRANCHES = frozenset({"main", "master", "v2/bootstrap", "legacy/v1-final"})


def create_repair_branch(root: str | Path, metadata_path: str | Path, branch_name: str, *, allow_write: bool = False) -> str:
    """Create a repair branch at LKG, never moving the current worktree."""
    if not allow_write:
        raise PermissionError("repair branch creation requires allow_write=True")
    if not _BRANCH_PATTERN.fullmatch(branch_name) or branch_name in _PROTECTED_BRANCHES or branch_name.endswith("/"):
        raise ValueError("invalid or protected repair branch name")
    path = Path(root).expanduser().resolve()
    target = _resolve_commit(path, load_last_known_good(metadata_path)["commit"])
    try:
        _git(path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    except RuntimeError:
        _git(path, "branch", branch_name, target)
        return branch_name
    raise ValueError(f"repair branch already exists: {branch_name}")


def snapshot_dict(snapshot: GitSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


__all__ = [
    "GitSnapshot",
    "RollbackPlan",
    "create_repair_branch",
    "inspect_git",
    "load_last_known_good",
    "plan_rollback",
    "record_last_known_good",
    "rollback_to_last_known_good",
    "snapshot_dict",
]
