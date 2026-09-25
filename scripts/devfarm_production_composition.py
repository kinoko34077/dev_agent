"""Thin Phase 8 production-composition primitives.

This module is intentionally not a second scheduler.  It starts by owning the
identity seam between the Operation planner projection and the DevFarm
Commander projection.  The two projections deliberately use different durable
UUID namespaces, so integration evidence must be joined by the validated
planner identity (proposal_id + child_key), never by coincidental task IDs.

The public ``Phase8ProductionComposition`` facade is added only after its
submit/observe lifecycle can be backed by the existing production authorities;
leaving it absent keeps the Phase 8 E2E RED rather than hiding an incomplete
composition behind a placeholder implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from src.dev_agent.domain.protocol import Task
from src.dev_agent.intelligence.planner import RootPlanningProposal


class ProductionCompositionError(ValueError):
    """A production projection cannot be joined without ambiguity."""


@dataclass(frozen=True)
class _DevelopmentTaskLink:
    child_key: str
    operation_task_id: str
    commander_task_id: str


def _development_task_links(
    proposal: RootPlanningProposal,
    operation_children: Sequence[Task],
    commander_tasks: Sequence[Mapping[str, Any]],
) -> tuple[_DevelopmentTaskLink, ...]:
    """Join Operation and Commander tasks by the planner's stable child key.

    Operation children persist ``planning_proposal_id`` and
    ``planner_child_key``.  Commander candidates preserve proposal order but
    generate a different deterministic task UUID.  This function validates
    both projections before returning an explicit mapping used by the future
    composition owner.
    """

    if not isinstance(proposal, RootPlanningProposal):
        raise TypeError("proposal must be a RootPlanningProposal")
    expected_keys = tuple(child.child_key for child in proposal.children)

    operation_by_key: dict[str, Task] = {}
    for task in operation_children:
        if not isinstance(task, Task):
            raise TypeError("operation_children must contain Task values")
        if task.metadata.get("planning_proposal_id") != proposal.proposal_id:
            raise ProductionCompositionError("operation child belongs to another planning proposal")
        child_key = task.metadata.get("planner_child_key")
        if not isinstance(child_key, str) or child_key not in expected_keys:
            raise ProductionCompositionError("operation child has no validated planner child key")
        if child_key in operation_by_key:
            raise ProductionCompositionError(f"duplicate operation child key: {child_key}")
        operation_by_key[child_key] = task

    if len(commander_tasks) != len(expected_keys):
        raise ProductionCompositionError("Commander projection does not match planner child count")
    commander_by_key: dict[str, Mapping[str, Any]] = {}
    for child_key, task in zip(expected_keys, commander_tasks, strict=True):
        if not isinstance(task, Mapping):
            raise TypeError("commander_tasks must contain mappings")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProductionCompositionError("Commander task has no durable task_id")
        commander_by_key[child_key] = task

    if set(operation_by_key) != set(expected_keys):
        raise ProductionCompositionError("Operation projection is missing planner children")

    return tuple(
        _DevelopmentTaskLink(
            child_key=child_key,
            operation_task_id=operation_by_key[child_key].task_id,
            commander_task_id=str(commander_by_key[child_key]["task_id"]),
        )
        for child_key in expected_keys
    )


__all__ = ["ProductionCompositionError"]
