"""Run one bounded development-worker request and store its handoff artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm import prepare_worktree, write_result
from scripts.devfarm_contracts import (
    VERIFICATION_TRUST_LEVELS,
    canonical_digest,
    normalize_patch_hunk_counts,
    parse_host_test_command,
    sha256_text,
    validate_manifest,
    validate_patch,
    validate_result,
)
from scripts.devfarm_errors import DevFarmError
from src.dev_agent.security.protected_paths import is_protected_path
from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.host_dispatch import HostProviderDispatch
from scripts.devfarm_worker_admission import (
    DevFarmActivationPolicy,
    DevFarmWorkerEligibility,
    validate_worker_provider as shared_validate_worker_provider,
)
from scripts.devfarm_provider_runtime import build_worker_provider
from scripts.devfarm_repository import read_json
from src.dev_agent.security.egress import (
    EgressManifest,
    contains_secret_candidate,
)
from scripts.devfarm_metrics import WorkerMetricsError, WorkerMetricsStore
from scripts.devfarm_artifacts import (
    MAX_TEST_OUTPUT_CHARS,
    attempt_id as shared_attempt_id,
    bounded_test_output as shared_bounded_test_output,
    list_verification_records as shared_list_verification_records,
    read_latest_result_projection as shared_read_latest_result_projection,
    result_directories as shared_result_directories,
    write_immutable_text as shared_write_immutable_text,
    write_latest_result_projection as shared_write_latest_result_projection,
    write_verification_record as shared_write_verification_record,
)
from scripts.devfarm_worker_prompt import build_worker_prompt
from scripts.devfarm_worker_input import (
    MAX_INPUT_FILE_BYTES as SHARED_MAX_INPUT_FILE_BYTES,
    build_worker_input_context,
)
from scripts.devfarm_worker_output import (
    build_worker_metrics,
    extract_json_object,
    normalize_model_status,
    safe_host_failure_metadata,
    safe_usage,
)
from scripts.devfarm_verification import (
    HostVerificationRunner as shared_host_verification_runner,
    target_is_independent as shared_target_is_independent,
    validate_host_test_targets as shared_validate_host_test_targets,
)


MAX_INPUT_FILE_BYTES = SHARED_MAX_INPUT_FILE_BYTES
MAX_OUTPUT_TEXT_CHARS = 32 * 1024
MAX_VERIFICATION_WALL_CLOCK_SECONDS = 10 * 60

# Local compatibility name; cross-script imports use the public path-policy
# symbol above so the architecture checker can enforce the boundary.
_is_protected = is_protected_path


_extract_json = extract_json_object


def _is_within(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _git_process(workspace: Path, *arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    # Send patch input as bytes.  Python's text-mode pipe on Windows converts
    # LF to CRLF, which makes an LF unified diff fail against a CRLF checkout.
    # Git still receives text output as decoded strings for the callers below.
    command = [
        "git",
        "-c",
        "core.whitespace=cr-at-eol",
        "-c",
        f"safe.directory={workspace.as_posix()}",
        "-C",
        workspace.as_posix(),
        *arguments,
    ]
    raw_input = input_text.encode("utf-8") if input_text is not None else None
    result = subprocess.run(command, input=raw_input, capture_output=True, text=False, check=False)
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
    )


def _git(workspace: Path, *arguments: str) -> str:
    result = _git_process(workspace, *arguments)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise DevFarmError(f"worker worktree Git validation failed: {detail}")
    return result.stdout.strip()


def _git_bytes(workspace: Path, *arguments: str) -> bytes:
    command = [
        "git",
        "-c",
        f"safe.directory={workspace.as_posix()}",
        "-C",
        workspace.as_posix(),
        *arguments,
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip() or "unknown Git error"
        raise DevFarmError(f"worker Git object read failed: {detail}")
    return result.stdout


def _validate_patch_application(workspace: Path, patch: str) -> None:
    if not patch:
        return
    checked = _git_process(
        workspace,
        "apply",
        "--check",
        "--ignore-whitespace",
        "--whitespace=error",
        "-",
        input_text=patch,
    )
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip() or "unknown patch check error"
        raise DevFarmError(f"worker patch apply check failed: {detail}")


def _workspace(root: Path, manifest: Mapping[str, Any]) -> Path:
    farm_path = root / ".devfarm"
    worktree_path = farm_path / "worktrees"
    if farm_path.is_symlink() or worktree_path.is_symlink():
        raise DevFarmError("worker farm paths cannot be symlinks")
    farm_root = farm_path.resolve()
    if not _is_within(root, farm_root):
        raise DevFarmError("worker farm resolves outside repository")
    worktree_root = worktree_path.resolve()
    if not _is_within(farm_root, worktree_root):
        raise DevFarmError("worker worktrees resolve outside .devfarm")
    candidate = worktree_root / str(manifest["task_id"])
    if candidate.is_symlink():
        raise DevFarmError("worker worktree symlinks are not allowed")
    if not candidate.is_dir():
        raise DevFarmError("worker worktree does not exist; repository-root fallback is forbidden")
    workspace = candidate.resolve()
    if not _is_within(worktree_root, workspace):
        raise DevFarmError("worker worktree resolves outside .devfarm/worktrees")
    head = _git(workspace, "rev-parse", "HEAD")
    try:
        expected = _git(workspace, "rev-parse", "--verify", f"{manifest['base_revision']}^{{commit}}")
    except DevFarmError as exc:
        raise DevFarmError("worker worktree base_revision cannot be resolved") from exc
    if head != expected:
        raise DevFarmError(f"worker worktree HEAD does not match manifest base_revision: {head} != {expected}")
    status = _git(workspace, "status", "--porcelain")
    if status:
        raise DevFarmError("worker worktree is dirty before execution")
    return workspace


def _proposal_workspace(root: Path, manifest: Mapping[str, Any]) -> Path:
    """Return the read-only proposal source without requiring a worktree.

    Proposal generation may inspect only the operator-scoped files at the
    exact manifest revision.  It does not depend on the current checkout, so
    Codex may commit or leave unrelated working-tree changes while an older
    proposal remains in flight.  Git worktrees are deliberately not created
    here; they belong to the host-verification stage.
    """

    root = root.resolve()
    try:
        top_level = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    except DevFarmError as exc:
        raise DevFarmError("proposal source is not a Git repository") from exc
    if top_level != root:
        raise DevFarmError("proposal source must be the repository root")
    try:
        expected = _git(root, "rev-parse", "--verify", f"{manifest['base_revision']}^{{commit}}")
    except DevFarmError as exc:
        raise DevFarmError("proposal base_revision cannot be resolved") from exc
    for relative in manifest.get("outbound_files", ()):
        read_file_at_revision(root, expected, relative)
    return root


def read_file_at_revision(root: str | Path, revision: str, relative: str) -> bytes:
    """Read one UTF-8-independent blob from a validated Git commit object.

    The caller performs the content-size and UTF-8 checks.  This function only
    resolves a commit, verifies that the exact path is a regular blob (not a
    symlink, submodule, or directory), and returns the bytes stored in that
    object.  It never reads the checkout's working-tree file.
    """

    repository = Path(root).resolve()
    if not isinstance(relative, str) or not relative.strip():
        raise DevFarmError("Git object path must be a non-empty string")
    normalized = relative.strip().replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise DevFarmError(f"Git object path must be a safe relative path: {relative}")
    normalized = parsed.as_posix()
    if _is_protected(normalized):
        raise DevFarmError(f"protected worker input cannot be sent: {normalized}")
    try:
        resolved = _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")
    except DevFarmError as exc:
        raise DevFarmError(f"proposal base_revision cannot be resolved: {revision}") from exc
    tree = _git(repository, "ls-tree", "-r", "-z", resolved, "--", normalized)
    entry = next((item for item in tree.split("\0") if item.endswith(f"\t{normalized}")), None)
    if entry is None:
        raise DevFarmError(f"worker input file does not exist at base_revision: {normalized}")
    header, path = entry.split("\t", 1)
    fields = header.split()
    if path != normalized or len(fields) != 3 or fields[1] != "blob":
        raise DevFarmError(f"worker input at base_revision is not a regular file: {normalized}")
    if fields[0] == "120000":
        raise DevFarmError(f"worker input symlink at base_revision is not allowed: {normalized}")
    return _git_bytes(repository, "show", f"{resolved}:{normalized}")


def _verification_workspace(root: Path, manifest: Mapping[str, Any]) -> Path:
    """Load or create the isolated worktree used by host verification."""

    farm_path = root / ".devfarm"
    worktree_path = farm_path / "worktrees"
    if farm_path.is_symlink() or worktree_path.is_symlink():
        raise DevFarmError("worker farm paths cannot be symlinks")
    farm_root = farm_path.resolve()
    if not _is_within(root, farm_root):
        raise DevFarmError("worker farm resolves outside repository")
    worktree_root = worktree_path.resolve()
    if not _is_within(farm_root, worktree_root):
        raise DevFarmError("worker worktrees resolve outside .devfarm")
    candidate = worktree_root / str(manifest["task_id"])
    if candidate.is_symlink():
        raise DevFarmError("worker worktree symlinks are not allowed")
    if not candidate.exists():
        branch = f"agent/devfarm/{manifest['task_id']}"
        try:
            prepare_worktree(
                root,
                task_id=str(manifest["task_id"]),
                branch=branch,
                revision=str(manifest["base_revision"]),
            )
        except (DevFarmError, FileExistsError, OSError, RuntimeError) as exc:
            raise DevFarmError(f"worker verification worktree could not be created: {exc}") from exc
    return _workspace(root, manifest)


def _resolve_input_file(workspace: Path, relative: str) -> Path:
    if _is_protected(relative):
        raise DevFarmError(f"protected worker input cannot be sent: {relative}")
    current = workspace
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise DevFarmError(f"worker input symlink is not allowed: {relative}")
    resolved = (workspace / relative).resolve()
    if not _is_within(workspace, resolved):
        raise DevFarmError(f"worker input resolves outside worktree: {relative}")
    if not resolved.is_file():
        raise DevFarmError(f"worker input file cannot be read: {relative}")
    return resolved


def _contains_secret(value: str) -> bool:
    return contains_secret_candidate(value)


_canonical_digest = canonical_digest
_sha256_text = sha256_text


# Keep the historical private names for in-process callers and old fixtures,
# while making the shared public modules the runtime source of truth.
HostVerificationRunner = shared_host_verification_runner
_validate_host_test_targets = shared_validate_host_test_targets
_target_is_independent = shared_target_is_independent
_bounded_test_output = shared_bounded_test_output
_attempt_id = shared_attempt_id
_result_directories = shared_result_directories
_write_immutable_text = shared_write_immutable_text
_write_verification_record = shared_write_verification_record
_list_verification_records = shared_list_verification_records
_write_latest_result_projection = shared_write_latest_result_projection


def _input_context_with_manifest(
    workspace: Path,
    manifest: Mapping[str, Any],
    *,
    destination: str | None = None,
) -> tuple[str, EgressManifest]:
    manifest = validate_manifest(manifest)
    return build_worker_input_context(
        manifest,
        read_file_at_revision=lambda revision, relative: read_file_at_revision(
            workspace,
            revision,
            relative,
        ),
        destination=destination,
    )


def _input_context(workspace: Path, manifest: Mapping[str, Any]) -> str:
    """Keep the historical context-only helper while using host egress checks."""

    context, _egress_manifest = _input_context_with_manifest(workspace, manifest)
    return context


def _prompt(
    manifest: Mapping[str, Any],
    inputs: str,
    *,
    egress_manifest: EgressManifest | None = None,
) -> str:
    """Backward-compatible Worker prompt entrypoint."""

    return build_worker_prompt(manifest, inputs, egress_manifest=egress_manifest)


_provider = build_worker_provider


def inspect_worker_egress(
    root: str | Path,
    manifest_path: str | Path,
    *,
    provider_id: str,
    model: str,
    provider_binding_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Return a bounded, read-only dispatch preflight without contacting a provider.

    The exact Git-revision inputs are read and scanned so the operator can
    verify destination, binding, and digests before a live Host dispatch.  No
    prompt, source content, credential, endpoint, or ModelRequest is emitted,
    and provider construction is used only for local activation/identity
    checks.
    """

    root_path = Path(root).resolve()
    manifest = validate_manifest(read_json(Path(manifest_path)))
    if not manifest["external_provider_allowed"]:
        raise DevFarmError("external provider execution is not approved by manifest")
    if provider_id not in manifest["approved_provider_ids"]:
        raise DevFarmError(f"provider is not approved by manifest: {provider_id}")
    provider = _provider(provider_id, model, timeout_seconds, provider_binding_id)
    workspace = _proposal_workspace(root_path, manifest)
    _context, egress_manifest = _input_context_with_manifest(
        workspace,
        manifest,
        destination=provider_id,
    )
    return {
        "status": "ready",
        "execution_boundary": "host_process",
        "network_requested": False,
        "source_content_emitted": False,
        "task_id": manifest["task_id"],
        "base_revision": manifest["base_revision"],
        "provider_id": getattr(provider, "provider_id", provider_id),
        "provider_binding_id": getattr(provider, "provider_binding_id", None) or provider_id,
        "model_id": getattr(provider, "model", model),
        "intelligence_tier": getattr(provider, "intelligence_tier", None),
        "egress_manifest": egress_manifest.to_dict(),
    }


def _write_auxiliary_artifacts(
    root: Path,
    task_id: str,
    output: Mapping[str, Any],
    *,
    worker_metrics: Mapping[str, Any] | None = None,
    attempt_id: str | None = None,
    egress_manifest: EgressManifest | None = None,
) -> None:
    selected_attempt = _attempt_id(attempt_id or output.get("attempt_id") or "legacy")
    directories = _result_directories(root, task_id, selected_attempt)
    patch = output.get("patch", "")
    if not isinstance(patch, str):
        raise DevFarmError("worker patch must be a string")
    if len(patch) > MAX_OUTPUT_TEXT_CHARS:
        raise DevFarmError(f"worker patch exceeds {MAX_OUTPUT_TEXT_CHARS} characters")
    proposed = output.get("tests_run", [])
    if not isinstance(proposed, list):
        raise DevFarmError("worker proposed tests must be a list")
    tests = {
        "proposed_test_commands": proposed,
        "model_claims": {
            "tests_run": proposed,
            "tests_passed": output.get("tests_passed"),
        },
        "host_verified_tests": [],
        "worker_metrics": dict(worker_metrics or {}),
    }
    notes = output.get("notes", "")
    if not isinstance(notes, str):
        notes = (
            f"[MODEL_NOTES_NORMALIZED type={type(notes).__name__}]\n"
            f"{json.dumps(notes, ensure_ascii=False, sort_keys=True)}"
        )
    if len(notes) > MAX_OUTPUT_TEXT_CHARS:
        notes = notes[:MAX_OUTPUT_TEXT_CHARS] + f"\n...[TRUNCATED original_chars={len(notes)}]"
    root_directory, attempt_directory = directories
    # The root files are explicitly latest projections.  Only the attempt
    # directory is immutable evidence.
    root_directory.joinpath("patch.diff").write_text(patch, encoding="utf-8")
    root_directory.joinpath("tests.json").write_text(json.dumps(tests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    root_directory.joinpath("notes.md").write_text(notes + "\n", encoding="utf-8")
    _write_immutable_text(attempt_directory / "patch.diff", patch)
    _write_immutable_text(attempt_directory / "tests.json", json.dumps(tests, ensure_ascii=False, indent=2) + "\n")
    _write_immutable_text(attempt_directory / "notes.md", notes + "\n")
    if egress_manifest is not None:
        if not isinstance(egress_manifest, EgressManifest):
            raise DevFarmError("egress_manifest must be an EgressManifest")
        egress_payload = json.dumps(
            egress_manifest.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        root_directory.joinpath("egress-manifest.json").write_text(egress_payload, encoding="utf-8")
        _write_immutable_text(attempt_directory / "egress-manifest.json", egress_payload)


def _record_failed_model_output(
    root: Path,
    manifest: Mapping[str, Any],
    reason: str,
    *,
    worker_metrics: Mapping[str, Any] | None = None,
    attempt_id: str | None = None,
    egress_manifest: EgressManifest | None = None,
) -> dict[str, Any]:
    selected_attempt = _attempt_id(attempt_id)
    result = {
        "status": "failed",
        "attempt_id": selected_attempt,
        "base_revision": manifest["base_revision"],
        "changed_files": [],
        "tests_run": [],
        "tests_passed": False,
        "model_claims": {},
        "proposed_test_commands": [],
        "host_verified_tests": [],
        "worker_metrics": dict(worker_metrics or {}),
        "known_issues": [reason],
        "assumptions": ["The model proposal failed deterministic validation; no patch was accepted."],
    }
    write_result(root, result, manifest=manifest)
    _write_auxiliary_artifacts(
        root,
        manifest["task_id"],
        {**result, "patch": "", "notes": reason},
        worker_metrics=result["worker_metrics"],
        attempt_id=selected_attempt,
        egress_manifest=egress_manifest,
    )
    return result


_normalize_model_status = normalize_model_status


def _read_result_artifact(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    return shared_read_latest_result_projection(root, manifest)


def _attempt_artifact_path(
    root: Path,
    task_id: str,
    attempt_id: str,
    name: str,
    *,
    required: bool = True,
) -> Path | None:
    """Resolve an artifact within the immutable attempt directory.

    The root-level files are only a latest-result projection.  Once a result
    identifies an attempt, verification must read the matching attempt
    artifacts so a later retry or a stale projection cannot change what is
    applied to the worker worktree.
    """

    safe_attempt = _attempt_id(attempt_id)
    if name not in {"patch.diff", "notes.md"}:
        raise DevFarmError("unsupported worker attempt artifact")
    attempt_path = root / ".devfarm" / "results" / task_id / "attempts" / safe_attempt / name
    if attempt_path.is_file():
        return attempt_path
    if safe_attempt != "legacy":
        if not required:
            return None
        raise DevFarmError(f"worker {name} artifact is missing for attempt: {safe_attempt}")
    legacy_path = root / ".devfarm" / "results" / task_id / name
    if required or legacy_path.is_file():
        return legacy_path
    return None


def apply_and_verify(
    root: str | Path,
    manifest_path: str | Path,
    *,
    trust_level: str = "STATIC_ONLY",
    operator_approved: bool = False,
) -> dict[str, Any]:
    """Apply a proposal and optionally run host tests behind an explicit gate.

    External-provider manifests default to STATIC_ONLY.  A caller must make
    the attempt-scoped operator decision explicit before TRUSTED_HOST_EXEC is
    allowed; OS_SANDBOXED is intentionally unavailable until a real OS
    sandbox implementation is supplied.
    """

    if trust_level not in VERIFICATION_TRUST_LEVELS:
        raise DevFarmError(f"unsupported verification trust level: {trust_level}")

    root = Path(root).resolve()
    manifest = validate_manifest(read_json(Path(manifest_path)))
    result = _read_result_artifact(root, manifest)
    attempt_id = _attempt_id(result.get("attempt_id") or "legacy")
    if trust_level == "OS_SANDBOXED":
        raise DevFarmError("OS_SANDBOXED verification is not available on this host")
    if manifest["external_provider_allowed"]:
        if trust_level == "TRUSTED_HOST_EXEC" and operator_approved is not True:
            raise DevFarmError("TRUSTED_HOST_EXEC requires explicit operator approval for this attempt")
    elif trust_level == "TRUSTED_HOST_EXEC" and operator_approved is not True:
        raise DevFarmError("TRUSTED_HOST_EXEC requires explicit operator approval")
    if result["status"] != "completed" or not result["changed_files"]:
        raise DevFarmError("only a completed worker proposal with a non-empty patch may be applied")
    patch_path = _attempt_artifact_path(root, manifest["task_id"], attempt_id, "patch.diff")
    try:
        patch = patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DevFarmError(f"worker patch artifact cannot be read: {exc}") from exc
    actual_changed_files = validate_patch(patch, manifest=manifest)
    if actual_changed_files != result["changed_files"]:
        raise DevFarmError("result changed_files does not match the patch artifact")
    workspace = _verification_workspace(root, manifest)
    if patch:
        _validate_patch_application(workspace, patch)
        applied = _git_process(
            workspace,
            "apply",
            "--ignore-whitespace",
            "--whitespace=error",
            "-",
            input_text=patch,
        )
        if applied.returncode != 0:
            detail = applied.stderr.strip() or applied.stdout.strip() or "unknown patch apply error"
            raise DevFarmError(f"worker patch apply failed: {detail}")

    verified: list[dict[str, Any]] = []
    independent_verification = False
    if trust_level == "TRUSTED_HOST_EXEC":
        verification_runner = HostVerificationRunner(timeout_seconds=120)
        verification_started = time.monotonic()
        changed_set = frozenset(actual_changed_files)
        for command in manifest["test_commands"]:
            if time.monotonic() - verification_started >= MAX_VERIFICATION_WALL_CLOCK_SECONDS:
                raise DevFarmError("worker verification exceeded total wall-clock budget")
            tokens = parse_host_test_command(command)
            _validate_host_test_targets(workspace, tokens)
            targets = {
                token.split("::", 1)[0].replace("\\", "/")
                for token in tokens[3:]
                if token not in {"-q", "-x"} and not token.startswith("--maxfail=")
            }
            if any(_target_is_independent(workspace, t, changed_set) for t in targets):
                independent_verification = True
            host_result = verification_runner.run(tokens, cwd=workspace)
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

    tests_passed = bool(verified) and all(item["passed"] for item in verified)
    result["tests_run"] = list(manifest["test_commands"])
    result["tests_passed"] = tests_passed
    result["host_verified_tests"] = verified
    result["status"] = "completed" if (tests_passed or trust_level == "STATIC_ONLY") else "failed"
    metrics = dict(result.get("worker_metrics", {}))
    metrics.update(
        {
            "host_verified": bool(verified),
            "host_verified_test_count": len(verified),
            "host_tests_passed": tests_passed,
            "independent_verification": independent_verification,
            "result_accepted": result["status"] == "completed" and tests_passed and independent_verification and (trust_level == "OS_SANDBOXED" or (trust_level == "TRUSTED_HOST_EXEC" and operator_approved is True)),
            "verification_trust_level": trust_level,
            "operator_approved": operator_approved is True,
        }
    )
    result["worker_metrics"] = metrics
    result["attempt_id"] = attempt_id
    issues = list(result["known_issues"])
    if not tests_passed:
        issues.append("host verification did not pass")
    if trust_level == "STATIC_ONLY":
        issues.append("STATIC_ONLY: patched code was not executed on the host")
    elif not independent_verification:
        issues.append("host verification did not include an unmodified trusted target")
    result["known_issues"] = issues
    try:
        with WorkerMetricsStore(root / ".devfarm" / "metrics.sqlite3") as metrics_store:
            metrics_store.record(manifest=manifest, result=result)
    except WorkerMetricsError:
        # Metrics are host-side observability, not acceptance authority.  Keep
        # the verified result durable while making a storage failure explicit
        # instead of silently claiming that the sample was accumulated.
        metrics["durable_recorded"] = False
        metrics["durable_record_error"] = "metrics_storage_rejected"
    else:
        metrics["durable_recorded"] = True
    result["worker_metrics"] = metrics
    notes_path = _attempt_artifact_path(
        root,
        manifest["task_id"],
        attempt_id,
        "notes.md",
        required=False,
    )
    notes = notes_path.read_text(encoding="utf-8") if notes_path is not None else "Host verification completed; no model notes artifact was available."
    if notes_path is None:
        (root / ".devfarm" / "results" / manifest["task_id"] / "notes.md").write_text(notes + "\n", encoding="utf-8")
    verification_record = {
        "attempt_id": attempt_id,
        "patch_sha256": _sha256_text(patch),
        "manifest_sha256": _canonical_digest(manifest),
        "base_revision": manifest["base_revision"],
        "test_spec_sha256": _canonical_digest(manifest["test_commands"]),
        "containment_level": trust_level,
        "verified_tests": verified,
        "independent_verification": independent_verification,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "operator_approved": operator_approved is True,
    }
    verification_id = _write_verification_record(root, manifest, attempt_id, verification_record)
    result["verification_id"] = verification_id
    _write_latest_result_projection(root, result, manifest=manifest)
    return result


def _safe_usage(usage: Any) -> dict[str, Any]:
    """Backward-compatible alias for the shared bounded usage projection."""

    return safe_usage(usage)


def _worker_metrics(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    response: Any = None,
    elapsed_ms: int = 0,
    task_type: str = "unspecified",
) -> dict[str, Any]:
    """Backward-compatible alias for the shared Worker metric projection."""

    return build_worker_metrics(
        provider,
        request,
        response=response,
        elapsed_ms=elapsed_ms,
        task_type=task_type,
    )


def _request_task_id(manifest: Mapping[str, Any]) -> str:
    """Map a human-readable manifest id to the protocol's stable UUID task id."""

    return str(uuid5(NAMESPACE_URL, f"dev_agent.devfarm/{manifest['task_id']}"))


def _validate_worker_provider(provider: ModelProvider) -> tuple[str, str, str, str | None, DevFarmWorkerEligibility]:
    """Backward-compatible alias for Host-owned admission validation."""

    return shared_validate_worker_provider(provider)


def run_worker(
    root: str | Path,
    manifest_path: str | Path,
    *,
    provider: ModelProvider,
    host_dispatch: HostProviderDispatch | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = validate_manifest(read_json(Path(manifest_path)))
    attempt_id = _attempt_id()
    if not manifest["external_provider_allowed"]:
        raise DevFarmError("external provider execution is not approved by manifest")
    provider_id, model_id, _binding_id, _tier, _eligibility = _validate_worker_provider(provider)
    worker_tier = _tier or _eligibility.intelligence_tier
    if not isinstance(worker_tier, str) or not worker_tier.strip():
        raise DevFarmError("worker provider has no Host-admitted intelligence tier")
    if provider_id not in manifest["approved_provider_ids"]:
        raise DevFarmError(f"provider is not approved by manifest: {provider_id}")
    # Stage A is remote proposal only.  Do not require or create a Git
    # worktree until a valid proposal reaches apply_and_verify().
    workspace = _proposal_workspace(root, manifest)
    inputs, egress_manifest = _input_context_with_manifest(workspace, manifest, destination=provider_id)
    request = ModelRequest(
        # DevFarm task ids are intentionally readable and are validated by the
        # manifest contract.  ModelRequest has a stricter UUID task identity;
        # keep both identities without weakening either contract.
        task_id=_request_task_id(manifest),
        messages=[
            {"role": "system", "content": "Return the bounded development-worker result as JSON only."},
            {"role": "user", "content": _prompt(manifest, inputs, egress_manifest=egress_manifest)},
        ],
        metadata={
            "devfarm_task_id": manifest["task_id"],
            "egress_manifest_sha256": egress_manifest.manifest_sha256,
            "allowed_intelligence_tiers": [worker_tier],
        },
        max_output_tokens=4096,
    )
    dispatch = host_dispatch or HostProviderDispatch(provider)
    if dispatch.provider_identity["provider_id"] not in {provider_id, "resource-router"}:
        raise DevFarmError("Host dispatch provider identity does not match the admitted Worker provider")
    started = time.perf_counter()
    try:
        response = dispatch.request(request)
    except ProviderError as exc:
        metrics = _worker_metrics(provider, request, elapsed_ms=round((time.perf_counter() - started) * 1000), task_type=manifest["task_type"])
        metrics["execution_boundary"] = dispatch.execution_boundary
        # Keep the provider/Host failure category distinct from the bounded
        # transport-layer diagnostic.  A Host configuration rejection must
        # not be reported as an unexplained transport failure, while a
        # transport category is still preserved when the Host boundary
        # provides one.
        metrics["provider_failure_category"] = exc.category
        metrics["transport_failure_category"] = (
            dispatch.last_transport_category.value if dispatch.last_transport_category is not None else None
        )
        metrics.update(safe_host_failure_metadata(exc))
        status = "blocked_external" if exc.category == "authentication" else "failed"
        result = {
            "status": status,
            "attempt_id": attempt_id,
            "base_revision": manifest["base_revision"],
            "changed_files": [],
            "tests_run": [],
            "tests_passed": False,
            "model_claims": {},
            "proposed_test_commands": [],
            "host_verified_tests": [],
            "worker_metrics": metrics,
            "known_issues": [str(exc)],
            "assumptions": ["The worker provider was unavailable; no patch was produced."],
        }
        write_result(root, result, manifest=manifest)
        _write_auxiliary_artifacts(
            root,
            manifest["task_id"],
            {**result, "notes": str(exc)},
            worker_metrics=metrics,
            attempt_id=attempt_id,
            egress_manifest=egress_manifest,
        )
        return result
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    metrics = _worker_metrics(provider, request, response=response, elapsed_ms=elapsed_ms, task_type=manifest["task_type"])
    if getattr(response, "provider", None) != provider_id or getattr(response, "model", None) != model_id:
        return _record_failed_model_output(
            root,
            manifest,
            "worker response identity mismatch with admitted provider binding",
            worker_metrics=metrics,
            attempt_id=attempt_id,
            egress_manifest=egress_manifest,
        )
    text = "".join(response.text_segments)
    if len(text) > MAX_OUTPUT_TEXT_CHARS:
        return _record_failed_model_output(
            root,
            manifest,
            "worker response exceeds output limit",
            worker_metrics=metrics,
            attempt_id=attempt_id,
            egress_manifest=egress_manifest,
        )
    try:
        output = _extract_json(text)
    except DevFarmError as exc:
        return _record_failed_model_output(
            root,
            manifest,
            str(exc),
            worker_metrics=metrics,
            attempt_id=attempt_id,
            egress_manifest=egress_manifest,
        )
    try:
        patch = output.get("patch", "")
        patch_normalizations: list[str] = []
        if isinstance(patch, str) and patch and not patch.endswith("\n"):
            # JSON responses commonly omit the final line ending.  Appending
            # exactly one LF is a transport-format normalization, not a patch
            # edit; record it in host metrics and validate the normalized bytes.
            patch = patch + "\n"
            patch_normalizations.append("appended_final_newline")
        patch, hunk_normalizations = normalize_patch_hunk_counts(patch)
        patch_normalizations.extend(hunk_normalizations)
        if patch_normalizations:
            output = {**output, "patch": patch, "patch_normalizations": patch_normalizations}
        actual_changed_files = validate_patch(patch, manifest=manifest)
        status = _normalize_model_status(output.get("status"))
        if status == "completed" and not actual_changed_files:
            raise DevFarmError("completed worker proposal must include a non-empty patch")
        model_claims = {
            "status": output.get("status"),
            "changed_files": output.get("changed_files"),
            "tests_run": output.get("tests_run"),
            "tests_passed": output.get("tests_passed"),
        }
        normalizations = output.get("patch_normalizations", [])
        if normalizations:
            if not isinstance(normalizations, list) or any(not isinstance(item, str) for item in normalizations):
                raise DevFarmError("patch_normalizations must be a list of strings")
            metrics["patch_normalizations"] = list(normalizations)
        result = {
            "status": status,
            "attempt_id": attempt_id,
            "base_revision": manifest["base_revision"],
            "changed_files": actual_changed_files,
            "tests_run": [],
            "tests_passed": False,
            "model_claims": model_claims,
            "proposed_test_commands": output.get("tests_run", []),
            "host_verified_tests": [],
            "worker_metrics": metrics,
            "known_issues": output.get("known_issues"),
            "assumptions": output.get("assumptions"),
        }
        normalized = validate_result(result, manifest=manifest)
    except DevFarmError as exc:
        return _record_failed_model_output(
            root,
            manifest,
            str(exc),
            worker_metrics=metrics,
            attempt_id=attempt_id,
            egress_manifest=egress_manifest,
        )
    write_result(root, normalized, manifest=manifest)
    _write_auxiliary_artifacts(
        root,
        manifest["task_id"],
        {**output, "attempt_id": attempt_id},
        worker_metrics=metrics,
        attempt_id=attempt_id,
        egress_manifest=egress_manifest,
    )
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provider", choices=("cloudflare", "gemini", "openrouter"))
    parser.add_argument("--model")
    parser.add_argument("--provider-binding-id")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--egress-dry-run",
        action="store_true",
        help="inspect exact Host egress admission without contacting a provider",
    )
    parser.add_argument(
        "--execution-boundary",
        choices=("unclassified", "codex_sandbox", "host_process", "provider_process"),
        default="unclassified",
        help="explicit runtime evidence used only to classify transport failures",
    )
    parser.add_argument("--apply-and-verify", action="store_true")
    parser.add_argument(
        "--trust-level",
        default="STATIC_ONLY",
        choices=list(VERIFICATION_TRUST_LEVELS),
        help="verification containment level; requires --operator-approved for TRUSTED_HOST_EXEC",
    )
    parser.add_argument("--operator-approved", action="store_true", help="explicit operator approval for TRUSTED_HOST_EXEC")
    args = parser.parse_args(argv)
    try:
        if args.egress_dry_run:
            if args.apply_and_verify:
                parser.error("--egress-dry-run cannot be combined with --apply-and-verify")
            if args.provider is None or not args.model:
                parser.error("--provider and --model are required for --egress-dry-run")
            result = inspect_worker_egress(
                args.root,
                args.manifest,
                provider_id=args.provider,
                model=args.model,
                provider_binding_id=args.provider_binding_id,
                timeout_seconds=args.timeout_seconds,
            )
        elif args.apply_and_verify:
            if args.provider is not None or args.model is not None or args.provider_binding_id is not None:
                parser.error("--apply-and-verify cannot be combined with provider selection")
            result = apply_and_verify(args.root, args.manifest, trust_level=args.trust_level, operator_approved=args.operator_approved)
        else:
            if args.provider is None or not args.model:
                parser.error("--provider and --model are required unless --apply-and-verify is used")
            provider = _provider(args.provider, args.model, args.timeout_seconds, args.provider_binding_id)
            executor = None
            if args.execution_boundary == "host_process":
                from scripts.devfarm_host_dispatch import create_host_process_executor

                executor = create_host_process_executor(
                    args.root / ".devfarm" / "host-dispatch",
                    timeout_seconds=args.timeout_seconds,
                )
            result = run_worker(
                args.root,
                args.manifest,
                provider=provider,
                host_dispatch=HostProviderDispatch(
                    provider,
                    execution_boundary=args.execution_boundary,
                    executor=executor,
                ),
            )
    except (DevFarmError, ProviderError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
