"""Repository paths whose ownership is reserved for trusted authorities.

The development Worker, Commander, and external AgentBackend boundaries all
need the same protected-path decision.  Keeping the policy here avoids a
slightly different allowlist in each caller.  This is a path policy only; it
does not grant access to the protected responsibility.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


# These files contain authority or deployment policy.  They are protected even
# when a caller does not also list them in ``forbidden_files``.
PROTECTED_AUTHORITY_PATHS = frozenset(
    {
        "spec/v2/GATE_STATUS.json",
        "src/dev_agent/resources/budget.py",
        "src/dev_agent/resources/budget_store.py",
        "src/dev_agent/resources/control.py",
        "config/v2.yaml",
    }
)

# Directory ownership is protected as a responsibility, not just by the
# current file names.  In particular, security and recovery policy must not be
# delegated to a development Worker.
PROTECTED_DIRECTORY_PREFIXES = frozenset(
    {
        "recovery",
        "src/dev_agent/security",
        ".devfarm",
    }
)

PROTECTED_PART_NAMES = frozenset(
    {
        "credential",
        "credentials",
        "secret",
        "secrets",
        "private",
        "password",
        "token",
        "tokens",
        "private_key",
    }
)


def _normalized_parts(path: Any) -> tuple[str, ...] | None:
    if not isinstance(path, (str, PurePosixPath)):
        path = str(path)
    normalized = str(path).replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        # Callers validate safe relative paths separately.  Treating an
        # invalid path as protected is the safe default at this boundary.
        return None
    return parsed.parts


def is_protected_path(path: Any) -> bool:
    """Return whether *path* belongs to a protected authority responsibility."""

    parts = _normalized_parts(path)
    if parts is None:
        return True
    normalized = "/".join(parts)
    if normalized in PROTECTED_AUTHORITY_PATHS:
        return True
    for prefix in PROTECTED_DIRECTORY_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return any(part == ".git" or part.startswith(".env") or part.lower() in PROTECTED_PART_NAMES for part in parts)


__all__ = [
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "is_protected_path",
]
