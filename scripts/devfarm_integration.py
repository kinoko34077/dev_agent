"""Host-owned proof and integration service for verified DevFarm patches.

This module owns the final Git mutation boundary for development plans.  It
consumes the public Commander plan/query interfaces and the shared repository
primitives, but it does not choose providers, grant approval, verify policy,
or schedule work.  Review remains a separate durable Supervisor decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping, Sequence
import uuid

from scripts.devfarm import (
    DevFarmError,
    canonical_digest,
    sha256_text,
    validate_manifest,
    validate_patch,
    validate_result,
)
from scripts.devfarm_artifacts import write_immutable_text
from scripts.devfarm_commander import CommanderPlanStore, record_result, refresh_plan
from scripts.devfarm_manifests import load_worker_manifest
from scripts.devfarm_plan_queries import require_approved_review_decision, require_task, result_reference
from scripts.devfarm_repository import git, git_diff_digest, read_json, repository_path, resolved_revision


_SAFE_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _git_process(
    cwd: Path,
    *arguments: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-c", f"safe.directory={cwd.as_posix()}", *arguments]
    raw_input = input_text.encode("utf-8") if input_text is not None else None
    result = subprocess.run(
        command,
        cwd=cwd,
        input=raw_input,
        capture_output=True,
        text=False,
        check=False,
    )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
    )


def _git_output(cwd: Path, *arguments: str) -> str:
    result = _git_process(cwd, *arguments)
    if result.returncode != 0:
        raise DevFarmError(result.stderr.strip() or result.stdout.strip() or "Git command failed")
    return result.stdout.strip()


def _git_status(cwd: Path) -> str:
    """Return tracked/untracked worktree changes excluding DevFarm artifacts."""

    return _git_output(
        cwd,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).devfarm",
    )


def _bounded_text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DevFarmError(f"{name} is too long")
    return normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verified_worker_patch(root: str | Path, task: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Return the exact patch whose independent Host verification passed."""

    root_path = Path(root).resolve()
    if task.get("owner") != "worker":
        raise DevFarmError("verified worker patch is required only for worker tasks")
    manifest_path, manifest = load_worker_manifest(root_path, task)
    attempt_id = task.get("last_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DevFarmError("worker integration requires a verified attempt_id")
    result_ref = task.get("result_ref") or result_reference(task["task_id"], attempt_id)
    result_path = repository_path(root_path, result_ref, required_parent=".devfarm/results")
    if not result_path.is_file():
        raise DevFarmError("worker integration result artifact is missing")
    result = validate_result(read_json(result_path), manifest=manifest)
    if result.get("status") != "completed":
        raise DevFarmError("worker integration requires a completed result artifact")
    if result.get("attempt_id") != attempt_id:
        raise DevFarmError("integration source_attempt_id does not match result artifact")
    patch_path = result_path.parent / "patch.diff"
    if not patch_path.is_file():
        raise DevFarmError("verified worker patch artifact is missing")
    try:
        patch = patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DevFarmError("verified worker patch artifact cannot be read") from exc
    changed_files = validate_patch(patch, manifest=manifest)
    if not changed_files:
        raise DevFarmError("worker integration requires a non-empty verified patch")
    verification_dir = result_path.parent / "verification"
    if not verification_dir.is_dir():
        raise DevFarmError("worker integration requires an immutable verification directory")
    records: list[Mapping[str, Any]] = []
    for verification_path in sorted(verification_dir.iterdir()):
        if verification_path.suffix != ".json" or not verification_path.stem or verification_path.stem.startswith("."):
            continue
        try:
            record = read_json(verification_path)
        except DevFarmError:
            continue
        if isinstance(record, Mapping):
            records.append(record)
    if not records:
        raise DevFarmError("worker integration requires at least one verification record")
    patch_digest = sha256_text(patch)
    manifest_digest = canonical_digest(manifest)
    test_spec_digest = canonical_digest(manifest["test_commands"])
    trust_priority = {"OS_SANDBOXED": 2, "TRUSTED_HOST_EXEC": 1}
    verification: Mapping[str, Any] | None = None
    for record in sorted(
        records,
        key=lambda item: trust_priority.get(item.get("containment_level", ""), -1),
        reverse=True,
    ):
        if record.get("attempt_id") != attempt_id or record.get("base_revision") != manifest["base_revision"]:
            continue
        if record.get("patch_sha256") != patch_digest:
            continue
        if record.get("manifest_sha256") != manifest_digest or record.get("test_spec_sha256") != test_spec_digest:
            continue
        trust = record.get("containment_level")
        if trust == "TRUSTED_HOST_EXEC" and record.get("operator_approved") is not True:
            continue
        if trust not in {"TRUSTED_HOST_EXEC", "OS_SANDBOXED"}:
            continue
        verified_tests = record.get("verified_tests")
        if (
            not isinstance(verified_tests, list)
            or not verified_tests
            or not all(isinstance(item, Mapping) and item.get("passed") is True for item in verified_tests)
        ):
            continue
        if record.get("independent_verification") is not True:
            continue
        verification = record
        break
    if verification is None:
        raise DevFarmError(
            "no qualifying verification record found: requires "
            "TRUSTED_HOST_EXEC+operator_approved or OS_SANDBOXED, matching digests, passing independent tests"
        )
    return patch, manifest, attempt_id


def _prove_worker_patch_in_revision(
    root: Path,
    patch: str,
    revision: str,
    changed_files: Sequence[str],
) -> None:
    """Prove that the verified patch is represented by one integration commit."""

    try:
        parent = git(root, "rev-parse", f"{revision}^{{commit}}^")
    except DevFarmError as exc:
        raise DevFarmError("integration revision must have a parent commit") from exc
    with tempfile.TemporaryDirectory(prefix="devfarm-integration-") as directory:
        worktree = Path(directory)
        git(root, "worktree", "add", "--detach", worktree.as_posix(), parent)
        try:
            applied = subprocess.run(
                ["git", "-c", f"safe.directory={worktree.as_posix()}", "apply", "--whitespace=error", "-"],
                cwd=worktree,
                input=patch.encode("utf-8"),
                capture_output=True,
                text=False,
                check=False,
            )
            if applied.returncode != 0:
                stderr = applied.stderr.decode("utf-8", errors="replace").strip()
                stdout = applied.stdout.decode("utf-8", errors="replace").strip()
                raise DevFarmError(stderr or stdout or "patch does not apply to integration parent")
            git(worktree, "add", "--all")
            compared = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={worktree.as_posix()}",
                    "diff",
                    "--cached",
                    "--exit-code",
                    revision,
                    "--",
                    *changed_files,
                ],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            if compared.returncode != 0:
                raise DevFarmError("verified worker patch is not reflected in integration revision")
        finally:
            subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={root.as_posix()}",
                    "worktree",
                    "remove",
                    "--force",
                    worktree.as_posix(),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )


def _advance_dependent_manifest_baselines(
    root: Path,
    plan: dict[str, Any],
    integrated_task_id: str,
    baseline_revision: str,
) -> None:
    """Issue immutable dependent manifests after a code dependency integrates."""

    for dependent in plan["tasks"]:
        if integrated_task_id not in dependent.get("dependencies", []):
            continue
        if dependent.get("owner") != "worker" or dependent.get("status") not in {"PLANNED", "READY", "BLOCKED"}:
            continue
        if dependent.get("status") == "BLOCKED" and dependent.get("block_reason") != "dependency_failed":
            continue
        if any(require_task(plan, dependency).get("status") != "INTEGRATED" for dependency in dependent.get("dependencies", [])):
            continue
        for dependency in dependent.get("dependencies", []):
            dependency_revision = require_task(plan, dependency).get("integration_revision")
            if not isinstance(dependency_revision, str) or not dependency_revision.strip():
                raise DevFarmError("integrated code dependency has no integration revision")
            try:
                git(root, "merge-base", "--is-ancestor", dependency_revision, baseline_revision)
            except DevFarmError as exc:
                raise DevFarmError("dependent task baseline does not contain every integrated dependency") from exc
        old_relative = dependent.get("manifest_path")
        if not isinstance(old_relative, str):
            raise DevFarmError(f"dependent worker task has no manifest path: {dependent['task_id']}")
        old_path = repository_path(root, old_relative, required_parent=".devfarm/tasks")
        old_manifest = validate_manifest(read_json(old_path))
        if old_manifest["base_revision"] == baseline_revision:
            continue
        new_name = f"{old_path.stem}.base-{baseline_revision[:12]}{old_path.suffix}"
        new_path = old_path.with_name(new_name)
        new_manifest = dict(old_manifest)
        new_manifest["base_revision"] = baseline_revision
        normalized = validate_manifest(new_manifest)
        if new_path.exists():
            if validate_manifest(read_json(new_path))["base_revision"] != baseline_revision:
                raise DevFarmError(f"dependent manifest baseline path already exists with another revision: {new_name}")
        else:
            write_immutable_text(new_path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
        new_relative = new_path.relative_to(root).as_posix()
        history = list(dependent.get("manifest_history", []))
        if old_relative not in history:
            history.append(old_relative)
        if new_relative not in history:
            history.append(new_relative)
        dependent["manifest_history"] = history
        dependent["manifest_path"] = new_relative


def integrate_worker(
    root: str | Path,
    run_id: str,
    task_id: str,
    *,
    note: str,
    target_ref: str,
    integration_revision: str,
    source_attempt_id: str,
    verified_patch_digest: str,
) -> dict[str, Any]:
    """Record one verified Worker patch in an already-created Git commit."""

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = store.load(run_id)
    task = require_task(plan, task_id)
    note = _bounded_text(note, "integration note", maximum=2000)
    target_ref = _bounded_text(target_ref, "target_ref", maximum=200)
    integration_revision = _bounded_text(integration_revision, "integration_revision", maximum=200)
    source_attempt_id = _bounded_text(source_attempt_id, "source_attempt_id", maximum=200)
    verified_patch_digest = _bounded_text(verified_patch_digest, "verified_patch_digest", maximum=64).lower()
    if _SAFE_DIGEST.fullmatch(verified_patch_digest) is None:
        raise DevFarmError("verified_patch_digest must be a SHA-256 hex digest")
    target_commit = resolved_revision(root_path, target_ref)
    integration_commit = resolved_revision(root_path, integration_revision)
    try:
        git(root_path, "merge-base", "--is-ancestor", integration_commit, target_commit)
    except DevFarmError as exc:
        raise DevFarmError("integration revision is not contained in target_ref") from exc
    if task["owner"] == "worker":
        if task["status"] != "HOST_VERIFIED":
            raise DevFarmError("worker task requires host verification before integration")
        patch, manifest, expected_attempt_id = verified_worker_patch(root_path, task)
        if source_attempt_id != expected_attempt_id:
            raise DevFarmError("source_attempt_id does not match the latest verified attempt")
        digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if verified_patch_digest != digest:
            raise DevFarmError("verified_patch_digest does not match the Host Verification artifact")
        changed_files = validate_patch(patch, manifest=manifest)
        _prove_worker_patch_in_revision(root_path, patch, integration_commit, changed_files)
    elif task["owner"] == "codex":
        if task["status"] != "READY":
            raise DevFarmError("Codex task must be READY before integration marking")
        if git_diff_digest(root_path, integration_commit) != verified_patch_digest:
            raise DevFarmError("Codex integration digest does not match the integration revision")
    else:
        raise DevFarmError("unsupported plan task owner")
    task["status"] = "INTEGRATED"
    task["integration_note"] = note
    task["target_ref"] = target_ref
    task["integration_revision"] = integration_commit
    task["source_attempt_id"] = source_attempt_id
    task["verified_patch_digest"] = verified_patch_digest
    _advance_dependent_manifest_baselines(root_path, plan, task_id, target_commit)
    record_result(plan, task_id, "integration", "integrated", task.get("result_ref"), attempt_id=source_attempt_id)
    return store.save(refresh_plan(plan))


def integrate_approved_worker(
    root: str | Path,
    run_id: str,
    task_id: str,
    *,
    decision_id: str,
    commit_message: str,
    target_checkout: str | Path,
    target_ref: str,
) -> dict[str, Any]:
    """Apply one durable approval and commit the exact verified Worker patch.

    This is the Host integration operation.  The caller supplies only a
    durable decision identity and checkout target; provider, approval, and
    verification semantics remain owned by their existing boundaries.
    """

    root_path = Path(root).resolve()
    target = Path(target_checkout).resolve()
    if not target.is_dir():
        raise DevFarmError("integration target checkout does not exist")
    normalized_commit_message = _bounded_text(commit_message, "integration commit_message", maximum=200)
    normalized_target_ref = _bounded_text(target_ref, "integration target_ref", maximum=256)
    plan = CommanderPlanStore(root_path).load(run_id)
    task = require_task(plan, task_id)
    if task.get("status") != "HOST_VERIFIED":
        raise DevFarmError("integration requires a HOST_VERIFIED worker task")
    attempt_id = task.get("last_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DevFarmError("integration requires a verified attempt_id")
    normalized_decision_id = _bounded_text(decision_id, "decision_id", maximum=128)
    try:
        require_approved_review_decision(
            plan,
            decision_id=normalized_decision_id,
            task_id=task_id,
            attempt_id=attempt_id,
        )
    except DevFarmError as exc:
        raise DevFarmError(f"durable approval decision is missing: {exc}") from exc
    patch, manifest, verified_attempt_id = verified_worker_patch(root_path, task)
    changed_files = validate_patch(patch, manifest=manifest)
    patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    recorded_digest = task.get("verified_patch_digest")
    if recorded_digest is not None and recorded_digest != patch_digest:
        raise DevFarmError("verified patch digest does not match the task record")
    if _git_status(target):
        raise DevFarmError("integration target checkout must be clean")
    target_revision = _git_output(target, "rev-parse", normalized_target_ref)
    try:
        _git_output(target, "merge-base", "--is-ancestor", manifest["base_revision"], target_revision)
    except DevFarmError as exc:
        raise DevFarmError("integration target does not contain the worker base revision") from exc
    checked = _git_process(target, "apply", "--check", "--whitespace=error", "-", input_text=patch)
    if checked.returncode != 0:
        raise DevFarmError(checked.stderr.strip() or checked.stdout.strip() or "verified patch does not apply")
    applied = _git_process(target, "apply", "--whitespace=error", "-", input_text=patch)
    if applied.returncode != 0:
        raise DevFarmError(applied.stderr.strip() or applied.stdout.strip() or "verified patch application failed")
    _git_output(target, "add", "--", *changed_files)
    _git_output(target, "diff", "--cached", "--check")
    _git_output(target, "commit", "-m", normalized_commit_message)
    integration_revision = _git_output(target, "rev-parse", "HEAD")
    return integrate_worker(
        root_path,
        run_id,
        task_id,
        note=f"approved by review decision {normalized_decision_id}",
        target_ref=normalized_target_ref,
        integration_revision=integration_revision,
        source_attempt_id=verified_attempt_id,
        verified_patch_digest=patch_digest,
    )


__all__ = ["integrate_approved_worker", "integrate_worker", "verified_worker_patch"]
