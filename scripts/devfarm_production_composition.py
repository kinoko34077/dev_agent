"""Thin Phase 8 production-composition boundaries.

This module is intentionally not a second scheduler.  It owns the explicit
submit/observe boundary between callers and the existing Operation/DevFarm
authorities, plus the identity seam between their durable projections.  The
two projections deliberately use different UUID namespaces, so integration
evidence must be joined by the validated planner identity
(``proposal_id + child_key``), never by coincidental task IDs.

The facade delegates to injected production boundaries.  It does not sequence
Planner, Worker, Host Verification, Review, Integration, or dependency release
itself; those remain owned by the existing runtime and Supervisor paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from src.dev_agent.domain.protocol import Task
from src.dev_agent.intelligence.planner import RootPlanningProposal


class ProductionCompositionError(ValueError):
    """A production projection cannot be joined without ambiguity."""


_MAX_PROJECTION_DEPTH = 3
_MAX_PROJECTION_ITEMS = 64
_MAX_PROJECTION_TEXT = 2048
_FORBIDDEN_PROJECTION_TERMS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
    "raw_response",
    "raw_output",
)


def _bounded_projection(value: Any, *, depth: int = 0) -> Any:
    """Copy a small, secret-free result projection without invoking policy."""

    if depth > _MAX_PROJECTION_DEPTH:
        raise ProductionCompositionError("boundary result exceeds bounded projection depth")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PROJECTION_TEXT:
            raise ProductionCompositionError("boundary result exceeds bounded text")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PROJECTION_ITEMS:
            raise ProductionCompositionError("boundary result exceeds bounded mapping")
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ProductionCompositionError("boundary result has an invalid mapping key")
            normalized_key = key.strip()
            lowered_key = normalized_key.casefold()
            if any(term in lowered_key for term in _FORBIDDEN_PROJECTION_TERMS):
                raise ProductionCompositionError("boundary result is not bounded")
            projected[normalized_key] = _bounded_projection(item, depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_PROJECTION_ITEMS:
            raise ProductionCompositionError("boundary result exceeds bounded sequence")
        return [_bounded_projection(item, depth=depth + 1) for item in value]
    raise ProductionCompositionError("boundary result contains an unsupported value")


class Phase8ProductionComposition:
    """Expose only the production submit/observe boundary.

    The injected callables are composed from existing durable authorities by
    the embedding runtime.  Keeping them injected makes it impossible for
    this facade to create a second queue, retry loop, or integration path.
    """

    def __init__(self, *, submit_boundary: Any, observe_boundary: Any) -> None:
        if not callable(submit_boundary) or not callable(observe_boundary):
            raise TypeError("submit and observe boundary callables are required")
        self._submit_boundary = submit_boundary
        self._observe_boundary = observe_boundary

    def submit(self, objective: str, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(objective, str) or not objective.strip() or len(objective.strip()) > 4000:
            raise ValueError("objective must be bounded non-empty text")
        result = self._submit_boundary(objective.strip(), **kwargs)
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("submit boundary must return a mapping")
        return projected

    def observe(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id.strip()) > 256:
            raise ValueError("run_id must be bounded non-empty text")
        result = self._observe_boundary(run_id.strip(), **kwargs)
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("observe boundary must return a mapping")
        return projected


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

    Operation children and Commander candidates persist
    ``planning_proposal_id`` and ``planner_child_key``.  Commander generates a
    different deterministic task UUID, so this function validates the
    semantic identity on both projections before returning an explicit
    mapping used by the future composition owner. Planner child order is used
    only for deterministic presentation after the identity join.
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
        raise ProductionCompositionError("Commander projection is missing planner children")
    commander_by_key: dict[str, Mapping[str, Any]] = {}
    for task in commander_tasks:
        if not isinstance(task, Mapping):
            raise TypeError("commander_tasks must contain mappings")
        commander_proposal_id = task.get("planning_proposal_id")
        if commander_proposal_id is None:
            raise ProductionCompositionError("Commander task has no validated planner identity")
        if commander_proposal_id != proposal.proposal_id:
            raise ProductionCompositionError("Commander task belongs to another planning proposal")
        child_key = task.get("planner_child_key")
        if not isinstance(child_key, str) or not child_key.strip():
            raise ProductionCompositionError("Commander task has no validated planner child key")
        if child_key not in expected_keys:
            raise ProductionCompositionError("Commander task has an unknown planner child key")
        if child_key in commander_by_key:
            raise ProductionCompositionError(f"duplicate Commander child key: {child_key}")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProductionCompositionError("Commander task has no durable task_id")
        commander_by_key[child_key] = task

    if set(operation_by_key) != set(expected_keys):
        raise ProductionCompositionError("Operation projection is missing planner children")
    if set(commander_by_key) != set(expected_keys):
        raise ProductionCompositionError("Commander projection is missing planner children")

    return tuple(
        _DevelopmentTaskLink(
            child_key=child_key,
            operation_task_id=operation_by_key[child_key].task_id,
            commander_task_id=str(commander_by_key[child_key]["task_id"]),
        )
        for child_key in expected_keys
    )


__all__ = ["Phase8ProductionComposition", "ProductionCompositionError"]
