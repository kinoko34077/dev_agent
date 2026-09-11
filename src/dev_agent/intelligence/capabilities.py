"""Capability vocabulary shared by task policy and provider routing.

The historical ``Task.required_capabilities`` field is kept for wire and
storage compatibility, but its values have different meanings.  This module
provides the narrow projection used by a ModelProvider request so policy
traits and task competencies cannot accidentally become provider capability
requirements.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.capabilities import CANONICAL_EXECUTION_CAPABILITIES

TASK_COMPETENCIES = frozenset(
    {
        "architecture",
        "coding",
        "review",
        "extraction",
        "classification",
        "translation",
        "documentation",
        "multilingual",
    }
)
TASK_POLICY_TRAITS = frozenset(
    {"security", "protected", "recovery", "security_sensitive", "private", "sensitive"}
)


class CapabilityClassificationError(ValueError):
    """Raised when a task contains a capability outside the known vocabulary."""


def classify_task_capabilities(values: Iterable[str]) -> dict[str, str]:
    """Classify task capability values and reject unknown spellings.

    The return value is deterministic and preserves the caller's first-seen
    spelling order.  Classification is a task-contract concern; the result is
    not a qualification or an authority grant.
    """

    if isinstance(values, (str, bytes)):
        raise CapabilityClassificationError("task capabilities must be a collection")
    try:
        items = list(values)
    except TypeError as exc:
        raise CapabilityClassificationError("task capabilities must be a collection") from exc
    classifications: dict[str, str] = {}
    for value in items:
        if not isinstance(value, str) or not value.strip():
            raise CapabilityClassificationError("task capability must be a non-empty string")
        normalized = value.strip().lower()
        if normalized in CANONICAL_EXECUTION_CAPABILITIES:
            kind = "execution"
        elif normalized in TASK_COMPETENCIES:
            kind = "competency"
        elif normalized in TASK_POLICY_TRAITS:
            kind = "policy_trait"
        else:
            raise CapabilityClassificationError(f"unknown task capability: {value}")
        classifications.setdefault(normalized, kind)
    return classifications


def execution_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    """Return only canonical provider execution capabilities for routing."""

    classifications = classify_task_capabilities(values)
    return tuple(name for name, kind in classifications.items() if kind == "execution")


__all__ = [
    "CANONICAL_EXECUTION_CAPABILITIES",
    "TASK_COMPETENCIES",
    "TASK_POLICY_TRAITS",
    "CapabilityClassificationError",
    "classify_task_capabilities",
    "execution_capabilities",
]
