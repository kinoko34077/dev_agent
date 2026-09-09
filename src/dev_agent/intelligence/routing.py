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

    @classmethod
    def metadata_for(cls, decision: IntelligenceDecision) -> dict[str, Any]:
        if not isinstance(decision, IntelligenceDecision):
            raise TypeError("decision must be IntelligenceDecision")
        allowed = tuple(decision.allowed_tiers)
        if not allowed or any(not isinstance(tier, IntelligenceTier) for tier in allowed):
            raise ValueError("decision must contain allowed intelligence tiers")
        return {
            "intelligence_routing": cls.MODE,
            "allowed_intelligence_tiers": [tier.value for tier in allowed],
        }


__all__ = ["IntelligenceRoutePolicy"]
