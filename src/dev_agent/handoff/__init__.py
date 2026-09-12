"""Model-neutral handoff protocol and renderer."""

from .directive import (
    AuthoritySource,
    ContinuationMode,
    DEFAULT_AUTHORITY_PRECEDENCE,
    HandoffDirective,
    PayloadSemantics,
    SourceRequirement,
)
from .presets import (
    critical_adjacent_audit,
    current_state_analysis,
    current_state_request,
    decision_request,
    implementation_instruction,
    integration_request,
    reanalyze_after_correction,
    reaudit_after_change,
    roadmap_comparison,
    sequence_planning_request,
)
from .protocol import HandoffEnvelope, HandoffKind, HandoffRole, PayloadMode
from .renderer import render_handoff
from .roles import ExecutorRole, PlannerRole, ReviewerRole
from .validation import validate_handoff

__all__ = [
    "HandoffEnvelope",
    "HandoffDirective",
    "HandoffKind",
    "HandoffRole",
    "PayloadMode",
    "AuthoritySource",
    "ContinuationMode",
    "DEFAULT_AUTHORITY_PRECEDENCE",
    "PayloadSemantics",
    "SourceRequirement",
    "ExecutorRole",
    "PlannerRole",
    "ReviewerRole",
    "render_handoff",
    "validate_handoff",
    "critical_adjacent_audit",
    "current_state_analysis",
    "current_state_request",
    "decision_request",
    "implementation_instruction",
    "integration_request",
    "reanalyze_after_correction",
    "reaudit_after_change",
    "roadmap_comparison",
    "sequence_planning_request",
]
