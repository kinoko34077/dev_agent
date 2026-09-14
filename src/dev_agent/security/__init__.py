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
    PROTECTED_AUTHORITY_PATHS,
    PROTECTED_DIRECTORY_PREFIXES,
    PROTECTED_PART_NAMES,
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
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "is_protected_path",
]
