"""Canonical production intelligence-tier authority.

Provider configuration, persisted resource metadata, qualification evidence,
and benchmark admission are not interchangeable facts.  This module is the
small shared decision boundary that combines them without creating another
router or lifecycle.  Remote tiers require current qualification evidence;
configuration and billing catalog labels never promote a remote route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import provider_policy


_VALID_TIERS = frozenset({"L0", "L1", "L2", "L3"})
_LOCAL_POLICY_TIERS = frozenset({"L0", "L1"})


@dataclass(frozen=True)
class ProductionTierDecision:
    """Bounded result of one exact provider/binding/model tier decision."""

    tier: str | None
    eligible: bool
    source: str
    reason: str

    def __post_init__(self) -> None:
        if self.tier is not None and self.tier not in _VALID_TIERS:
            raise ValueError("tier must be one of L0, L1, L2, or L3")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "eligible": self.eligible,
            "source": self.source,
            "reason": self.reason,
        }


def _evidence_tier(evidence: Any | None) -> str | None:
    if evidence is None:
        return None
    tier = getattr(evidence, "intelligence_tier", None)
    if tier is None:
        return None
    if not isinstance(tier, str) or tier not in _VALID_TIERS:
        raise ValueError("tier evidence must contain one of L0, L1, L2, or L3")
    return tier


class ProductionTierAuthority:
    """Resolve the one production tier for an exact execution identity.

    Remote configuration, persisted labels, and billing profiles are merely
    descriptive.  A remote route is eligible only with current qualification
    evidence.  A benchmark/model-admission tier may refine that qualification,
    but it cannot stand alone.  Local execution has an explicit conservative
    L0/L1 operator policy; local L2/L3 requires evidence instead of silently
    becoming a formal high-tier route.
    """

    def resolve(
        self,
        *,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        configured_tier: str | None = None,
        persisted_tier: str | None = None,
        qualification: Any | None = None,
        model_admission: Any | None = None,
        profile: Any | None = None,
        allow_legacy_missing_model: bool = False,
    ) -> ProductionTierDecision:
        if not all(isinstance(value, str) and value.strip() for value in (provider_id, provider_binding_id)):
            return ProductionTierDecision(None, False, "none", "IDENTITY_INCOMPLETE")
        if (not isinstance(model_id, str) or not model_id.strip()) and not allow_legacy_missing_model:
            return ProductionTierDecision(None, False, "none", "IDENTITY_INCOMPLETE")

        configured = self._validate_optional_tier(configured_tier, "configured_tier")
        persisted = self._validate_optional_tier(persisted_tier, "persisted_tier")
        qualification_tier = _evidence_tier(qualification)
        admission_tier = _evidence_tier(model_admission)
        if qualification_tier is not None and admission_tier is not None and qualification_tier != admission_tier:
            return ProductionTierDecision(None, False, "evidence", "TIER_EVIDENCE_CONFLICT")

        evidence_tier = admission_tier or qualification_tier
        if provider_policy.requires_qualification(provider_id):
            if qualification is None:
                return ProductionTierDecision(None, False, "none", "QUALIFICATION_MISSING")
            if evidence_tier is not None:
                source = "qualification+model_admission" if admission_tier is not None else "qualification"
                return ProductionTierDecision(evidence_tier, True, source, "QUALIFIED")
            if persisted is not None:
                # A current exact qualification may deliberately omit a tier
                # for legacy/L1 resources.  In that narrow case, an already
                # persisted tier is a projection of the same qualified
                # identity, not a free-standing configuration grant.  It is
                # never consulted when qualification is missing or expired.
                return ProductionTierDecision(
                    persisted,
                    True,
                    "qualification+persistent",
                    "QUALIFIED_PERSISTED_PROJECTION",
                )
            # A current qualification without an explicit tier remains a valid
            # low-risk route for requests that do not demand a tier.  It is not
            # a production tier grant and cannot satisfy an L1-L3 filter.
            return ProductionTierDecision(None, True, "qualification", "NO_TIER_GRANT")

        if evidence_tier is not None:
            source = "qualification+model_admission" if admission_tier is not None else "qualification"
            return ProductionTierDecision(evidence_tier, True, source, "LOCAL_EVIDENCE")

        local_tier = configured or persisted
        if local_tier is None and profile is not None:
            local_tier = self._validate_optional_tier(
                getattr(profile, "intelligence_tier", None),
                "profile.intelligence_tier",
            )
        if local_tier in _LOCAL_POLICY_TIERS:
            return ProductionTierDecision(local_tier, True, "local_policy", "LOCAL_OPERATOR_POLICY")
        if local_tier in {"L2", "L3"}:
            return ProductionTierDecision(None, False, "local_policy", "LOCAL_TIER_REQUIRES_EVIDENCE")
        if provider_id == "fake":
            return ProductionTierDecision("L1", True, "fake_default", "DETERMINISTIC_SMOKE")
        return ProductionTierDecision(None, True, "none", "NO_TIER_GRANT")

    @staticmethod
    def _validate_optional_tier(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in _VALID_TIERS:
            raise ValueError(f"{name} must be one of L0, L1, L2, or L3")
        return value


production_tier_authority = ProductionTierAuthority()


__all__ = ["ProductionTierAuthority", "ProductionTierDecision", "production_tier_authority"]
