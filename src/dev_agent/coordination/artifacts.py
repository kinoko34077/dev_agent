"""Immutable, content-addressed artifacts for coordination messages."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any

from .protocol import ArtifactReference, HandoffNote
from .protocol_helpers import (
    CoordinationValidationError,
    ensure_json_safe,
    ensure_secret_free,
    validate_identifier,
    validate_text,
)


MAX_ARTIFACT_BYTES = 256 * 1024


class CoordinationArtifactStore:
    """Write-once artifact store rooted outside the source checkout by default."""

    def __init__(self, root: str | Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
        self.root = Path(root)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise CoordinationValidationError("max_bytes must be positive")
        self.max_bytes = max_bytes

    def _target(self, reference: ArtifactReference) -> Path:
        relative = Path(reference.path.replace("/", os.sep))
        target = (self.root / relative).resolve()
        root = self.root.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CoordinationValidationError("artifact path escapes the coordination root") from exc
        if target.is_symlink():
            raise CoordinationValidationError("artifact path must not be a symbolic link")
        return target

    def put_bytes(self, data: bytes | bytearray, *, kind: str, revision: str) -> ArtifactReference:
        if not isinstance(data, (bytes, bytearray)):
            raise CoordinationValidationError("artifact data must be bytes")
        payload = bytes(data)
        if len(payload) > self.max_bytes:
            raise CoordinationValidationError("artifact exceeds its size bound")
        kind = validate_identifier(kind, "kind")
        revision = validate_text(revision, "revision", max_chars=512)
        digest = hashlib.sha256(payload).hexdigest()
        relative = Path("artifacts") / kind / f"{digest}.bin"
        target = self._target(
            ArtifactReference(
                path=relative.as_posix(),
                sha256=digest,
                size_bytes=len(payload),
                kind=kind,
                revision=revision,
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if existing != payload:
                raise CoordinationValidationError("immutable artifact digest collision")
            return ArtifactReference(
                path=relative.as_posix(),
                sha256=digest,
                size_bytes=len(payload),
                kind=kind,
                revision=revision,
            )
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != payload:
                    raise CoordinationValidationError("immutable artifact digest collision")
            except OSError:
                # The final fallback remains write-once: open the target with
                # exclusive creation and verify an existing target on races.
                try:
                    with target.open("xb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    if target.read_bytes() != payload:
                        raise CoordinationValidationError("immutable artifact digest collision")
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return ArtifactReference(
            path=relative.as_posix(),
            sha256=digest,
            size_bytes=len(payload),
            kind=kind,
            revision=revision,
        )

    def put_json(self, value: Any, *, kind: str, revision: str) -> ArtifactReference:
        ensure_json_safe(value, "artifact")
        ensure_secret_free(value, "artifact")
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise CoordinationValidationError("artifact must be JSON-safe") from exc
        return self._put_json_bytes(encoded, kind=kind, revision=revision)

    def _put_json_bytes(self, encoded: bytes, *, kind: str, revision: str) -> ArtifactReference:
        if len(encoded) > self.max_bytes:
            raise CoordinationValidationError("artifact exceeds its size bound")
        kind = validate_identifier(kind, "kind")
        revision = validate_text(revision, "revision", max_chars=512)
        digest = hashlib.sha256(encoded).hexdigest()
        relative = Path("artifacts") / kind / f"{digest}.json"
        target = self._target(
            ArtifactReference(
                path=relative.as_posix(),
                sha256=digest,
                size_bytes=len(encoded),
                kind=kind,
                revision=revision,
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != encoded:
                raise CoordinationValidationError("immutable artifact digest collision")
            return ArtifactReference(relative.as_posix(), digest, len(encoded), kind, revision)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != encoded:
                    raise CoordinationValidationError("immutable artifact digest collision")
            except OSError:
                try:
                    with target.open("xb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    if target.read_bytes() != encoded:
                        raise CoordinationValidationError("immutable artifact digest collision")
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return ArtifactReference(relative.as_posix(), digest, len(encoded), kind, revision)

    def read(self, reference: ArtifactReference | Mapping[str, Any]) -> bytes:
        if not isinstance(reference, ArtifactReference):
            reference = ArtifactReference.from_dict(reference)
        target = self._target(reference)
        if not target.is_file():
            raise CoordinationValidationError("artifact does not exist")
        if reference.size_bytes > self.max_bytes:
            raise CoordinationValidationError("artifact reference exceeds the read bound")
        payload = target.read_bytes()
        if len(payload) != reference.size_bytes:
            raise CoordinationValidationError("artifact size does not match its reference")
        if hashlib.sha256(payload).hexdigest() != reference.sha256:
            raise CoordinationValidationError("artifact digest does not match its reference")
        return payload

    def read_json(self, reference: ArtifactReference | Mapping[str, Any]) -> Any:
        try:
            value = json.loads(self.read(reference))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoordinationValidationError("artifact is not valid JSON") from exc
        ensure_json_safe(value, "artifact")
        ensure_secret_free(value, "artifact")
        return value

    def put_handoff(self, note: HandoffNote) -> ArtifactReference:
        if not isinstance(note, HandoffNote):
            raise CoordinationValidationError("handoff must be a HandoffNote")
        return self._put_json_bytes(
            json.dumps(note.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            kind="handoffs",
            revision=note.revision,
        )


__all__ = ["CoordinationArtifactStore", "MAX_ARTIFACT_BYTES"]
