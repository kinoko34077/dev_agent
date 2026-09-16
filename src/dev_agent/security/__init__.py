"""Security boundary helpers for durable audit and event data."""

from .event_artifacts import EventArtifactStore
from .egress import (
    EgressDecision,
    EgressFileEntry,
    EgressManifest,
    EgressValidationError,
    StandingEgressGrant,
    build_egress_manifest,
    contains_secret_candidate,
)
from .protected_paths import (
    AUTHORITY_SENSITIVE_DIRECTORY_PREFIXES,
    HARD_DENY_DIRECTORY_PREFIXES,
    PathProtectionClass,
    PROTECTED_AUTHORITY_PATHS,
    PROTECTED_DIRECTORY_PREFIXES,
    PROTECTED_PART_NAMES,
    classify_path,
    is_authority_sensitive_path,
    is_hard_denied_path,
    is_protected_path,
)

__all__ = [
    "EventArtifactStore",
    "EgressDecision",
    "EgressFileEntry",
    "EgressManifest",
    "EgressValidationError",
    "StandingEgressGrant",
    "build_egress_manifest",
    "contains_secret_candidate",
    "AUTHORITY_SENSITIVE_DIRECTORY_PREFIXES",
    "HARD_DENY_DIRECTORY_PREFIXES",
    "PathProtectionClass",
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "classify_path",
    "is_authority_sensitive_path",
    "is_hard_denied_path",
    "is_protected_path",
]
