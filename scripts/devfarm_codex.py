"""Development-only Codex AgentBackend execution inside a DevFarm worktree.

This module composes the existing DevFarm manifest, Git, AgentBackend, and
Host Verification boundaries.  It is deliberately not a production worker
loop, scheduler, or alternative state machine.  A Codex process may edit only
an attempt-specific worktree; the host derives the patch from Git (including
untracked files) and records a proposal that remains unintegrated.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping
from uuid import uuid4

from scripts.devfarm import (
    DevFarmError,
    canonical_digest,
    parse_host_test_command,
    prepare_worktree,
    sha256_text,
    validate_manifest,
    validate_patch,
    write_result,
)
from scripts.devfarm_worker import (
    HostVerificationRunner,
    _attempt_id,
    _bounded_test_output,
    _target_is_independent,
    _validate_host_test_targets,
    _write_immutable_text,
    _write_latest_result_projection,
    _write_verification_record,
)
from src.dev_agent.backends.codex_exec import CodexExecBackend
from src.dev_agent.backends.protocol import (
    AgentBackend,
    AgentBackendEvent,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendScope,
    AgentBackendStatus,
)


TERMINAL_BACKEND_STATUSES = frozenset(
    {
        AgentBackendStatus.COMPLETED,
        AgentBackendStatus.FAILED,
        AgentBackendStatus.CANCELLED,
        AgentBackendStatus.UNKNOWN,
        AgentBackendStatus.RECONCILING,
    }
)


@dataclass(frozen=True)
class CodexDevFarmAttempt:
    """Stable summary returned by one isolated Codex attempt."""

    task_id: str
    attempt_id: str
    workspace: Path
    backend_result: AgentBackendResult
    events: tuple[AgentBackendEvent, ...]
    changed_files: tuple[str, ...]
    patch: str
    patch_sha256: str
    authority_status: str
    host_verification_started: bool
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "workspace": str(self.workspace),
            "backend_status": self.backend_result.status.value,
            "backend_session_id": self.backend_result.session_id,
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "status": event.status.value if event.status is not None else None,
                }
                for event in self.events
            ],
            "changed_files": list(self.changed_files),
            "patch_sha256": self.patch_sha256,
            "authority_status": self.authority_status,
            "host_verification_started": self.host_verification_started,
            "result": dict(self.result),
        }


def _git(
    workspace: Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
) -> str:
    command = ["git", "-c", f"safe.directory={workspace.as_posix()}", *arguments]
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise DevFarmError(f"git {' '.join(arguments[:2])}: {detail}")
    return completed.stdout


def _resolve_revision(root: Path, revision: str) -> str:
    try:
        return _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()
    except DevFarmError as exc:
        raise DevFarmError(f"manifest base_revision cannot be resolved: {revision}") from exc


def _worktree_id(task_id: str, attempt_id: str) -> str:
    return f"codex-{sha256_text(f'{task_id}:{attempt_id}')[:32]}"


def _prepare_attempt_worktree(root: Path, manifest: Mapping[str, Any], attempt_id: str) -> Path:
    worktree_id = _worktree_id(str(manifest["task_id"]), attempt_id)
    branch = f"agent/codex-exec/{worktree_id}"
    return prepare_worktree(root, task_id=worktree_id, branch=branch, revision=str(manifest["base_revision"]))


def _request_for_attempt(manifest: Mapping[str, Any], workspace: Path, attempt_id: str) -> AgentBackendRequest:
    objective = (
        f"DevFarm task {manifest['task_id']}\n"
        f"Objective: {manifest['objective']}\n"
        f"Base revision: {manifest['base_revision']}\n"
        f"Allowed files: {', '.join(manifest['allowed_files'])}\n"
        f"Readable files: {', '.join(manifest['read_files'])}\n"
        f"Forbidden files: {', '.join(manifest['forbidden_files'])}\n"
        f"Requirements: {'; '.join(manifest['requirements'])}\n"
        f"Acceptance: {'; '.join(manifest['acceptance'])}\n"
        "Edit only the allowed files in this isolated workspace. Do not edit protected files, "
        "request credentials, push, merge, or claim host test results."
    )
    return AgentBackendRequest(
        task_id=str(manifest["task_id"]),
        objective=objective,
        scope=AgentBackendScope(
            workspace_id=str(workspace),
            allowed_paths=tuple(manifest["allowed_files"]),
        ),
        client_session_key=f"devfarm:{manifest['task_id']}:{attempt_id}",
        metadata={
            "base_revision": manifest["base_revision"],
            "read_files": tuple(manifest["read_files"]),
            "forbidden_files": tuple(manifest["forbidden_files"]),
            "test_commands": tuple(manifest["test_commands"]),
        },
    )


def _git_patch_with_untracked(workspace: Path, base_revision: str) -> str:
    """Return a binary-capable diff from base without changing the real index."""

    fd, index_name = tempfile.mkstemp(prefix="devfarm-codex-index-", suffix=".tmp")
    os.close(fd)
    index_path = Path(index_name)
    try:
        index_path.unlink()
        git_env = dict(os.environ)
        git_env["GIT_INDEX_FILE"] = str(index_path)
        _git(workspace, "read-tree", base_revision, env=git_env)
        _git(workspace, "add", "--all", "--", env=git_env)
        return _git(workspace, "diff", "--cached", "--binary", base_revision, "--", env=git_env)
    finally:
        try:
            index_path.unlink()
        except FileNotFoundError:
            pass


def _reject_ignored_changes(workspace: Path) -> None:
    status = _git(workspace, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching")
    ignored = [line for line in status.splitlines() if line.startswith("!! ")]
    if ignored:
        raise DevFarmError("Codex created ignored files that cannot be represented in the authoritative patch")


def _backend_status_result(result: AgentBackendResult) -> str:
    return "completed" if result.status is AgentBackendStatus.COMPLETED else "failed"


def _base_result(
    manifest: Mapping[str, Any],
    attempt_id: str,
    backend_result: AgentBackendResult,
    events: tuple[AgentBackendEvent, ...],
    changed_files: list[str],
    patch: str,
    *,
    elapsed_ms: int,
    known_issues: list[str],
) -> dict[str, Any]:
    backend = backend_result.reconciliation_metadata
    metrics = {
        "backend_id": "codex-exec",
        "backend_session_id": backend_result.session_id,
        "backend_status": backend_result.status.value,
        "event_count": len(events),
        "elapsed_ms": max(0, elapsed_ms),
        "changed_files": len(changed_files),
        "patch_sha256": sha256_text(patch),
        "host_verified": False,
        "result_accepted": False,
        "backend_metadata": dict(backend),
    }
    return {
        "status": _backend_status_result(backend_result),
        "attempt_id": attempt_id,
        "base_revision": manifest["base_revision"],
        "changed_files": changed_files,
        "tests_run": list(manifest["test_commands"]),
        "tests_passed": False,
        "model_claims": {
            "backend_status": backend_result.status.value,
            "events": len(events),
        },
        "proposed_test_commands": list(manifest["test_commands"]),
        "host_verified_tests": [],
        "worker_metrics": metrics,
        "known_issues": known_issues,
        "assumptions": ["Host-side Git inspection and verification are authoritative; backend claims are not."],
    }


def _persist_attempt(
    root: Path,
    manifest: Mapping[str, Any],
    result: dict[str, Any],
    patch: str,
    *,
    notes: str,
) -> None:
    write_result(root, result, manifest=manifest)
    attempt_dir = root / ".devfarm" / "results" / str(manifest["task_id"]) / "attempts" / str(result["attempt_id"])
    _write_immutable_text(attempt_dir / "patch.diff", patch)
    _write_immutable_text(attempt_dir / "notes.md", notes + "\n")


def _collect_events(backend: AgentBackend, session_id: str, existing: dict[int, AgentBackendEvent]) -> None:
    for event in backend.events(session_id):
        if not isinstance(event, AgentBackendEvent):
            raise DevFarmError("AgentBackend returned a non-typed event")
        prior = existing.get(event.sequence)
        if prior is not None and prior != event:
            raise DevFarmError(f"AgentBackend emitted conflicting event sequence: {event.sequence}")
        existing[event.sequence] = event


def _await_backend(
    backend: AgentBackend,
    session_id: str,
    *,
    max_wait_seconds: float,
    event_map: dict[int, AgentBackendEvent],
) -> AgentBackendResult:
    deadline = time.monotonic() + max_wait_seconds
    while True:
        _collect_events(backend, session_id, event_map)
        result = backend.result(session_id)
        if not isinstance(result, AgentBackendResult):
            raise DevFarmError("AgentBackend returned a non-typed result")
        if result.status in TERMINAL_BACKEND_STATUSES:
            _collect_events(backend, session_id, event_map)
            return result
        if time.monotonic() >= deadline:
            backend.cancel(session_id)
            return backend.result(session_id)
        time.sleep(0.05)


def _run_host_verification(
    workspace: Path,
    manifest: Mapping[str, Any],
    changed_files: list[str],
    *,
    trust_level: str,
    operator_approved: bool,
) -> tuple[list[dict[str, Any]], bool]:
    if trust_level == "STATIC_ONLY":
        return [], False
    if trust_level == "OS_SANDBOXED":
        raise DevFarmError("OS_SANDBOXED verification is not available on this host")
    if trust_level != "TRUSTED_HOST_EXEC":
        raise DevFarmError(f"unsupported verification trust level: {trust_level}")
    if operator_approved is not True:
        raise DevFarmError("TRUSTED_HOST_EXEC requires explicit operator approval for this attempt")

    runner = HostVerificationRunner(timeout_seconds=120)
    changed_set = frozenset(changed_files)
    verified: list[dict[str, Any]] = []
    independent = False
    started = time.monotonic()
    for command in manifest["test_commands"]:
        if time.monotonic() - started >= 120:
            raise DevFarmError("worker verification exceeded total wall-clock budget")
        tokens = parse_host_test_command(command)
        _validate_host_test_targets(workspace, tokens)
        targets = {
            token.split("::", 1)[0].replace("\\", "/")
            for token in tokens[3:]
            if token not in {"-q", "-x"} and not token.startswith("--maxfail=")
        }
        if any(_target_is_independent(workspace, target, changed_set) for target in targets):
            independent = True
        host_result = runner.run(tokens, cwd=workspace)
        verified.append(
            {
                "command": command,
                "exit_code": host_result["returncode"],
                "passed": host_result["returncode"] == 0 and not host_result["timed_out"],
                "stdout": _bounded_test_output(host_result["stdout"]),
                "stderr": _bounded_test_output(host_result["stderr"]),
                "timed_out": host_result["timed_out"],
                "output_truncated": host_result["output_truncated"],
                "containment": host_result["containment"],
            }
        )
    return verified, independent


def run_codex_attempt(
    root: str | Path,
    manifest_path: str | Path,
    *,
    backend: AgentBackend | None = None,
    attempt_id: str | None = None,
    trust_level: str = "STATIC_ONLY",
    operator_approved: bool = False,
    max_wait_seconds: float = 600.0,
) -> dict[str, Any]:
    """Run one Codex edit attempt and return a host-authoritative summary.

    The default is static validation only.  Explicit trusted host execution
    is attempt-scoped and requires operator approval; no mode automatically
    integrates a result into the official branch.
    """

    root_path = Path(root).resolve()
    manifest_file = Path(manifest_path)
    try:
        manifest_value = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevFarmError(f"could not read manifest: {manifest_file}") from exc
    manifest = validate_manifest(manifest_value)
    selected_attempt = _attempt_id(attempt_id or uuid4().hex)
    if isinstance(max_wait_seconds, bool) or not isinstance(max_wait_seconds, (int, float)) or max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be positive")
    if trust_level not in {"STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"}:
        raise DevFarmError(f"unsupported verification trust level: {trust_level}")

    _resolve_revision(root_path, str(manifest["base_revision"]))
    workspace = _prepare_attempt_worktree(root_path, manifest, selected_attempt)
    request = _request_for_attempt(manifest, workspace, selected_attempt)
    selected_backend = backend or CodexExecBackend(sandbox_mode="workspace-write")
    event_map: dict[int, AgentBackendEvent] = {}
    started = time.monotonic()
    try:
        session = selected_backend.start(request)
        backend_result = _await_backend(
            selected_backend,
            session.session_id,
            max_wait_seconds=float(max_wait_seconds),
            event_map=event_map,
        )
    except BaseException as exc:
        backend_result = AgentBackendResult(
            session_id=f"failed:{selected_attempt}",
            status=AgentBackendStatus.FAILED,
            reconciliation_metadata={"error_type": type(exc).__name__},
        )
        events = tuple(event_map.values())
        result = _base_result(
            manifest,
            selected_attempt,
            backend_result,
            events,
            [],
            "",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            known_issues=[f"backend execution failed: {type(exc).__name__}"],
        )
        _persist_attempt(root_path, manifest, result, "", notes=result["known_issues"][0])
        return {
            "authority_status": "REJECTED",
            "host_verification_started": False,
            "workspace": str(workspace),
            "patch": "",
            "patch_sha256": sha256_text(""),
            "changed_files": [],
            "attempt_id": selected_attempt,
            "verification_trust_level": trust_level,
            "result": result,
        }

    events = tuple(sorted(event_map.values(), key=lambda event: event.sequence))
    patch = ""
    changed_files: list[str] = []
    authority_status = "ACCEPTED"
    known_issues: list[str] = []
    try:
        _reject_ignored_changes(workspace)
        expected_head = _resolve_revision(root_path, str(manifest["base_revision"]))
        actual_head = _git(workspace, "rev-parse", "HEAD").strip()
        if actual_head != expected_head:
            raise DevFarmError("Codex attempt worktree HEAD does not match manifest base_revision")
        patch = _git_patch_with_untracked(workspace, expected_head)
        changed_files = validate_patch(patch, manifest=manifest)
        if not changed_files:
            raise DevFarmError("Codex attempt produced no authoritative patch")
    except DevFarmError as exc:
        authority_status = "REJECTED"
        known_issues.append(str(exc))
        patch = ""
        changed_files = []

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = _base_result(
        manifest,
        selected_attempt,
        backend_result,
        events,
        changed_files,
        patch,
        elapsed_ms=elapsed_ms,
        known_issues=known_issues,
    )
    if authority_status == "REJECTED":
        result["status"] = "failed"
        _persist_attempt(root_path, manifest, result, patch, notes="; ".join(known_issues))
        return {
            "authority_status": authority_status,
            "host_verification_started": False,
            "workspace": str(workspace),
            "patch": patch,
            "patch_sha256": sha256_text(patch),
            "changed_files": changed_files,
            "attempt_id": selected_attempt,
            "verification_trust_level": trust_level,
            "result": result,
        }

    verified, independent = _run_host_verification(
        workspace,
        manifest,
        changed_files,
        trust_level=trust_level,
        operator_approved=operator_approved,
    )
    tests_passed = bool(verified) and all(item["passed"] for item in verified)
    result["tests_passed"] = tests_passed
    result["host_verified_tests"] = verified
    metrics = dict(result["worker_metrics"])
    metrics.update(
        {
            "host_verified": bool(verified),
            "host_verified_test_count": len(verified),
            "host_tests_passed": tests_passed,
            "independent_verification": independent,
            "verification_trust_level": trust_level,
            "operator_approved": operator_approved is True,
            "result_accepted": bool(
                tests_passed
                and independent
                and trust_level == "TRUSTED_HOST_EXEC"
                and operator_approved is True
            ),
        }
    )
    result["worker_metrics"] = metrics
    if trust_level == "STATIC_ONLY":
        result["known_issues"] = ["STATIC_ONLY: patched code was not executed on the host"]
    elif not tests_passed:
        result["status"] = "failed"
        result["known_issues"] = ["host verification did not pass"]
    elif not independent:
        result["status"] = "failed"
        result["known_issues"] = ["host verification did not include an unmodified trusted target"]

    _persist_attempt(root_path, manifest, result, patch, notes="Codex attempt host verification completed.")
    verification_record = {
        "attempt_id": selected_attempt,
        "patch_sha256": sha256_text(patch),
        "manifest_sha256": canonical_digest(manifest),
        "base_revision": manifest["base_revision"],
        "test_spec_sha256": canonical_digest(manifest["test_commands"]),
        "containment_level": trust_level,
        "verified_tests": verified,
        "independent_verification": independent,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operator_approved": operator_approved is True,
    }
    verification_id = _write_verification_record(root_path, manifest, selected_attempt, verification_record)
    result["verification_id"] = verification_id
    _write_latest_result_projection(root_path, result, manifest=manifest)
    return {
        "authority_status": authority_status,
        "host_verification_started": bool(verified),
        "workspace": str(workspace),
        "patch": patch,
        "patch_sha256": sha256_text(patch),
        "changed_files": changed_files,
        "attempt_id": selected_attempt,
        "verification_trust_level": trust_level,
        "result": result,
    }


__all__ = ["CodexDevFarmAttempt", "run_codex_attempt"]
