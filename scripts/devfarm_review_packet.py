"""Read-only compact ReviewPacket construction for development plans.

The packet is the normal review boundary: it contains bounded identity,
changed-file, digest, verification, acceptance, and artifact references but
never raw Worker conversation or patch text.  This module does not make a
review decision and does not mutate a Commander plan.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError
from scripts.devfarm_repository import read_json
from scripts.devfarm_supervisor_protocol import normalize_review_packet


def build_review_packet(root: str | Path, task: Mapping[str, Any]) -> dict[str, Any]:
    """Build one bounded packet from Host-side artifacts."""

    root_path = Path(root).resolve()
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise DevFarmError("review packet task_id is missing")
    attempt_id = task.get("last_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DevFarmError(f"review packet requires an attempt id: {task_id}")
    result_ref = task.get("result_ref")
    if not isinstance(result_ref, str) or not result_ref.strip():
        raise DevFarmError(f"review packet requires a result reference: {task_id}")
    result_path = (root_path / result_ref).resolve()
    try:
        result_path.relative_to(root_path)
    except ValueError as exc:
        raise DevFarmError("review result reference escapes repository") from exc
    result: Mapping[str, Any] = {}
    if result_path.is_file():
        loaded = read_json(result_path)
        if not isinstance(loaded, Mapping):
            raise DevFarmError("review result artifact must be an object")
        result = loaded
    assignment = task.get("assignment", {})
    if not isinstance(assignment, Mapping):
        assignment = {}
    attempt_root = result_path.parent
    verification_id = result.get("verification_id")
    verification_record: Mapping[str, Any] | None = None
    verification_dir = attempt_root / "verification"
    if verification_dir.is_dir():
        records: list[Mapping[str, Any]] = []
        for candidate in sorted(verification_dir.glob("*.json")):
            try:
                loaded = read_json(candidate)
            except DevFarmError:
                continue
            if isinstance(loaded, Mapping):
                records.append(loaded)
        if records:
            records.sort(key=lambda item: (str(item.get("verified_at", "")), str(item.get("verification_id", ""))))
            verification_record = records[-1]
            if isinstance(verification_record.get("verification_id"), str):
                verification_id = verification_record["verification_id"]
    verification_ref = None
    if isinstance(verification_id, str) and verification_id.strip():
        verification_ref = (attempt_root / "verification" / f"{verification_id}.json").relative_to(root_path).as_posix()
    patch_path = attempt_root / "patch.diff"
    patch_ref = patch_path.relative_to(root_path).as_posix()
    patch_sha256 = task.get("verified_patch_digest")
    if patch_path.is_file():
        patch_sha256 = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    if task.get("verified_patch_digest") is not None and task["verified_patch_digest"] != patch_sha256:
        raise DevFarmError("review packet patch digest does not match the Host artifact")
    manifest_ref = task.get("manifest_path")
    manifest: Mapping[str, Any] = {}
    if isinstance(manifest_ref, str):
        manifest_path = (root_path / manifest_ref).resolve()
        try:
            manifest_path.relative_to(root_path)
            if manifest_path.is_file():
                loaded_manifest = read_json(manifest_path)
                if isinstance(loaded_manifest, Mapping):
                    manifest = loaded_manifest
        except (ValueError, DevFarmError):
            manifest = {}
    summary: dict[str, Any] = {}
    if verification_record is not None:
        verified_tests = verification_record.get("verified_tests", [])
        if not isinstance(verified_tests, list):
            verified_tests = []
        tests_passed = bool(verified_tests) and all(
            isinstance(item, Mapping) and item.get("passed") is True
            for item in verified_tests
        )
        summary = {
            "host_verified": bool(verified_tests),
            "host_verified_test_count": len(verified_tests),
            "host_tests_passed": tests_passed,
            "independent_verification": verification_record.get("independent_verification") is True,
            "result_accepted": task.get("status") == "HOST_VERIFIED",
            "verification_trust_level": verification_record.get("containment_level"),
            "operator_approved": verification_record.get("operator_approved") is True,
        }
    return normalize_review_packet(
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "status": task.get("status", result.get("status", "unknown")),
            "provider": assignment.get("provider_id"),
            "model": assignment.get("model_id"),
            "changed_files": result.get("changed_files", []),
            "patch_sha256": patch_sha256,
            "result_ref": result_ref,
            "verification_ref": verification_ref,
            "verification_summary": summary,
            "known_issues": result.get("known_issues", []),
            "acceptance": manifest.get("acceptance", []),
            "artifact_refs": [
                {"kind": "result", "path": result_ref},
                {"kind": "patch", "path": patch_ref},
                *([{"kind": "verification", "path": verification_ref}] if verification_ref else []),
            ],
            "created_at": task.get("updated_at"),
        }
    )


__all__ = ["build_review_packet"]
