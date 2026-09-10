"""Small bridge from host intelligence policy to bounded resource routing."""

from __future__ import annotations

from typing import Any

from ..domain.protocol import IntelligenceTier
from .policy import IntelligenceDecision


class IntelligenceRoutePolicy:
    """Render a host policy decision as explicit, non-self-elevating metadata.

    The marker is opt-in. A normal request keeps the legacy routing behavior;
    when the marker is present, the ResourceRouter requires an exact tier
    label on the selected resource. The model never supplies this decision.
    """

    MODE = "bounded"

    @staticmethod
    def thinking_effort_for_tier(tier: IntelligenceTier, *, difficult: bool = False) -> str:
        """Return the minimum provider-native thinking level for a tier.

        Model identity and reasoning effort intentionally remain separate. The
        adapter may translate this bounded value to its own wire format; a
        provider cannot raise it through model output.
        """

        if not isinstance(tier, IntelligenceTier):
            try:
                tier = IntelligenceTier(tier)
            except (TypeError, ValueError) as exc:
                raise ValueError("tier must be L0, L1, L2, or L3") from exc
        if tier in {IntelligenceTier.L0, IntelligenceTier.L1}:
            return "minimal"
        if tier is IntelligenceTier.L2:
            return "high" if difficult else "low"
        return "high"

    @classmethod
    def thinking_effort_for(cls, decision: IntelligenceDecision) -> str:
        if not isinstance(decision, IntelligenceDecision):
            raise TypeError("decision must be IntelligenceDecision")
        difficult = any(reason in {"risk:high", "risk:critical"} for reason in decision.reasons)
        return cls.thinking_effort_for_tier(decision.minimum_tier, difficult=difficult)

    @classmethod
    def metadata_for(cls, decision: IntelligenceDecision) -> dict[str, Any]:
        if not isinstance(decision, IntelligenceDecision):
            raise TypeError("decision must be IntelligenceDecision")
        allowed = tuple(decision.allowed_tiers)
        if not allowed or any(not isinstance(tier, IntelligenceTier) for tier in allowed):
            raise ValueError("decision must contain allowed intelligence tiers")
        thinking_effort = cls.thinking_effort_for(decision)
        return {
            "intelligence_routing": cls.MODE,
            "current_intelligence_tier": decision.current_tier.value,
            "escalation_intelligence_tiers": [tier.value for tier in decision.escalation_tiers],
            "allowed_intelligence_tiers": [tier.value for tier in allowed],
            "minimum_thinking_effort": thinking_effort,
            "thinking_effort": thinking_effort,
        }


__all__ = ["IntelligenceRoutePolicy"]
