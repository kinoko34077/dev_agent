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
import shlex
import subprocess
from typing import Any, Mapping

from src.dev_agent.security.audit import AuditRecorder


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
    "external_provider_allowed",
    "approved_provider_ids",
    "outbound_files",
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


def _test_commands(value: Any) -> list[str]:
    commands = _strings(value, "test_commands")
    normalized: list[str] = []
    for command in commands:
        if any(char in command for char in ";&|<>`$()\r\n"):
            raise DevFarmError("test_commands contain shell syntax")
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            raise DevFarmError("test_commands must be parseable without a shell") from exc
        if len(tokens) < 3 or tokens[0] not in {"python", "python3", "py"} or tokens[1:2] != ["-m"] or tokens[2] not in {"pytest", "compileall"}:
            raise DevFarmError("test_commands must use python -m pytest or python -m compileall")
        if any(token in {"-c", "--config-file", "--rootdir", "--confcutdir", "--pyargs"} or token.startswith(("--config-file=", "--rootdir=", "--confcutdir=")) for token in tokens[3:]):
            raise DevFarmError("test_commands contain an unsafe pytest option")
        for token in tokens[3:]:
            if token.startswith("-"):
                continue
            parsed = PurePosixPath(token.replace("\\", "/"))
            if parsed.is_absolute() or ".." in parsed.parts:
                raise DevFarmError("test_commands paths must stay relative to the worker worktree")
        normalized.append(command)
    return normalized


def _is_protected(path: str) -> bool:
    parts = PurePosixPath(path).parts
    protected_names = AuditRecorder.SECRET_KEYS | {
        "credential",
        "credentials",
        "secret",
        "secrets",
        "private",
        "password",
        "token",
        "tokens",
    }
    return (
        path in _PROTECTED_FILES
        or path == "recovery"
        or path.startswith("recovery/")
        or path == ".devfarm"
        or path.startswith(".devfarm/")
        or any(part == ".git" or part.startswith(".env") or part.lower() in protected_names for part in parts)
    )


def _revision(value: Any, name: str) -> str:
    revision = _nonempty(value, name)
    if revision.startswith("-") or any(char.isspace() or char in "\r\n" for char in revision) or len(revision) > 200:
        raise DevFarmError(f"{name} must be a safe Git revision")
    return revision


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
    base_revision = _revision(value["base_revision"], "base_revision")
    allowed = _paths(value["allowed_files"], "allowed_files")
    read = _paths(value["read_files"], "read_files")
    forbidden = _paths(value["forbidden_files"], "forbidden_files")
    if not isinstance(value["external_provider_allowed"], bool):
        raise DevFarmError("external_provider_allowed must be a boolean")
    external_provider_allowed = value["external_provider_allowed"]
    approved_provider_ids = _strings(value["approved_provider_ids"], "approved_provider_ids")
    if len(approved_provider_ids) != len(set(approved_provider_ids)):
        raise DevFarmError("approved_provider_ids must not contain duplicates")
    outbound = _paths(value["outbound_files"], "outbound_files")
    readable = set(read) | set(allowed)
    outside_outbound = sorted(set(outbound) - readable)
    if outside_outbound:
        raise DevFarmError(f"outbound_files must be a subset of read_files and allowed_files: {', '.join(outside_outbound)}")
    protected_read = sorted(path for path in [*allowed, *read, *outbound] if _is_protected(path))
    if protected_read:
        raise DevFarmError(f"protected files cannot be read or sent by a worker: {', '.join(dict.fromkeys(protected_read))}")
    if external_provider_allowed and not approved_provider_ids:
        raise DevFarmError("approved_provider_ids is required when external_provider_allowed is true")
    if not external_provider_allowed and (approved_provider_ids or outbound):
        raise DevFarmError("external provider approval and outbound_files require external_provider_allowed=true")
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
        "external_provider_allowed": external_provider_allowed,
        "approved_provider_ids": approved_provider_ids,
        "outbound_files": outbound,
        "requirements": _strings(value["requirements"], "requirements"),
        "acceptance": _strings(value["acceptance"], "acceptance"),
        "test_commands": _test_commands(value["test_commands"]),
        "max_attempts": value["max_attempts"],
        "output_contract": dict(value["output_contract"]),
    }


def _diff_path(raw: str, prefix: str) -> str | None:
    raw = raw.strip()
    if raw == "/dev/null":
        return None
    if raw.startswith('"'):
        try:
            values = shlex.split(raw, posix=True)
        except ValueError as exc:
            raise DevFarmError(f"patch path is not valid: {raw}") from exc
        if len(values) != 1:
            raise DevFarmError(f"patch path is not valid: {raw}")
        raw = values[0]
    if not raw.startswith(prefix + "/"):
        raise DevFarmError(f"patch path has an invalid prefix: {raw}")
    return _path(raw[len(prefix) + 1 :], "patch path")


def _marker_path(line: str, prefix: str) -> str | None:
    raw = line[4:].split("\t", 1)[0].strip()
    return _diff_path(raw, prefix)


def validate_patch(patch: Any, *, manifest: Mapping[str, Any]) -> list[str]:
    """Return actual changed paths after deterministic, fail-closed checks."""

    normalized_manifest = validate_manifest(manifest)
    if not isinstance(patch, str):
        raise DevFarmError("worker patch must be a string")
    if not patch:
        return []
    lines = patch.splitlines()
    if any(line == "GIT binary patch" or line.startswith("Binary files ") for line in lines):
        raise DevFarmError("binary patches are not allowed")
    if any("Subproject commit " in line for line in lines):
        raise DevFarmError("submodule patches are not allowed")
    for line in lines:
        match = re.search(r"(?:old mode|new mode|new file mode|deleted file mode) (\d{6})$", line)
        if match and match.group(1) not in {"100644", "100664"}:
            mode = match.group(1)
            if mode == "120000":
                raise DevFarmError("symlink patches are not allowed")
            if mode == "160000":
                raise DevFarmError("submodule patches are not allowed")
            raise DevFarmError(f"unsafe file mode in patch: {mode}")

    actual: set[str] = set()
    saw_header = False
    for line in lines:
        if line.startswith("diff --git "):
            saw_header = True
            try:
                values = shlex.split(line[len("diff --git ") :], posix=True)
            except ValueError as exc:
                raise DevFarmError("patch diff header is invalid") from exc
            if len(values) != 2:
                raise DevFarmError("patch diff header must contain exactly two paths")
            old_path = _diff_path(values[0], "a")
            new_path = _diff_path(values[1], "b")
            if old_path is not None:
                actual.add(old_path)
            if new_path is not None:
                actual.add(new_path)
        elif line.startswith("--- "):
            marker = _marker_path(line, "a")
            if marker is not None:
                actual.add(marker)
        elif line.startswith("+++ "):
            marker = _marker_path(line, "b")
            if marker is not None:
                actual.add(marker)
    if not saw_header:
        raise DevFarmError("patch is not a unified diff")
    outside = sorted(actual - set(normalized_manifest["allowed_files"]))
    if outside:
        raise DevFarmError(f"patch changed files outside manifest allowed_files: {', '.join(outside)}")
    forbidden = sorted(actual & set(normalized_manifest["forbidden_files"]))
    if forbidden:
        raise DevFarmError(f"patch changed forbidden files: {', '.join(forbidden)}")
    protected = sorted(path for path in actual if _is_protected(path))
    if protected:
        raise DevFarmError(f"patch changed protected files: {', '.join(protected)}")
    return sorted(actual)


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
    base_revision = _revision(value["base_revision"], "base_revision")
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
    model_claims = value.get("model_claims", {})
    if not isinstance(model_claims, Mapping):
        raise DevFarmError("model_claims must be an object")
    proposed_test_commands = value.get("proposed_test_commands", value["tests_run"])
    host_verified_tests = value.get("host_verified_tests", [])
    if not isinstance(host_verified_tests, list):
        raise DevFarmError("host_verified_tests must be a list")
    return {
        "schema_version": 1,
        "status": status,
        "base_revision": base_revision,
        "changed_files": changed,
        "tests_run": _strings(value["tests_run"], "tests_run"),
        "tests_passed": value["tests_passed"],
        "model_claims": dict(model_claims),
        "proposed_test_commands": _strings(proposed_test_commands, "proposed_test_commands"),
        "host_verified_tests": list(host_verified_tests),
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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevFarmError(f"could not read JSON file {path}: {exc}") from exc


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
    manifest = sub.add_parser("validate-manifest")
    manifest.add_argument("path", type=Path)
    result = sub.add_parser("validate-result")
    result.add_argument("path", type=Path)
    result.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init_farm(args.root))
        elif args.command == "prepare-worktree":
            print(prepare_worktree(args.root, task_id=args.task_id, branch=args.branch, revision=args.revision))
        elif args.command == "validate-manifest":
            print(json.dumps(validate_manifest(_read_json(args.path)), ensure_ascii=False, indent=2))
        else:
            normalized_manifest = validate_manifest(_read_json(args.manifest))
            normalized_result = validate_result(_read_json(args.path), manifest=normalized_manifest)
            print(json.dumps(normalized_result, ensure_ascii=False, indent=2))
    except (DevFarmError, FileExistsError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
