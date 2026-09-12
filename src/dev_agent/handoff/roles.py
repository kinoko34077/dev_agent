"""Model-neutral role contracts for one development handoff cycle.

These are protocols only.  They do not own scheduling, task state, budget,
authority, or execution.  The development-only cycle composition lives under
``scripts/`` and must use the existing DevFarm boundaries.
"""

from __future__ import annotations

from typing import Protocol

from .protocol import HandoffEnvelope


class PlannerRole(Protocol):
    def plan(self, request: HandoffEnvelope) -> HandoffEnvelope:
        """Turn a human-authorized objective into an executor handoff."""


class ExecutorRole(Protocol):
    def execute(self, request: HandoffEnvelope) -> HandoffEnvelope:
        """Execute one bounded implementation attempt."""


class ReviewerRole(Protocol):
    def review(self, request: HandoffEnvelope) -> HandoffEnvelope:
        """Review current evidence and return the next human-facing result."""


__all__ = ["ExecutorRole", "PlannerRole", "ReviewerRole"]
