"""Read-only queries shared by DevFarm plan consumers.

This module contains no plan mutation, approval consumption, dispatch, or
integration authority.  It centralizes the small identity-bound lookups used
by repair and other Host compositions so callers do not duplicate slightly
different interpretations of a Commander plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.devfarm import DevFarmError


def require_task(plan: Mapping[str, Any], task_id: str) -> Mapping[str, Any]:
    """Return one task by durable ID or fail closed."""

    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise DevFarmError("plan tasks are missing")
    for item in tasks:
        if isinstance(item, Mapping) and item.get("task_id") == task_id:
            return item
    raise DevFarmError(f"repair task does not exist: {task_id}")


def require_approved_review_decision(
    plan: Mapping[str, Any],
    *,
    decision_id: str,
    task_id: str,
    attempt_id: str,
    required_decision: str = "APPROVE_INTEGRATION",
) -> Mapping[str, Any]:
    """Return an identity-bound durable decision with the required outcome."""

    decisions = plan.get("review_decisions")
    if not isinstance(decisions, list):
        raise DevFarmError("repair plan review decisions are missing")
    for item in decisions:
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("decision_id") == decision_id
            and item.get("task_id") == task_id
            and item.get("attempt_id") == attempt_id
        ):
            if item.get("decision") != required_decision:
                raise DevFarmError(f"repair requires {required_decision}")
            return item
    raise DevFarmError("matching durable repair review decision is missing")


def artifact_reference_paths(packet: Mapping[str, Any]) -> set[str]:
    """Return only bounded path references from a compact artifact packet."""

    references = packet.get("artifact_refs")
    if not isinstance(references, list):
        raise DevFarmError("repair ReviewPacket artifact references are missing")
    return {
        reference["path"]
        for reference in references
        if isinstance(reference, Mapping) and isinstance(reference.get("path"), str)
    }


__all__ = [
    "artifact_reference_paths",
    "require_approved_review_decision",
    "require_task",
]
