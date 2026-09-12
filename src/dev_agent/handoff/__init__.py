"""Model-neutral handoff protocol and renderer."""

from .protocol import HandoffEnvelope, HandoffKind, HandoffRole, PayloadMode
from .renderer import render_handoff
from .roles import ExecutorRole, PlannerRole, ReviewerRole
from .validation import validate_handoff

__all__ = [
    "HandoffEnvelope",
    "HandoffKind",
    "HandoffRole",
    "PayloadMode",
    "ExecutorRole",
    "PlannerRole",
    "ReviewerRole",
    "render_handoff",
    "validate_handoff",
]
