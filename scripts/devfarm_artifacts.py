"""Public development artifact primitives shared by DevFarm boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from scripts.devfarm import DevFarmError, validate_manifest, validate_result
from scripts.devfarm_repository import read_json
from src.dev_agent.security.audit import AuditRecorder


MAX_TEST_OUTPUT_CHARS = 32 * 1024


def artifact_reference(value: str, *, kind: str = "artifact") -> dict[str, str]:
    """Build a bounded non-secret artifact reference for a control message."""

    if not isinstance(value, str) or not value.strip():
        raise DevFarmError("artifact reference must be non-empty")
    return {"kind": kind, "path": value.strip()}


def bounded_test_output(value: str) -> dict[str, Any]:
    """Return bounded, redacted Host-test output for durable evidence."""

    if not isinstance(value, str):
        raise ValueError("test output must be text")
    original_chars = len(value)
    clipped = value[:MAX_TEST_OUTPUT_CHARS]
    sanitized = AuditRecorder.sanitize_payload({"text": clipped})["text"]
    result: dict[str, Any] = {"text": sanitized, "truncated": original_chars > MAX_TEST_OUTPUT_CHARS}
    if original_chars > MAX_TEST_OUTPUT_CHARS:
        result["original_chars"] = original_chars
    return result


def attempt_id(value: Any = None) -> str:
    """Validate or create the bounded identity of one immutable attempt."""

    if value is None:
        return uuid4().hex
    if not isinstance(value, str) or not value.strip() or not value.replace("-", "").replace("_", "").isalnum():
        raise DevFarmError("attempt_id must contain only safe identifier characters")
    return value.strip()


def result_directories(root: Path, task_id: str, attempt: str) -> tuple[Path, Path]:
    """Return the result projection and immutable attempt directories."""

    base = root / ".devfarm" / "results" / task_id
    attempt_path = base / "attempts" / attempt_id(attempt)
    base.mkdir(parents=True, exist_ok=True)
    attempt_path.mkdir(parents=True, exist_ok=True)
    return base, attempt_path


def write_immutable_text(path: Path, content: str) -> None:
    """Create an artifact once, refusing all later rewrites."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise DevFarmError(f"immutable worker artifact already exists: {path.name}") from exc
        except OSError as exc:
            raise DevFarmError(f"immutable worker artifact link failed: {path.name}: {exc}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_verification_record(
    root: Path,
    manifest: Mapping[str, Any],
    attempt: str,
    record: Mapping[str, Any],
) -> str:
    """Append one immutable verification record for an attempt."""

    _base, attempt_path = result_directories(root, manifest["task_id"], attempt)
    verification_id = uuid4().hex
    record_with_id = dict(record)
    record_with_id["verification_id"] = verification_id
    payload = json.dumps(record_with_id, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    verification_dir = attempt_path / "verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    write_immutable_text(verification_dir / f"{verification_id}.json", payload)
    return verification_id


def list_verification_records(root: Path, task_id: str, attempt: str) -> list[dict[str, Any]]:
    """Read immutable verification records in deterministic order."""

    safe_attempt = attempt_id(attempt)
    verification_dir = root / ".devfarm" / "results" / task_id / "attempts" / safe_attempt / "verification"
    if not verification_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(verification_dir.iterdir()):
        if path.suffix == ".json" and path.stem and not path.stem.startswith("."):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
    records.sort(key=lambda record: (record.get("verified_at") or "", record.get("verification_id") or ""))
    return records


def write_latest_result_projection(root: Path, result: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> Path:
    """Write the replaceable latest result projection, never attempt evidence."""

    normalized_manifest = validate_manifest(manifest)
    normalized = validate_result(result, manifest=normalized_manifest)
    directory = root / ".devfarm" / "results" / normalized_manifest["task_id"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "result.json"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def read_latest_result_projection(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Read and validate one mutable latest-result projection."""

    path = Path(root) / ".devfarm" / "results" / manifest["task_id"] / "result.json"
    try:
        value = read_json(path)
    except DevFarmError as exc:
        raise DevFarmError(f"worker result artifact cannot be read: {exc}") from exc
    return validate_result(value, manifest=manifest)


__all__ = [
    "MAX_TEST_OUTPUT_CHARS",
    "artifact_reference",
    "attempt_id",
    "bounded_test_output",
    "list_verification_records",
    "read_latest_result_projection",
    "result_directories",
    "write_immutable_text",
    "write_latest_result_projection",
    "write_verification_record",
]
