"""Repository paths whose ownership is reserved for trusted authorities.

The development Worker, Commander, and external AgentBackend boundaries all
need the same protected-path decision.  Keeping the policy here avoids a
slightly different allowlist in each caller.  This is a path policy only; it
does not grant access to the protected responsibility.
"""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
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
        # Host-owned outbound/network boundary and its development operator.
        "src/dev_agent/providers/host_dispatch.py",
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
        # Process Coordination and Guardian authority
        "src/dev_agent/intelligence/refinement.py",
        "src/dev_agent/intelligence/self_repair.py",
        "scripts/devfarm_self_repair.py",
        "scripts/devfarm_supervisor.py",
        "scripts/devfarm_verification.py",
        "scripts/devfarm_guardian.py",
        "scripts/devfarm_host_dispatch.py",
        "scripts/devfarm_mcp.py",
        "config/v2.yaml",
        "scripts/devfarm.py",
        "scripts/devfarm_worker.py",
        "scripts/devfarm_worker_admission.py",
        "scripts/devfarm_provider_runtime.py",
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
        "src/dev_agent/coordination",
        # Reviewed model availability, benchmark, and capability snapshots
        # can influence Router admission.  Workers may consume them but must
        # not rewrite the evidence that the Host composes.
        "spec/v2/model_evidence",
        ".devfarm",
        ".github",
    }
)

# A protected path is not necessarily a secret.  Keep that distinction
# explicit for callers that need to decide whether a change may be proposed in
# an isolated worktree while still requiring elevated review before adoption.
# The legacy ``is_protected_path`` predicate remains conservative and returns
# True for both non-normal classes.
HARD_DENY_DIRECTORY_PREFIXES = frozenset({"recovery", ".devfarm"})
AUTHORITY_SENSITIVE_DIRECTORY_PREFIXES = frozenset(PROTECTED_DIRECTORY_PREFIXES - HARD_DENY_DIRECTORY_PREFIXES)


class PathProtectionClass(str, Enum):
    """Host policy classification for a repository-relative path."""

    HARD_DENY = "HARD_DENY"
    AUTHORITY_SENSITIVE = "AUTHORITY_SENSITIVE"
    NORMAL_REPO = "NORMAL_REPO"

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
    windows = PureWindowsPath(normalized)
    if (
        parsed.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        # Callers validate safe relative paths separately.  Treating an
        # invalid path as protected is the safe default at this boundary.
        return None
    return parsed.parts


def _under_prefix(normalized: str, prefix: str) -> bool:
    return normalized == prefix or normalized.startswith(prefix + "/")


def classify_path(path: Any) -> PathProtectionClass:
    """Classify a path without granting read, write, or egress authority.

    ``HARD_DENY`` is reserved for secrets, repository/runtime state, recovery
    data, and malformed paths.  ``AUTHORITY_SENSITIVE`` identifies code that
    can influence policy or authority but is not itself secret material.  The
    latter remains protected by the legacy predicate until a separate Host
    policy explicitly permits an isolated proposal/edit path.
    """

    parts = _normalized_parts(path)
    if parts is None:
        return PathProtectionClass.HARD_DENY
    normalized = "/".join(parts)
    for part in parts:
        lowered = part.lower()
        if part == ".git" or lowered.startswith(".env") or lowered in PROTECTED_PART_NAMES:
            return PathProtectionClass.HARD_DENY
        if lowered in PROTECTED_SECRET_FILENAMES or any(lowered.endswith(suffix) for suffix in PROTECTED_SECRET_SUFFIXES):
            return PathProtectionClass.HARD_DENY
        stem = lowered.rsplit(".", 1)[0]
        if any(marker in stem for marker in ("credential", "private_key", "secret_token", "access_token", "api_key")):
            return PathProtectionClass.HARD_DENY
    if normalized in PROTECTED_AUTHORITY_PATHS:
        return PathProtectionClass.AUTHORITY_SENSITIVE
    if any(_under_prefix(normalized, prefix) for prefix in HARD_DENY_DIRECTORY_PREFIXES):
        return PathProtectionClass.HARD_DENY
    if any(_under_prefix(normalized, prefix) for prefix in AUTHORITY_SENSITIVE_DIRECTORY_PREFIXES):
        return PathProtectionClass.AUTHORITY_SENSITIVE
    return PathProtectionClass.NORMAL_REPO


def is_hard_denied_path(path: Any) -> bool:
    """Return whether a path is never delegated by the current policy."""

    return classify_path(path) is PathProtectionClass.HARD_DENY


def is_authority_sensitive_path(path: Any) -> bool:
    """Return whether a path needs elevated adoption authority."""

    return classify_path(path) is PathProtectionClass.AUTHORITY_SENSITIVE


def is_protected_path(path: Any) -> bool:
    """Return the legacy conservative protected-path decision.

    This intentionally remains true for both hard-deny and authority-
    sensitive paths so existing Worker/Egress callers do not gain authority
    as a side effect of introducing the finer-grained classification.
    """

    return classify_path(path) is not PathProtectionClass.NORMAL_REPO


__all__ = [
    "AUTHORITY_SENSITIVE_DIRECTORY_PREFIXES",
    "HARD_DENY_DIRECTORY_PREFIXES",
    "PathProtectionClass",
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "PROTECTED_SECRET_FILENAMES",
    "PROTECTED_SECRET_SUFFIXES",
    "classify_path",
    "is_authority_sensitive_path",
    "is_hard_denied_path",
    "is_protected_path",
]
