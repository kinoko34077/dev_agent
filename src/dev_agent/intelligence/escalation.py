"""Finite escalation planning for the Phase 7 intelligence hierarchy.

This module only plans the next bounded action.  It does not select a model,
dispatch a Provider, change a Task, or grant a task a higher authority.  The
caller must persist the returned plan and perform any later dispatch through
the existing control-plane boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any

from ..domain.protocol import IntelligenceTier
from .evaluator import EvaluatorDecision


_TIER_ORDER = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)


class EscalationTarget(str, Enum):
    NONE = "none"
    SAME_PROVIDER = "same_provider"
    OTHER_PROVIDER = "other_provider"
    HIGHER_TIER = "higher_tier"
    HUMAN = "human"


@dataclass(frozen=True)
class EscalationContext:
    """Immutable host-observed limits and availability for one next action."""

    task_id: str
    current_tier: IntelligenceTier
    allowed_tiers: tuple[IntelligenceTier, ...]
    attempt: int
    max_attempts: int
    escalation_count: int
    max_escalations: int
    now_epoch: float
    deadline_epoch: float
    budget_remaining: float
    estimated_cost: float
    retryable_failure: bool
    same_provider_available: bool
    alternate_provider_available: bool

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(self.current_tier, IntelligenceTier):
            raise ValueError("current_tier must be an IntelligenceTier")
        if not isinstance(self.allowed_tiers, tuple) or not self.allowed_tiers:
            raise ValueError("allowed_tiers must be a non-empty tuple")
        if any(not isinstance(tier, IntelligenceTier) for tier in self.allowed_tiers):
            raise ValueError("allowed_tiers must contain IntelligenceTier values")
        if len(set(self.allowed_tiers)) != len(self.allowed_tiers):
            raise ValueError("allowed_tiers must not contain duplicates")
        positions = [_TIER_ORDER.index(tier) for tier in self.allowed_tiers]
        if positions != sorted(positions):
            raise ValueError("allowed_tiers must be ordered from lower to higher tier")
        if self.current_tier not in self.allowed_tiers:
            raise ValueError("current_tier must be included in allowed_tiers")

        for name, value in (("attempt", self.attempt), ("max_attempts", self.max_attempts)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        for name, value in (("escalation_count", self.escalation_count), ("max_escalations", self.max_escalations)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.escalation_count > self.max_escalations:
            raise ValueError("escalation_count cannot exceed max_escalations")
        for name, value in (
            ("now_epoch", self.now_epoch),
            ("deadline_epoch", self.deadline_epoch),
            ("budget_remaining", self.budget_remaining),
            ("estimated_cost", self.estimated_cost),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.deadline_epoch < self.now_epoch:
            raise ValueError("deadline_epoch must not precede now_epoch")
        if self.budget_remaining < 0 or self.estimated_cost < 0:
            raise ValueError("budget values must be non-negative")
        for name, value in (
            ("retryable_failure", self.retryable_failure),
            ("same_provider_available", self.same_provider_available),
            ("alternate_provider_available", self.alternate_provider_available),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(self, "task_id", self.task_id.strip())


@dataclass(frozen=True)
class EscalationPlan:
    """The next bounded action, without performing that action."""

    task_id: str
    decision: EvaluatorDecision
    target: EscalationTarget
    next_tier: IntelligenceTier | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["target"] = self.target.value
        data["next_tier"] = self.next_tier.value if self.next_tier is not None else None
        data["reasons"] = list(self.reasons)
        return data


class BoundedEscalationPolicy:
    """Select a finite retry/escalation step from host-observed context."""

    def plan(self, context: EscalationContext) -> EscalationPlan:
        if not isinstance(context, EscalationContext):
            raise TypeError("context must be EscalationContext")

        if context.now_epoch >= context.deadline_epoch:
            return self._plan(context, EvaluatorDecision.FAIL, EscalationTarget.NONE, "deadline_exceeded")
        if context.attempt >= context.max_attempts:
            return self._plan(context, EvaluatorDecision.FAIL, EscalationTarget.NONE, "attempt_ceiling_reached")
        if context.estimated_cost > context.budget_remaining:
            return self._plan(context, EvaluatorDecision.WAIT_HUMAN, EscalationTarget.HUMAN, "budget_insufficient")

        if context.retryable_failure:
            if context.same_provider_available:
                return self._plan(context, EvaluatorDecision.RETRY_SAME, EscalationTarget.SAME_PROVIDER, "retryable_failure")
            if context.alternate_provider_available:
                return self._plan(context, EvaluatorDecision.RETRY_OTHER_PROVIDER, EscalationTarget.OTHER_PROVIDER, "retryable_failure")

        next_tier = self._next_tier(context)
        if next_tier is not None:
            if context.escalation_count < context.max_escalations:
                return self._plan(
                    context,
                    EvaluatorDecision.ESCALATE,
                    EscalationTarget.HIGHER_TIER,
                    "higher_tier_available",
                    next_tier=next_tier,
                )
            return self._plan(context, EvaluatorDecision.FAIL, EscalationTarget.NONE, "escalation_ceiling_reached")

        reason = "no_escalation_target" if context.retryable_failure else "non_retryable_failure"
        return self._plan(context, EvaluatorDecision.FAIL, EscalationTarget.NONE, reason)

    @staticmethod
    def _next_tier(context: EscalationContext) -> IntelligenceTier | None:
        current_position = _TIER_ORDER.index(context.current_tier)
        for tier in context.allowed_tiers:
            if _TIER_ORDER.index(tier) > current_position:
                return tier
        return None

    @staticmethod
    def _plan(
        context: EscalationContext,
        decision: EvaluatorDecision,
        target: EscalationTarget,
        *reasons: str,
        next_tier: IntelligenceTier | None = None,
    ) -> EscalationPlan:
        return EscalationPlan(
            task_id=context.task_id,
            decision=decision,
            target=target,
            next_tier=next_tier,
            reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        )


__all__ = ["BoundedEscalationPolicy", "EscalationContext", "EscalationPlan", "EscalationTarget"]
