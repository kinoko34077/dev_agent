"""Host-owned admission policy for development Worker providers.

This module owns only activation, qualification, and trusted no-charge
admission evidence.  It does not construct providers, dispatch requests, or
write Worker artifacts.  Keeping this boundary separate prevents the Worker
execution path from becoming the authority for its own outbound eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.devfarm_errors import DevFarmError
from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG
from src.dev_agent.resources.provider_policy import is_local_provider as _is_local_provider
from src.dev_agent.resources.qualification import QualificationError, QualificationResolver
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.resources.provider_policy import validate_provider_instance_authority


@dataclass(frozen=True)
class DevFarmWorkerEligibility:
    """Independent evidence used to admit one remote Worker binding."""

    provider_id: str
    model_id: str
    provider_binding_id: str | None
    intelligence_tier: str | None
    operator_activated: bool
    capability_qualified: bool
    capability_expires_at: str | None
    billing_admitted: bool
    billing_source: str | None
    billing_expires_at: str | None
    eligible: bool
    reason: str


class DevFarmActivationPolicy:
    """Resolve the Host-owned activation/qualification/billing boundary."""

    DEFAULT_ACTIVE_PROVIDER_IDS = frozenset({"cloudflare", "gemini", "openrouter"})

    def __init__(
        self,
        active_provider_ids: set[str] | frozenset[str] | None = None,
        *,
        capability_matrix_path: str | Path | None = None,
        now: datetime | None = None,
    ) -> None:
        selected = self.DEFAULT_ACTIVE_PROVIDER_IDS if active_provider_ids is None else active_provider_ids
        if not isinstance(selected, (set, frozenset)) or not all(
            isinstance(item, str) and item.strip() for item in selected
        ):
            raise ValueError("active_provider_ids must be a set of non-empty strings")
        self._active_provider_ids = frozenset(item.strip() for item in selected)
        if now is not None and not isinstance(now, datetime):
            raise ValueError("now must be a datetime or None")
        self._now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            self._qualification_resolver = QualificationResolver(matrix_path=capability_matrix_path)
        except QualificationError as exc:
            raise DevFarmError(f"development worker qualification matrix is invalid: {exc}") from exc

    def eligibility_for(
        self,
        provider_id: str,
        model_id: str,
        *,
        provider_binding_id: str | None = None,
    ) -> DevFarmWorkerEligibility:
        """Resolve activation, qualification, and billing without a wildcard."""

        normalized_provider = provider_id.strip() if isinstance(provider_id, str) else ""
        normalized_model = model_id.strip() if isinstance(model_id, str) else ""
        normalized_binding = None
        if provider_binding_id is not None:
            if not isinstance(provider_binding_id, str) or not provider_binding_id.strip():
                raise ValueError("provider_binding_id must be a non-empty string or None")
            normalized_binding = provider_binding_id.strip()
        operator_activated = bool(normalized_provider and normalized_provider in self._active_provider_ids)
        if not operator_activated:
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, None, None, False, False, None,
                False, None, None, False, "operator_inactive",
            )
        if not normalized_model:
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, None, None, True, False, None,
                False, None, None, False, "capability_unqualified_or_expired",
            )
        identities = self._qualification_resolver.catalog.identities_for(normalized_provider, normalized_model)
        if normalized_binding is None and len(identities) != 1:
            reason = "ambiguous_binding" if len(identities) > 1 else "capability_unqualified_or_expired"
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, None, None, True, False, None,
                False, None, None, False, reason,
            )
        binding_id = normalized_binding or identities[0][1]
        qualification = self._qualification_resolver.resolve(
            normalized_provider,
            binding_id,
            normalized_model,
            now=self._now,
        )
        if qualification is None or qualification.intelligence_tier != "L1" or "text" not in qualification.routing_capabilities:
            return DevFarmWorkerEligibility(
                normalized_provider,
                normalized_model,
                binding_id,
                None if qualification is None else qualification.intelligence_tier,
                True,
                False,
                None if qualification is None else qualification.expires_at,
                False,
                None,
                None,
                False,
                "capability_unqualified_or_expired",
            )
        tier = qualification.intelligence_tier
        capability_expiry = qualification.expires_at
        if normalized_binding is not None and normalized_binding != binding_id:
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, binding_id, tier, True, True,
                capability_expiry, False, None, None, False, "binding_not_qualified",
            )
        profile = TRUSTED_RESOURCE_CATALOG.get((normalized_provider, binding_id, normalized_model))
        if profile is None:
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, binding_id, tier, True, True,
                capability_expiry, False, None, None, False, "billing_unknown",
            )
        billing_current = profile.is_current(now=self._now)
        billing_admitted = profile.no_charge_guaranteed and billing_current
        if not billing_admitted:
            reason = "billing_expired" if not billing_current else "billing_not_no_charge"
            return DevFarmWorkerEligibility(
                normalized_provider, normalized_model, binding_id, tier, True, True,
                capability_expiry, False, profile.source, profile.expires_at, False, reason,
            )
        return DevFarmWorkerEligibility(
            normalized_provider, normalized_model, binding_id, tier, True, True,
            capability_expiry, True, profile.source, profile.expires_at, True, "eligible",
        )

    def is_active(self, provider_id: str, model_id: str | None = None) -> bool:
        if not isinstance(model_id, str) or not model_id.strip():
            return False
        return self.eligibility_for(provider_id, model_id).eligible

    def ensure_active(
        self,
        provider_id: str,
        model_id: str | None = None,
        *,
        provider_binding_id: str | None = None,
    ) -> None:
        if _is_local_provider(provider_id):
            raise DevFarmError(
                f"development worker provider is not active: {provider_id} (local providers are not permitted in DevFarm)"
            )
        if not isinstance(model_id, str) or not model_id.strip() or not self.eligibility_for(
            provider_id,
            model_id,
            provider_binding_id=provider_binding_id,
        ).eligible:
            label = f"{provider_id}/{model_id}" if model_id is not None else provider_id
            raise DevFarmError(f"development worker provider is not active: {label}")

    def binding_for(
        self,
        provider_id: str,
        model_id: str,
        *,
        provider_binding_id: str | None = None,
    ) -> tuple[str, str | None]:
        self.ensure_active(provider_id, model_id, provider_binding_id=provider_binding_id)
        eligibility = self.eligibility_for(provider_id, model_id, provider_binding_id=provider_binding_id)
        if eligibility.provider_binding_id is None:
            raise DevFarmError(f"development worker qualification is unavailable: {provider_id}/{model_id}")
        return eligibility.provider_binding_id, eligibility.intelligence_tier


def validate_worker_provider(
    provider: ModelProvider,
    *,
    allow_local: bool = False,
) -> tuple[str, str, str, str | None, DevFarmWorkerEligibility]:
    """Validate one injected Provider against Host admission authority.

    Construction and routing remain separate.  This function only verifies
    the live instance identity/authority and re-resolves the existing
    qualification/billing evidence before Worker execution begins.
    """

    provider_id = getattr(provider, "provider_id", None)
    model_id = getattr(provider, "model_id", None) or getattr(provider, "model", None)
    binding_id = getattr(provider, "provider_binding_id", None)
    tier = getattr(provider, "intelligence_tier", None)
    tier = getattr(tier, "value", tier)
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise DevFarmError("worker provider must expose a non-empty provider_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise DevFarmError("worker provider must expose a non-empty model_id")
    if not isinstance(binding_id, str) or not binding_id.strip():
        raise DevFarmError("worker provider must expose a non-empty provider_binding_id")
    if tier is not None and (not isinstance(tier, str) or not tier.strip()):
        raise DevFarmError("worker provider intelligence_tier must be a non-empty string or None")
    normalized_provider = provider_id.strip()
    normalized_model = model_id.strip()
    normalized_binding = binding_id.strip()
    normalized_tier = tier.strip() if isinstance(tier, str) else None
    try:
        validate_provider_instance_authority(provider)
    except ValueError as exc:
        raise DevFarmError(f"worker provider failed authority validation: {exc}") from exc
    if _is_local_provider(normalized_provider):
        if allow_local is not True:
            raise DevFarmError(
                "worker provider is not eligible for external development work: "
                f"{normalized_provider}/{normalized_binding}/{normalized_model} (local_trial_required)"
            )
        if normalized_tier is None:
            raise DevFarmError("local Worker trial requires an explicit intelligence tier")
        eligibility = DevFarmWorkerEligibility(
            normalized_provider,
            normalized_model,
            normalized_binding,
            normalized_tier,
            True,
            True,
            None,
            True,
            "local_runtime",
            None,
            True,
            "local_trial",
        )
        return normalized_provider, normalized_model, normalized_binding, normalized_tier, eligibility
    eligibility = DevFarmActivationPolicy().eligibility_for(
        normalized_provider,
        normalized_model,
        provider_binding_id=normalized_binding,
    )
    if not eligibility.eligible:
        raise DevFarmError(
            "worker provider is not eligible for external development work: "
            f"{normalized_provider}/{normalized_binding}/{normalized_model} ({eligibility.reason})"
        )
    if normalized_tier is not None and normalized_tier != eligibility.intelligence_tier:
        raise DevFarmError(
            "worker provider intelligence tier does not match qualified binding: "
            f"{normalized_tier} != {eligibility.intelligence_tier}"
        )
    return normalized_provider, normalized_model, normalized_binding, normalized_tier, eligibility


__all__ = ["DevFarmActivationPolicy", "DevFarmWorkerEligibility", "validate_worker_provider"]
