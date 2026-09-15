"""Shared development-only task, patch, and result contract validation.

The command-line entrypoint keeps compatibility exports, but the validation
rules live here so Worker, Commander, and Host services do not depend on the
CLI module to enforce the same contract.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
from typing import Any, Mapping

from scripts.devfarm_errors import DevFarmError
from src.dev_agent.security.protected_paths import PROTECTED_AUTHORITY_PATHS, is_protected_path


def sha256_text(value: str) -> str:
    """SHA-256 hex digest of a UTF-8 encoded string."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_digest(value: Any) -> str:
    """Deterministic SHA-256 hex digest of a JSON-serialisable value."""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_STATUSES = {"pending", "running", "completed", "failed", "blocked_external"}
_PROTECTED_FILES = PROTECTED_AUTHORITY_PATHS
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
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
VERIFICATION_TRUST_LEVELS = frozenset({"STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"})
_EMBEDDED_OUTPUT_KEYS = frozenset({"raw_output", "conversation", "stdout", "stderr", "patch"})

# Development-worker inputs are deliberately bounded before any provider call
# or host-side worktree/test allocation.  These are contract limits, not a
# replacement for the provider and host resource governors.
MAX_OBJECTIVE_CHARS = 4_000
MAX_REQUIREMENTS = 32
MAX_REQUIREMENT_CHARS = 4_000
MAX_ACCEPTANCE = 32
MAX_ACCEPTANCE_CHARS = 4_000
MAX_TEST_COMMANDS = 16
MAX_OUTBOUND_FILES = 64
MAX_OUTBOUND_BYTES = 512 * 1024


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


def _reject_embedded_output(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        if _EMBEDDED_OUTPUT_KEYS.intersection(value):
            raise DevFarmError(f"{name} must not contain raw Worker output")
        for child in value.values():
            _reject_embedded_output(child, name)
    elif isinstance(value, list):
        for child in value:
            _reject_embedded_output(child, name)


def parse_host_test_command(command: str) -> list[str]:
    """Parse one strictly allowlisted host verification command."""

    if not isinstance(command, str) or not command.strip():
        raise DevFarmError("test_commands must contain non-empty strings")
    if any(char in command for char in ";&|<>`$()\r\n"):
        raise DevFarmError("test_commands contain shell syntax")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        raise DevFarmError("test_commands must be parseable without a shell") from exc
    if len(tokens) < 3 or tokens[0] not in {"python", "python3", "py"} or tokens[1:2] != ["-m"] or tokens[2] not in {"pytest", "compileall"}:
        raise DevFarmError("test_commands must use python -m pytest or python -m compileall")
    targets = 0
    for token in tokens[3:]:
        if token in {"-q", "-x"}:
            continue
        if token.startswith("--maxfail="):
            value = token.partition("=")[2]
            if not value.isdigit() or not 0 < int(value) <= 10:
                raise DevFarmError("test_commands --maxfail must be between 1 and 10")
            continue
        if token.startswith("-"):
            raise DevFarmError(f"test_commands contain an unsafe option: {token}")
        target = token.split("::", 1)[0]
        parsed = PurePosixPath(target.replace("\\", "/"))
        if parsed.is_absolute() or ".." in parsed.parts or not target:
            raise DevFarmError("test_commands paths must stay relative to the worker worktree")
        targets += 1
    if tokens[2] == "pytest" and targets == 0:
        raise DevFarmError("pytest test_commands must name at least one relative target")
    return tokens


def _test_commands(value: Any) -> list[str]:
    commands = _strings(value, "test_commands")
    normalized: list[str] = []
    for command in commands:
        parse_host_test_command(command)
        normalized.append(command)
    return normalized


def _is_protected(path: str) -> bool:
    return is_protected_path(path)


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
    task_type = _nonempty(value.get("task_type", "unspecified"), "task_type")
    if len(task_type) > 64:
        raise DevFarmError("task_type must be at most 64 characters")
    objective = _nonempty(value["objective"], "objective")
    if len(objective) > MAX_OBJECTIVE_CHARS:
        raise DevFarmError(f"objective must be at most {MAX_OBJECTIVE_CHARS} characters")
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
    if len(outbound) > MAX_OUTBOUND_FILES:
        raise DevFarmError(f"outbound_files must contain at most {MAX_OUTBOUND_FILES} paths")
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
    requirements = _strings(value["requirements"], "requirements")
    acceptance = _strings(value["acceptance"], "acceptance")
    test_commands = _test_commands(value["test_commands"])
    if len(requirements) > MAX_REQUIREMENTS:
        raise DevFarmError(f"requirements must contain at most {MAX_REQUIREMENTS} items")
    if len(acceptance) > MAX_ACCEPTANCE:
        raise DevFarmError(f"acceptance must contain at most {MAX_ACCEPTANCE} items")
    if any(len(item) > MAX_REQUIREMENT_CHARS for item in requirements):
        raise DevFarmError(f"requirements entries must be at most {MAX_REQUIREMENT_CHARS} characters")
    if any(len(item) > MAX_ACCEPTANCE_CHARS for item in acceptance):
        raise DevFarmError(f"acceptance entries must be at most {MAX_ACCEPTANCE_CHARS} characters")
    if len(test_commands) > MAX_TEST_COMMANDS:
        raise DevFarmError(f"test_commands must contain at most {MAX_TEST_COMMANDS} items")
    normalized = {
        "schema_version": 1,
        "task_id": task_id,
        "task_type": task_type,
        "objective": objective,
        "base_revision": base_revision,
        "allowed_files": allowed,
        "read_files": read,
        "forbidden_files": forbidden,
        "external_provider_allowed": external_provider_allowed,
        "approved_provider_ids": approved_provider_ids,
        "outbound_files": outbound,
        "requirements": requirements,
        "acceptance": acceptance,
        "test_commands": test_commands,
        "max_attempts": value["max_attempts"],
        "output_contract": dict(value["output_contract"]),
    }
    rework_handoff = value.get("rework_handoff")
    if rework_handoff is not None:
        if not isinstance(rework_handoff, Mapping):
            raise DevFarmError("rework_handoff must be an object")
        _reject_embedded_output(rework_handoff, "rework_handoff")
        try:
            encoded_handoff = json.dumps(rework_handoff, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DevFarmError("rework_handoff must be JSON-serializable") from exc
        if len(encoded_handoff.encode("utf-8")) > 20_000:
            raise DevFarmError("rework_handoff is too large")
        normalized["rework_handoff"] = dict(rework_handoff)
    return normalized


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


def _validate_hunk_ranges(lines: list[str]) -> None:
    """Validate unified-diff hunk counts before delegating to Git."""

    active: tuple[int, int] | None = None
    old_count = 0
    new_count = 0

    def finish() -> None:
        nonlocal active, old_count, new_count
        if active is None:
            return
        expected_old, expected_new = active
        if (old_count, new_count) != (expected_old, expected_new):
            raise DevFarmError(
                "patch hunk line counts do not match header: "
                f"expected {expected_old}/{expected_new}, got {old_count}/{new_count}"
            )
        active = None
        old_count = 0
        new_count = 0

    for line in lines:
        if line.startswith("diff --git "):
            finish()
            continue
        if line.startswith("@@"):
            finish()
            match = _HUNK_HEADER.fullmatch(line)
            if match is None:
                raise DevFarmError("patch hunk header is invalid")
            active = (int(match.group(2) or "1"), int(match.group(4) or "1"))
            continue
        if active is None:
            continue
        if line == r"\ No newline at end of file":
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise DevFarmError("patch hunk contains an invalid line")
        if line[0] in {" ", "-"}:
            old_count += 1
        if line[0] in {" ", "+"}:
            new_count += 1
    finish()


def normalize_patch_hunk_counts(patch: Any) -> tuple[str, list[str]]:
    """Correct only literal unified-diff hunk counts at the transport edge."""

    if not isinstance(patch, str):
        raise DevFarmError("worker patch must be a string")
    if not patch:
        return patch, []
    lines = patch.splitlines(keepends=True)
    replacements: dict[int, str] = {}
    normalizations: list[str] = []
    active: tuple[int, re.Match[str], int, int] | None = None

    def finish() -> None:
        nonlocal active
        if active is None:
            return
        line_index, match, old_count, new_count = active
        expected_old = int(match.group(2) or "1")
        expected_new = int(match.group(4) or "1")
        if (old_count, new_count) != (expected_old, expected_new):
            header = lines[line_index].rstrip("\r\n")
            second_marker = header.find("@@", 2)
            suffix = header[second_marker + 2 :] if second_marker >= 0 else ""
            replacements[line_index] = (
                f"@@ -{match.group(1)},{old_count} +{match.group(3)},{new_count} @@{suffix}"
                + lines[line_index][len(header) :]
            )
            normalizations.append(f"hunk_line_counts:{expected_old}/{expected_new}->{old_count}/{new_count}")
        active = None

    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if line.startswith("diff --git "):
            finish()
            continue
        if line.startswith("@@"):
            finish()
            match = _HUNK_HEADER.fullmatch(line)
            if match is None:
                raise DevFarmError("patch hunk header is invalid")
            active = (index, match, 0, 0)
            continue
        if active is None:
            continue
        if line == r"\ No newline at end of file":
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise DevFarmError("patch hunk contains an invalid line")
        index_of_header, match, old_count, new_count = active
        if line[0] in {" ", "-"}:
            old_count += 1
        if line[0] in {" ", "+"}:
            new_count += 1
        active = (index_of_header, match, old_count, new_count)
    finish()
    if not replacements:
        return patch, []
    normalized_lines = list(lines)
    for index, replacement in replacements.items():
        normalized_lines[index] = replacement
    return "".join(normalized_lines), normalizations


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
    _validate_hunk_ranges(lines)
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
    worker_metrics = value.get("worker_metrics", {})
    if not isinstance(worker_metrics, Mapping):
        raise DevFarmError("worker_metrics must be an object")
    normalized = {
        "schema_version": 1,
        "status": status,
        "base_revision": base_revision,
        "changed_files": changed,
        "tests_run": _strings(value["tests_run"], "tests_run"),
        "tests_passed": value["tests_passed"],
        "model_claims": dict(model_claims),
        "proposed_test_commands": _strings(proposed_test_commands, "proposed_test_commands"),
        "host_verified_tests": list(host_verified_tests),
        "worker_metrics": dict(worker_metrics),
        "known_issues": _strings(value["known_issues"], "known_issues"),
        "assumptions": _strings(value["assumptions"], "assumptions"),
    }
    attempt_id = value.get("attempt_id")
    if attempt_id is not None:
        attempt_id = _nonempty(attempt_id, "attempt_id")
        if not _ATTEMPT_ID.fullmatch(attempt_id):
            raise DevFarmError("attempt_id contains unsafe characters")
        normalized["attempt_id"] = attempt_id
    verification_id = value.get("verification_id")
    if verification_id is not None:
        normalized["verification_id"] = _nonempty(verification_id, "verification_id")
    return normalized


__all__ = [
    "DevFarmError",
    "MAX_ACCEPTANCE",
    "MAX_ACCEPTANCE_CHARS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_OUTBOUND_BYTES",
    "MAX_OUTBOUND_FILES",
    "MAX_REQUIREMENT_CHARS",
    "MAX_REQUIREMENTS",
    "MAX_TEST_COMMANDS",
    "VERIFICATION_TRUST_LEVELS",
    "canonical_digest",
    "normalize_patch_hunk_counts",
    "parse_host_test_command",
    "sha256_text",
    "validate_manifest",
    "validate_patch",
    "validate_result",
]
