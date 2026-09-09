"""Small development-only Worker Farm boundary.

This is not a runtime scheduler or a Phase 7 AgentBackend.  It validates
narrow task/result contracts and prepares one Git worktree per worker so a
free model never needs write access to ``v2/bootstrap`` or another worker's
checkout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Mapping


class DevFarmError(ValueError):
    """A development-farm manifest or result is unsafe or malformed."""


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_BRANCH = re.compile(r"^agent/[A-Za-z0-9._/-]+$")
_STATUSES = {"pending", "running", "completed", "failed", "blocked_external"}
_PROTECTED_FILES = frozenset(
    {
        "spec/v2/GATE_STATUS.json",
        "src/dev_agent/resources/budget.py",
    }
)
_MANIFEST_FIELDS = {
    "task_id",
    "objective",
    "base_revision",
    "allowed_files",
    "read_files",
    "forbidden_files",
    "requirements",
    "acceptance",
    "test_commands",
    "max_attempts",
    "output_contract",
}
_RESULT_FIELDS = {"status", "base_revision", "changed_files", "tests_run", "tests_passed", "known_issues", "assumptions"}


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError(f"{name} must be a non-empty string")
    return value.strip()


def _path(value: Any, name: str) -> str:
    candidate = _nonempty(value, name).replace("\\", "/")
    parsed = PurePosixPath(candidate)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise DevFarmError(f"{name} must be a safe relative path")
    return str(parsed)


def _paths(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise DevFarmError(f"{name} must be a list")
    result: list[str] = []
    for item in value:
        normalized = _path(item, name)
        if normalized not in result:
            result.append(normalized)
    return result


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise DevFarmError(f"{name} must be a list")
    result = []
    for item in value:
        result.append(_nonempty(item, name))
    return result


def _is_protected(path: str) -> bool:
    return path in _PROTECTED_FILES or path == "recovery" or path.startswith("recovery/") or path == ".devfarm" or path.startswith(".devfarm/")


def validate_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DevFarmError("manifest must be an object")
    missing = sorted(_MANIFEST_FIELDS - set(value))
    if missing:
        raise DevFarmError(f"manifest missing fields: {', '.join(missing)}")
    task_id = _nonempty(value["task_id"], "task_id")
    if not _TASK_ID.fullmatch(task_id):
        raise DevFarmError("task_id contains unsafe characters")
    objective = _nonempty(value["objective"], "objective")
    base_revision = _nonempty(value["base_revision"], "base_revision")
    allowed = _paths(value["allowed_files"], "allowed_files")
    read = _paths(value["read_files"], "read_files")
    forbidden = _paths(value["forbidden_files"], "forbidden_files")
    protected = sorted(path for path in allowed if _is_protected(path))
    if protected:
        raise DevFarmError(f"protected files cannot be worker-owned: {', '.join(protected)}")
    for path in sorted(_PROTECTED_FILES):
        if path not in forbidden:
            forbidden.append(path)
    overlap = sorted(set(allowed) & set(forbidden))
    if overlap:
        raise DevFarmError(f"files cannot be both allowed and forbidden: {', '.join(overlap)}")
    if isinstance(value["max_attempts"], bool) or not isinstance(value["max_attempts"], int) or value["max_attempts"] <= 0:
        raise DevFarmError("max_attempts must be a positive integer")
    if not isinstance(value["output_contract"], Mapping):
        raise DevFarmError("output_contract must be an object")
    return {
        "schema_version": 1,
        "task_id": task_id,
        "objective": objective,
        "base_revision": base_revision,
        "allowed_files": allowed,
        "read_files": read,
        "forbidden_files": forbidden,
        "requirements": _strings(value["requirements"], "requirements"),
        "acceptance": _strings(value["acceptance"], "acceptance"),
        "test_commands": _strings(value["test_commands"], "test_commands"),
        "max_attempts": value["max_attempts"],
        "output_contract": dict(value["output_contract"]),
    }


def validate_result(value: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    if not isinstance(value, Mapping):
        raise DevFarmError("result must be an object")
    missing = sorted(_RESULT_FIELDS - set(value))
    if missing:
        raise DevFarmError(f"result missing fields: {', '.join(missing)}")
    status = _nonempty(value["status"], "status")
    if status not in _STATUSES:
        raise DevFarmError(f"status must be one of: {', '.join(sorted(_STATUSES))}")
    base_revision = _nonempty(value["base_revision"], "base_revision")
    if base_revision != manifest["base_revision"]:
        raise DevFarmError("result base_revision does not match manifest base_revision")
    changed = _paths(value["changed_files"], "changed_files")
    outside = sorted(set(changed) - set(manifest["allowed_files"]))
    if outside:
        raise DevFarmError(f"changed_files are outside manifest allowed_files: {', '.join(outside)}")
    if set(changed) & set(manifest["forbidden_files"]):
        raise DevFarmError("changed_files include forbidden_files")
    if not isinstance(value["tests_passed"], bool):
        raise DevFarmError("tests_passed must be a boolean")
    return {
        "schema_version": 1,
        "status": status,
        "base_revision": base_revision,
        "changed_files": changed,
        "tests_run": _strings(value["tests_run"], "tests_run"),
        "tests_passed": value["tests_passed"],
        "known_issues": _strings(value["known_issues"], "known_issues"),
        "assumptions": _strings(value["assumptions"], "assumptions"),
    }


def init_farm(root: str | Path) -> Path:
    repository = Path(root).resolve()
    farm = repository / ".devfarm"
    for name in ("tasks", "results", "shared", "status", "worktrees"):
        (farm / name).mkdir(parents=True, exist_ok=True)
    return farm


def write_manifest(root: str | Path, value: Mapping[str, Any]) -> Path:
    manifest = validate_manifest(value)
    path = init_farm(root) / "tasks" / f"{manifest['task_id']}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_result(root: str | Path, value: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> Path:
    normalized_manifest = validate_manifest(manifest)
    result = validate_result(value, manifest=normalized_manifest)
    directory = init_farm(root) / "results" / normalized_manifest["task_id"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def prepare_worktree(root: str | Path, *, task_id: str, branch: str, revision: str | None = None) -> Path:
    repository = Path(root).resolve()
    if not _TASK_ID.fullmatch(task_id):
        raise DevFarmError("task_id contains unsafe characters")
    if not _BRANCH.fullmatch(branch) or branch.endswith("/"):
        raise DevFarmError("worker branch must use the agent/<provider>/<task> form")
    if revision is not None and (not isinstance(revision, str) or not revision.strip() or revision.lstrip().startswith("-") or any(char in revision for char in "\r\n")):
        raise DevFarmError("revision must be a safe Git revision")
    worktree = init_farm(repository) / "worktrees" / task_id
    if worktree.exists():
        raise FileExistsError(worktree)
    command = ["git", "-c", f"safe.directory={repository.as_posix()}", "worktree", "add", "-b", branch, str(worktree)]
    if revision:
        command.append(_nonempty(revision, "revision"))
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git worktree add failed")
    return worktree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", type=Path, default=Path.cwd())
    worktree = sub.add_parser("prepare-worktree")
    worktree.add_argument("task_id")
    worktree.add_argument("branch")
    worktree.add_argument("--revision")
    worktree.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init_farm(args.root))
        else:
            print(prepare_worktree(args.root, task_id=args.task_id, branch=args.branch, revision=args.revision))
    except (DevFarmError, FileExistsError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
