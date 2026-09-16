"""Small fail-closed validators shared by process-coordination contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

from ..security.audit import AuditRecorder


class CoordinationValidationError(ValueError):
    """Raised when a coordination value is malformed or unsafe."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_SEQUENCE_ITEMS = 64
_MAX_ITEM_CHARS = 4_096


def validate_identifier(value: Any, name: str = "identifier") -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CoordinationValidationError(f"{name} must be a bounded identifier")
    return value


def validate_text(value: Any, name: str = "text", *, max_chars: int = _MAX_ITEM_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoordinationValidationError(f"{name} must be non-empty text")
    if _CONTROL.search(value) or len(value) > max_chars:
        raise CoordinationValidationError(f"{name} contains control characters or exceeds its bound")
    return value


def validate_relative_path(value: Any, name: str = "path") -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoordinationValidationError(f"{name} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if (
        pure.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.startswith("..") for part in pure.parts)
        or _CONTROL.search(normalized)
    ):
        raise CoordinationValidationError(f"{name} must stay within the coordination root")
    if len(normalized) > _MAX_ITEM_CHARS:
        raise CoordinationValidationError(f"{name} exceeds its bound")
    return pure.as_posix()


def validate_timestamp(value: Any, name: str = "timestamp") -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoordinationValidationError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CoordinationValidationError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CoordinationValidationError(f"{name} must include a timezone")
    return value


def timestamp_is_after(candidate: str, reference: str) -> bool:
    """Return whether an already validated timestamp is strictly later."""

    validate_timestamp(candidate, "candidate")
    validate_timestamp(reference, "reference")
    left = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    right = datetime.fromisoformat(reference.replace("Z", "+00:00"))
    return left > right


def validate_string_sequence(
    value: Any,
    name: str = "sequence",
    *,
    max_items: int = _MAX_SEQUENCE_ITEMS,
    item_max_chars: int = _MAX_ITEM_CHARS,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CoordinationValidationError(f"{name} must be a sequence of strings")
    if len(value) > max_items:
        raise CoordinationValidationError(f"{name} has too many items")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(validate_text(item, f"{name}[{index}]", max_chars=item_max_chars))
    return tuple(result)


def ensure_json_safe(value: Any, name: str = "value") -> Any:
    """Validate JSON shape and finite numeric values without mutating input."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CoordinationValidationError(f"{name} mapping keys must be strings")
            validate_text(key, f"{name} key", max_chars=_MAX_ITEM_CHARS)
            ensure_json_safe(child, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            ensure_json_safe(child, f"{name}[{index}]")
    elif value is None or isinstance(value, (str, bool, int)):
        pass
    elif isinstance(value, float):
        if not (value == value and value not in {float("inf"), float("-inf")}):
            raise CoordinationValidationError(f"{name} contains a non-finite number")
    else:
        raise CoordinationValidationError(f"{name} contains a non-JSON value")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CoordinationValidationError(f"{name} is not JSON-safe") from exc
    return value


def ensure_secret_free(value: Any, name: str = "value") -> Any:
    """Reject secret-shaped keys and values before durable storage or dispatch."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CoordinationValidationError(f"{name} mapping keys must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in AuditRecorder.SECRET_KEYS or normalized.endswith(AuditRecorder.SECRET_KEY_SUFFIXES):
                raise CoordinationValidationError(f"{name} contains a secret-shaped key")
            ensure_secret_free(child, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            ensure_secret_free(child, f"{name}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in AuditRecorder.SECRET_PATTERNS):
            raise CoordinationValidationError(f"{name} contains a secret-shaped value")
    return value


__all__ = [
    "CoordinationValidationError",
    "ensure_json_safe",
    "ensure_secret_free",
    "validate_identifier",
    "validate_relative_path",
    "validate_string_sequence",
    "validate_text",
    "validate_timestamp",
    "timestamp_is_after",
]
