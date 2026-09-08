"""Independent validation for event artifact roots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


_REFERENCE = re.compile(r"^event-artifact-sha256:([0-9a-f]{64})$")


def validate_artifact_root(root: str | Path) -> tuple[bool, str]:
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        return False, f"artifact root is not a directory: {path}"
    checked = 0
    for metadata_path in sorted(path.glob("*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            reference = metadata["uri"]
            match = _REFERENCE.fullmatch(reference)
            if match is None:
                return False, f"invalid artifact reference: {metadata_path.name}"
            digest = match.group(1)
            payload_path = (path / f"{digest}.bin").resolve()
            payload_path.relative_to(path)
            if not payload_path.is_file():
                return False, f"artifact payload is missing: {digest}"
            payload = payload_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                return False, f"artifact digest mismatch: {digest}"
            if metadata.get("byte_length") != len(payload):
                return False, f"artifact byte length mismatch: {digest}"
            float(metadata["created_at"])
            float(metadata["expires_at"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return False, f"invalid artifact metadata {metadata_path.name}: {exc}"
        checked += 1
    return True, f"event artifact root is readable ({checked} artifacts)"


__all__ = ["validate_artifact_root"]
