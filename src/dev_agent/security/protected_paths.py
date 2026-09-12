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
        "spec/v2/PROVIDER_CAPABILITY_MATRIX.json",
        # Provider authority — locality, endpoint, credential, qualification
        "src/dev_agent/resources/provider_authority_constants.py",
        "src/dev_agent/resources/provider_policy.py",
        "src/dev_agent/providers/factory.py",
        # Billing and budget authority
        "src/dev_agent/resources/budget.py",
        "src/dev_agent/resources/budget_store.py",
        "src/dev_agent/resources/billing_catalog.py",
        "src/dev_agent/resources/quota_policy.py",
        # Qualification and routing authority
        "src/dev_agent/resources/qualification.py",
        "src/dev_agent/resources/control.py",
        "src/dev_agent/resources/router.py",
        "src/dev_agent/resources/repair.py",
        # Approval and permission authority
        "src/dev_agent/policy/approvals.py",
        "src/dev_agent/policy/permissions.py",
        # Tool side-effect and effect guard authority
        "src/dev_agent/tools/registry.py",
        "src/dev_agent/tools/runtime.py",
        "src/dev_agent/tools/effect_guard.py",
        "src/dev_agent/state/effects_repository.py",
        # Operation and bootstrap authority
        "src/dev_agent/operation.py",
        "src/dev_agent/operation_bootstrap.py",
        # AgentBackend dispatch authority
        "src/dev_agent/backends/dispatcher.py",
        "config/v2.yaml",
        "scripts/devfarm.py",
        "scripts/devfarm_worker.py",
        "scripts/devfarm_commander.py",
        "scripts/devfarm_orchestrator.py",
        ".gitmodules",
        "AGENTS.md",
        "pytest.ini",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "Dockerfile",
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
        ".github",
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
PROTECTED_SECRET_FILENAMES = frozenset(
    {
        "credentials.json",
        "credential.json",
        "token.txt",
        "secret.txt",
        "private_key.pem",
        "service-account.json",
    }
)
PROTECTED_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})


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
    for part in parts:
        lowered = part.lower()
        if part == ".git" or lowered.startswith(".env") or lowered in PROTECTED_PART_NAMES:
            return True
        if lowered in PROTECTED_SECRET_FILENAMES or any(lowered.endswith(suffix) for suffix in PROTECTED_SECRET_SUFFIXES):
            return True
        stem = lowered.rsplit(".", 1)[0]
        if any(marker in stem for marker in ("credential", "private_key", "secret_token", "access_token", "api_key")):
            return True
    return False


__all__ = [
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "PROTECTED_SECRET_FILENAMES",
    "PROTECTED_SECRET_SUFFIXES",
    "is_protected_path",
]
