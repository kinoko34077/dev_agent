"""Host-side egress admission for bounded development-worker inputs.

The egress boundary is intentionally separate from Task ownership and from
provider routing.  A standing grant describes the permitted destination and
path roots; a dispatch manifest records the exact bytes inspected for one
dispatch.  Neither value contains the source content itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

from ..domain.protocol import ModelRequest
from .audit import AuditRecorder
from .protected_paths import classify_path, is_protected_path


class EgressValidationError(ValueError):
    """Raised when a standing grant or dispatch input is malformed."""


class EgressDecision(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    DENY = "DENY"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVITY = {"public": 0, "normal": 1, "internal": 2, "sensitive": 3}
_SAFE_TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".ps1",
        ".py",
        ".rst",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

MODEL_REQUEST_EGRESS_POLICY = "dev-agent-model-request-egress-v1"
MODEL_REQUEST_EGRESS_DESTINATION = "qualified-provider-pool"
MODEL_REQUEST_EGRESS_PATH = "__model_request__.json"
MODEL_REQUEST_EGRESS_MAX_BYTES = 512 * 1024


def _text(value: Any, name: str, *, max_chars: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EgressValidationError(f"{name} must be non-empty text")
    result = value.strip()
    if len(result) > max_chars or _CONTROL.search(result):
        raise EgressValidationError(f"{name} is outside its bound")
    return result


def _identifier(value: Any, name: str) -> str:
    result = _text(value, name)
    if _IDENTIFIER.fullmatch(result) is None:
        raise EgressValidationError(f"{name} is not a safe identifier")
    return result


def _path(value: Any, name: str = "path") -> str:
    result = _text(value, name, max_chars=4096).replace("\\", "/")
    parsed = PurePosixPath(result)
    windows = PureWindowsPath(result)
    if (
        parsed.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise EgressValidationError(f"{name} must be a safe relative path")
    return parsed.as_posix()


def _paths(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EgressValidationError(f"{name} must be a sequence")
    if not value:
        raise EgressValidationError(f"{name} must not be empty")
    if len(value) > 64:
        raise EgressValidationError(f"{name} contains too many paths")
    result: list[str] = []
    for index, item in enumerate(value):
        normalized = _path(item, f"{name}[{index}]")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _strings(value: Any, name: str, *, max_items: int = 64) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EgressValidationError(f"{name} must be a sequence")
    if len(value) > max_items:
        raise EgressValidationError(f"{name} contains too many items")
    result: list[str] = []
    for index, item in enumerate(value):
        normalized = _identifier(item, f"{name}[{index}]")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EgressValidationError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EgressValidationError(f"{name} must be a non-negative integer")
    return value


def _ensure_metadata_safe(value: Any, name: str) -> None:
    """Reject secret-shaped metadata without retaining or echoing its value."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise EgressValidationError(f"{name} contains a non-text key")
            normalized = key.lower().replace("-", "_")
            if normalized in AuditRecorder.SECRET_KEYS or normalized.endswith(AuditRecorder.SECRET_KEY_SUFFIXES):
                raise EgressValidationError(f"{name} contains a secret-shaped key")
            _ensure_metadata_safe(child, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _ensure_metadata_safe(child, f"{name}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in AuditRecorder.SECRET_PATTERNS):
            raise EgressValidationError(f"{name} contains a secret-shaped value")
    elif value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise EgressValidationError(f"{name} contains a non-finite number")
    else:
        raise EgressValidationError(f"{name} contains a non-JSON value")


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StandingEgressGrant:
    """A bounded standing grant for low-risk source sent to a known worker."""

    policy_id: str
    destinations: tuple[str, ...]
    allowed_roots: tuple[str, ...]
    max_sensitivity: str = "normal"
    max_files: int = 64
    max_bytes: int = 512 * 1024
    max_file_bytes: int = 64 * 1024
    content_scan_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, "policy_id"))
        object.__setattr__(self, "destinations", _strings(self.destinations, "destinations"))
        object.__setattr__(self, "allowed_roots", _paths(self.allowed_roots, "allowed_roots"))
        sensitivity = _text(self.max_sensitivity, "max_sensitivity", max_chars=16).lower()
        if sensitivity not in _SENSITIVITY:
            raise EgressValidationError("max_sensitivity is unsupported")
        object.__setattr__(self, "max_sensitivity", sensitivity)
        object.__setattr__(self, "max_files", _positive_int(self.max_files, "max_files"))
        object.__setattr__(self, "max_bytes", _positive_int(self.max_bytes, "max_bytes"))
        object.__setattr__(self, "max_file_bytes", _positive_int(self.max_file_bytes, "max_file_bytes"))
        if not isinstance(self.content_scan_required, bool):
            raise EgressValidationError("content_scan_required must be a boolean")
        if any(is_protected_path(path) for path in self.allowed_roots):
            raise EgressValidationError("allowed_roots cannot include protected paths")
        _ensure_metadata_safe(self.to_dict(), "standing grant")

    def allows_path(self, path: str) -> bool:
        normalized = _path(path)
        return any(normalized == root or normalized.startswith(root + "/") for root in self.allowed_roots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "destinations": list(self.destinations),
            "allowed_roots": list(self.allowed_roots),
            "max_sensitivity": self.max_sensitivity,
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "max_file_bytes": self.max_file_bytes,
            "content_scan_required": self.content_scan_required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StandingEgressGrant":
        if not isinstance(value, Mapping):
            raise EgressValidationError("standing grant must be an object")
        return cls(
            policy_id=value.get("policy_id"),
            destinations=value.get("destinations", ()),
            allowed_roots=value.get("allowed_roots", ()),
            max_sensitivity=value.get("max_sensitivity", "normal"),
            max_files=value.get("max_files", 64),
            max_bytes=value.get("max_bytes", 512 * 1024),
            max_file_bytes=value.get("max_file_bytes", 64 * 1024),
            content_scan_required=value.get("content_scan_required", True),
        )


@dataclass(frozen=True)
class EgressFileEntry:
    """Metadata for one inspected file; never stores its content."""

    path: str
    sha256: str
    size_bytes: int
    sensitivity: str = "normal"
    secret_scan: str = "clean"
    path_class: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path(self.path))
        expected_path_class = classify_path(self.path).value
        if self.path_class is not None:
            supplied_path_class = _text(self.path_class, "path_class", max_chars=32)
            if supplied_path_class != expected_path_class:
                raise EgressValidationError("path_class does not match the central path policy")
        object.__setattr__(self, "path_class", expected_path_class)
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256.lower()) is None:
            raise EgressValidationError("file sha256 must be a SHA-256 digest")
        object.__setattr__(self, "sha256", self.sha256.lower())
        object.__setattr__(self, "size_bytes", _nonnegative_int(self.size_bytes, "file size_bytes"))
        sensitivity = _text(self.sensitivity, "file sensitivity", max_chars=16).lower()
        if sensitivity not in _SENSITIVITY:
            raise EgressValidationError("file sensitivity is unsupported")
        object.__setattr__(self, "sensitivity", sensitivity)
        scan = _text(self.secret_scan, "secret_scan", max_chars=16).lower()
        if scan not in {"clean", "blocked", "not_run"}:
            raise EgressValidationError("secret_scan is unsupported")
        object.__setattr__(self, "secret_scan", scan)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "sensitivity": self.sensitivity,
            "secret_scan": self.secret_scan,
            "path_class": self.path_class,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EgressFileEntry":
        if not isinstance(value, Mapping):
            raise EgressValidationError("egress file entry must be an object")
        return cls(
            path=value.get("path"),
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
            sensitivity=value.get("sensitivity", "normal"),
            secret_scan=value.get("secret_scan", "clean"),
            path_class=value.get("path_class"),
        )


@dataclass(frozen=True)
class EgressManifest:
    """Per-dispatch proof of the exact host inspection decision."""

    policy_id: str
    destination: str
    task_id: str
    revision: str
    files: tuple[EgressFileEntry, ...]
    decision: EgressDecision
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, "policy_id"))
        object.__setattr__(self, "destination", _identifier(self.destination, "destination"))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "revision", _text(self.revision, "revision", max_chars=512))
        if isinstance(self.files, (str, bytes)) or not isinstance(self.files, Sequence):
            raise EgressValidationError("egress files must be a sequence")
        if len(self.files) > 64:
            raise EgressValidationError("egress files contain too many entries")
        files = tuple(self.files)
        if any(not isinstance(entry, EgressFileEntry) for entry in files):
            raise EgressValidationError("egress files contain an invalid entry")
        if len({entry.path for entry in files}) != len(files):
            raise EgressValidationError("egress files contain duplicate paths")
        object.__setattr__(self, "files", files)
        try:
            decision = self.decision if isinstance(self.decision, EgressDecision) else EgressDecision(self.decision)
        except (TypeError, ValueError) as exc:
            raise EgressValidationError("egress decision is unsupported") from exc
        object.__setattr__(self, "decision", decision)
        if isinstance(self.reasons, (str, bytes)) or not isinstance(self.reasons, Sequence):
            raise EgressValidationError("egress reasons must be a sequence")
        reasons = tuple(_identifier(reason, "egress reason") for reason in self.reasons)
        if len(reasons) > 32:
            raise EgressValidationError("egress reasons contain too many entries")
        object.__setattr__(self, "reasons", reasons)
        _ensure_metadata_safe(self.to_dict(include_digest=False), "egress manifest")

    def _digest_value(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "destination": self.destination,
            "task_id": self.task_id,
            "revision": self.revision,
            "files": [entry.to_dict() for entry in self.files],
            "decision": self.decision.value,
            "reasons": list(self.reasons),
        }

    @property
    def manifest_sha256(self) -> str:
        return _canonical_digest(self._digest_value())

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        value = self._digest_value()
        if include_digest:
            value["manifest_sha256"] = self.manifest_sha256
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EgressManifest":
        if not isinstance(value, Mapping):
            raise EgressValidationError("egress manifest must be an object")
        manifest = cls(
            policy_id=value.get("policy_id"),
            destination=value.get("destination"),
            task_id=value.get("task_id"),
            revision=value.get("revision"),
            files=tuple(EgressFileEntry.from_dict(item) for item in value.get("files", ())),
            decision=value.get("decision"),
            reasons=value.get("reasons", ()),
        )
        supplied = value.get("manifest_sha256")
        if not isinstance(supplied, str) or supplied.lower() != manifest.manifest_sha256:
            raise EgressValidationError("egress manifest digest does not match")
        return manifest


def build_egress_manifest(
    *,
    task_id: str,
    destination: str,
    revision: str,
    files: Sequence[tuple[str, bytes | bytearray]],
    grant: StandingEgressGrant,
    sensitivity_by_path: Mapping[str, str] | None = None,
) -> EgressManifest:
    """Inspect exact file bytes and return an ALLOW/REVIEW/DENY manifest."""

    if not isinstance(grant, StandingEgressGrant):
        raise EgressValidationError("grant must be a StandingEgressGrant")
    destination = _identifier(destination, "destination")
    if destination not in grant.destinations:
        raise EgressValidationError("destination is not allowed by the standing grant")
    task_id = _identifier(task_id, "task_id")
    revision = _text(revision, "revision", max_chars=512)
    if isinstance(files, (str, bytes)) or not isinstance(files, Sequence):
        raise EgressValidationError("files must be a sequence")
    if len(files) > grant.max_files:
        raise EgressValidationError("files exceed the standing grant count limit")
    if sensitivity_by_path is not None and not isinstance(sensitivity_by_path, Mapping):
        raise EgressValidationError("sensitivity_by_path must be an object")

    entries: list[EgressFileEntry] = []
    reasons: list[str] = []
    total_bytes = 0
    has_review = False
    has_deny = False
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise EgressValidationError(f"files[{index}] must contain path and bytes")
        path = _path(item[0], f"files[{index}].path")
        if path in seen:
            raise EgressValidationError(f"files contains duplicate path: {path}")
        seen.add(path)
        data = item[1]
        if not isinstance(data, (bytes, bytearray)):
            raise EgressValidationError(f"files[{index}].data must be bytes")
        payload = bytes(data)
        size = len(payload)
        total_bytes += size
        digest = hashlib.sha256(payload).hexdigest()
        sensitivity = "normal"
        if sensitivity_by_path is not None and path in sensitivity_by_path:
            sensitivity = _text(sensitivity_by_path[path], "file sensitivity", max_chars=16).lower()
            if sensitivity not in _SENSITIVITY:
                raise EgressValidationError("file sensitivity is unsupported")

        protected = is_protected_path(path)
        if protected:
            sensitivity = "sensitive"
            has_deny = True
            _add_reason(reasons, "protected_path")
        elif not grant.allows_path(path):
            has_deny = True
            _add_reason(reasons, "path_not_granted")
        if _SENSITIVITY[sensitivity] > _SENSITIVITY[grant.max_sensitivity]:
            has_deny = True
            _add_reason(reasons, "sensitivity_exceeded")
        if size > grant.max_file_bytes:
            has_deny = True
            _add_reason(reasons, "size_limit")

        scan = "not_run"
        if not protected and grant.content_scan_required:
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError:
                has_deny = True
                _add_reason(reasons, "binary_content")
            else:
                scan = "clean"
                if any(pattern.search(content) for pattern in AuditRecorder.SECRET_PATTERNS):
                    scan = "blocked"
                    has_deny = True
                    _add_reason(reasons, "secret_detected")
        entries.append(
            EgressFileEntry(
                path=path,
                sha256=digest,
                size_bytes=size,
                sensitivity=sensitivity,
                secret_scan=scan,
            )
        )
        if not protected and PurePosixPath(path).suffix.lower() not in _SAFE_TEXT_SUFFIXES:
            has_review = True
            _add_reason(reasons, "unknown_extension")

    if total_bytes > grant.max_bytes:
        has_deny = True
        _add_reason(reasons, "size_limit")
    if has_deny:
        decision = EgressDecision.DENY
    elif has_review:
        decision = EgressDecision.REVIEW
    else:
        decision = EgressDecision.ALLOW
    return EgressManifest(
        policy_id=grant.policy_id,
        destination=destination,
        task_id=task_id,
        revision=revision,
        files=tuple(entries),
        decision=decision,
        reasons=tuple(reasons),
    )


def contains_secret_candidate(value: str) -> bool:
    """Return whether text matches the existing bounded secret scanner."""

    if not isinstance(value, str):
        raise EgressValidationError("value must be text")
    return any(pattern.search(value) for pattern in AuditRecorder.SECRET_PATTERNS)


def attach_model_request_egress(
    request: ModelRequest,
    *,
    revision: str = "model-request",
) -> tuple[ModelRequest, EgressManifest]:
    """Attach a Host-owned digest for one bounded model request.

    Planner, Reviewer, and Critic requests do not have source-file manifests,
    but they still cross the same external-model boundary.  Represent the
    canonical request as one virtual, scanned payload so the Host one-shot
    dispatch contract is never bypassed.  The manifest records only hashes
    and bounded metadata; request text is not retained in the returned
    manifest or its metadata.
    """

    if not isinstance(request, ModelRequest):
        raise EgressValidationError("model request must be a ModelRequest")
    if not isinstance(revision, str) or not revision.strip():
        raise EgressValidationError("model request revision must be non-empty text")
    metadata = dict(request.metadata)
    metadata.pop("egress_manifest_sha256", None)
    metadata.pop("egress_manifest_policy", None)
    unsigned_request = replace(request, metadata=metadata)
    encoded = json.dumps(
        unsigned_request.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MODEL_REQUEST_EGRESS_MAX_BYTES:
        raise EgressValidationError("model request exceeds the egress payload limit")
    grant = StandingEgressGrant(
        policy_id=MODEL_REQUEST_EGRESS_POLICY,
        destinations=(MODEL_REQUEST_EGRESS_DESTINATION,),
        allowed_roots=(MODEL_REQUEST_EGRESS_PATH,),
        max_files=1,
        max_bytes=MODEL_REQUEST_EGRESS_MAX_BYTES,
        max_file_bytes=MODEL_REQUEST_EGRESS_MAX_BYTES,
        max_sensitivity="normal",
    )
    manifest = build_egress_manifest(
        task_id=request.task_id,
        destination=MODEL_REQUEST_EGRESS_DESTINATION,
        revision=revision,
        files=((MODEL_REQUEST_EGRESS_PATH, encoded),),
        grant=grant,
    )
    if manifest.decision is not EgressDecision.ALLOW:
        reasons = ", ".join(manifest.reasons) or "egress_policy_rejected"
        if "secret_detected" in manifest.reasons:
            raise EgressValidationError("model request egress rejected: secret candidate")
        raise EgressValidationError(f"model request egress rejected: {reasons}")
    prepared_metadata = dict(metadata)
    prepared_metadata["egress_manifest_sha256"] = manifest.manifest_sha256
    prepared_metadata["egress_manifest_policy"] = MODEL_REQUEST_EGRESS_POLICY
    return replace(request, metadata=prepared_metadata), manifest


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


__all__ = [
    "EgressDecision",
    "EgressFileEntry",
    "EgressManifest",
    "EgressValidationError",
    "StandingEgressGrant",
    "MODEL_REQUEST_EGRESS_POLICY",
    "MODEL_REQUEST_EGRESS_DESTINATION",
    "MODEL_REQUEST_EGRESS_PATH",
    "MODEL_REQUEST_EGRESS_MAX_BYTES",
    "attach_model_request_egress",
    "build_egress_manifest",
    "contains_secret_candidate",
]
