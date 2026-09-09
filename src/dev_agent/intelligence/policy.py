"""Phase 7A/B task classification and intelligence-tier policy.

The policy is deliberately deterministic and independent from any model.  A
caller may request a task type, risk, and capabilities, but no task metadata
can promote itself to a higher tier.  Escalation is a later, explicit phase.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.protocol import IntelligenceTier, RiskLevel, Task, TaskType


_TIER_ORDER = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)
_BASE_BOUNDS: dict[TaskType, tuple[IntelligenceTier, IntelligenceTier]] = {
    TaskType.DETERMINISTIC: (IntelligenceTier.L0, IntelligenceTier.L0),
    TaskType.WORKER: (IntelligenceTier.L1, IntelligenceTier.L1),
    TaskType.REASONING: (IntelligenceTier.L2, IntelligenceTier.L2),
    TaskType.EXPERT: (IntelligenceTier.L3, IntelligenceTier.L3),
    TaskType.DELEGATED_AGENT: (IntelligenceTier.L2, IntelligenceTier.L2),
    TaskType.RECOVERY: (IntelligenceTier.L2, IntelligenceTier.L2),
    TaskType.PROTECTED: (IntelligenceTier.L3, IntelligenceTier.L3),
}
_CAPABILITY_MINIMUMS = {
    "architecture": IntelligenceTier.L2,
    "security": IntelligenceTier.L2,
    "protected": IntelligenceTier.L2,
}


def _tier_at_least(current: IntelligenceTier, required: IntelligenceTier) -> IntelligenceTier:
    return _TIER_ORDER[max(_TIER_ORDER.index(current), _TIER_ORDER.index(required))]


@dataclass(frozen=True)
class IntelligenceDecision:
    """Auditable bounds produced for one task before model dispatch."""

    minimum_tier: IntelligenceTier
    maximum_tier: IntelligenceTier
    allowed_tiers: tuple[IntelligenceTier, ...]
    requires_human_approval: bool
    reasons: tuple[str, ...]


class TaskIntelligencePolicy:
    """Map a typed Task profile to a bounded, non-self-elevating tier."""

    def decide(self, task: Task) -> IntelligenceDecision:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        minimum, maximum = _BASE_BOUNDS[task.task_type]
        reasons = [f"task_type:{task.task_type.value}"]

        for capability in task.required_capabilities:
            required = _CAPABILITY_MINIMUMS.get(capability.lower())
            if required is None:
                continue
            raised = _tier_at_least(minimum, required)
            if raised is not minimum:
                minimum = raised
                maximum = _tier_at_least(maximum, required)
            reasons.append(f"required_capability:{capability}")

        if task.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            required = IntelligenceTier.L2 if task.risk is RiskLevel.HIGH else IntelligenceTier.L3
            minimum = _tier_at_least(minimum, required)
            maximum = _tier_at_least(maximum, required)
            reasons.append(f"risk:{task.risk.value}")
        else:
            reasons.append(f"risk:{task.risk.value}")

        allowed = _TIER_ORDER[_TIER_ORDER.index(minimum) : _TIER_ORDER.index(maximum) + 1]
        return IntelligenceDecision(
            minimum_tier=minimum,
            maximum_tier=maximum,
            allowed_tiers=allowed,
            requires_human_approval=task.risk is RiskLevel.CRITICAL,
            reasons=tuple(reasons),
        )


__all__ = ["IntelligenceDecision", "TaskIntelligencePolicy"]
