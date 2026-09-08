"""Bounded, content-addressed storage for already-sanitized event payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any


class EventArtifactStore:
    """Store non-secret event overflow outside the append-only event record.

    The store is intentionally conservative: it rejects common secret-bearing
    values instead of trying to classify them after persistence. Callers must
    redact first; stored bytes are content-addressed and expire by metadata.
    """

    _REFERENCE = re.compile(r"^event-artifact-sha256:([0-9a-f]{64})$")
    _SECRET_PATTERNS = (
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;\"}]+"),
        re.compile(r"\b(?:sk-(?:proj-)?|AIza|ghp_|github_pat_|AKIA)[A-Za-z0-9._-]{8,}\b"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _contains_secret(cls, payload: bytes) -> bool:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return any(pattern.search(text) for pattern in cls._SECRET_PATTERNS)

    def _paths(self, reference: str) -> tuple[Path, Path]:
        match = self._REFERENCE.fullmatch(reference)
        if match is None:
            raise ValueError("invalid artifact reference")
        digest = match.group(1)
        payload_path = (self.root / f"{digest}.bin").resolve()
        metadata_path = (self.root / f"{digest}.json").resolve()
        try:
            payload_path.relative_to(self.root)
            metadata_path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact reference escapes artifact root") from exc
        return payload_path, metadata_path

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, payload: bytes, *, content_type: str, retention_seconds: int) -> dict[str, Any]:
        if not isinstance(payload, bytes):
            raise TypeError("artifact payload must be bytes")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("content_type must be non-empty")
        if isinstance(retention_seconds, bool) or not isinstance(retention_seconds, int) or retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        if self._contains_secret(payload):
            raise ValueError("artifact payload must be sanitized before storage")
        digest = hashlib.sha256(payload).hexdigest()
        reference = f"event-artifact-sha256:{digest}"
        payload_path, metadata_path = self._paths(reference)
        expires_at = time.time() + retention_seconds
        if not payload_path.exists():
            self._atomic_write(payload_path, payload)
        metadata = {
            "uri": reference,
            "content_type": content_type,
            "byte_length": len(payload),
            "created_at": time.time(),
            "expires_at": expires_at,
        }
        self._atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return metadata

    def read(self, reference: str) -> bytes:
        payload_path, metadata_path = self._paths(reference)
        if not payload_path.exists() or not metadata_path.exists():
            raise FileNotFoundError(reference)
        return payload_path.read_bytes()

    def purge(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        removed = 0
        for metadata_path in self.root.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                expires_at = float(metadata["expires_at"])
                reference = metadata["uri"]
                payload_path, _ = self._paths(reference)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if expires_at <= current:
                payload_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                removed += 1
        return removed
