"""Security boundary helpers for durable audit and event data."""

from .event_artifacts import EventArtifactStore
from .protected_paths import (
    PROTECTED_AUTHORITY_PATHS,
    PROTECTED_DIRECTORY_PREFIXES,
    PROTECTED_PART_NAMES,
    is_protected_path,
)

__all__ = [
    "EventArtifactStore",
    "PROTECTED_AUTHORITY_PATHS",
    "PROTECTED_DIRECTORY_PREFIXES",
    "PROTECTED_PART_NAMES",
    "is_protected_path",
]
