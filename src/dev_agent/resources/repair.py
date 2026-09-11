"""Explicit, operator-owned repair of legacy Resource projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from .billing_catalog import TRUSTED_RESOURCE_CATALOG
from .ledger import ResourceLedger
from .qualification import QualificationResolver


@dataclass(frozen=True)
class ResourceRepairPlan:
    resource_id: str
    status: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "status": self.status,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
        }


def _safe_projection(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), Mapping) else {}
    return {
        "provider_id": resource.get("provider_id"),
        "provider_binding_id": resource.get("provider_binding_id") or metadata.get("provider_binding_id"),
        "model_id": metadata.get("model_id"),
        "capabilities": sorted(str(value) for value in resource.get("capabilities", ())),
        "sensitivity": resource.get("sensitivity"),
        "cost_minor": resource.get("cost_minor"),
        "price_currency": resource.get("price_currency"),
        "quota_domain": resource.get("quota_domain"),
        "qualification_required": metadata.get("qualification_required"),
        "qualification_confidence": metadata.get("qualification_confidence"),
        "billing_authority": metadata.get("billing_authority"),
        "billing_mode": metadata.get("billing_mode"),
        "overage_policy": metadata.get("overage_policy"),
        "no_charge_guaranteed": metadata.get("no_charge_guaranteed"),
        "billing_verified_at": metadata.get("billing_verified_at"),
        "billing_expires_at": metadata.get("billing_expires_at"),
        "allowance_amount": metadata.get("allowance_amount"),
        "allowance_currency": metadata.get("allowance_currency"),
        "allowance_period": metadata.get("allowance_period"),
        "privacy_profile": metadata.get("privacy_profile"),
        "intelligence_tier": metadata.get("intelligence_tier"),
    }


def _profile_for(provider_id: str, binding_id: str, model_id: str, *, now: datetime):
    profile = TRUSTED_RESOURCE_CATALOG.get((provider_id, binding_id, model_id))
    if profile is None:
        return None, "no exact trusted billing profile"
    if not profile.is_current(now=now):
        return None, "trusted billing profile is expired or not yet current"
    return profile, ""


def _plan_one(resource: Mapping[str, Any], resolver: QualificationResolver, *, now: datetime) -> ResourceRepairPlan:
    resource_id = str(resource["resource_id"])
    before = _safe_projection(resource)
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), Mapping) else {}
    provider_id = resource.get("provider_id")
    binding_id = resource.get("provider_binding_id") or metadata.get("provider_binding_id")
    model_id = metadata.get("model_id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        return ResourceRepairPlan(resource_id, "blocked", before, before, "provider identity is missing")
    if not isinstance(binding_id, str) or not binding_id.strip():
        return ResourceRepairPlan(resource_id, "blocked", before, before, "provider binding identity is missing")
    if not isinstance(model_id, str) or not model_id.strip():
        return ResourceRepairPlan(resource_id, "blocked", before, before, "model identity is missing")
    provider_id = provider_id.strip()
    binding_id = binding_id.strip()
    model_id = model_id.strip()
    profile, reason = _profile_for(provider_id, binding_id, model_id, now=now)
    if profile is None:
        return ResourceRepairPlan(resource_id, "blocked", before, before, reason)
    if profile.quota_required and not isinstance(resource.get("quota_domain"), str):
        return ResourceRepairPlan(resource_id, "blocked", before, before, "quota_domain is required for this binding")
    qualification = resolver.resolve(provider_id, binding_id, model_id, now=now)
    qualification_required = provider_id != "fake"
    if qualification_required and qualification is None:
        return ResourceRepairPlan(resource_id, "blocked", before, before, "qualification is expired or unqualified")
    resource_sensitivity = "sensitive" if provider_id == "ollama" else "normal"
    privacy_profile = "local_only" if provider_id == "ollama" else "remote_cloud"
    tier = qualification.intelligence_tier if qualification is not None else profile.intelligence_tier
    capabilities = sorted(qualification.routing_capabilities if qualification is not None else {"text"})
    after = {
        "provider_id": provider_id,
        "provider_binding_id": binding_id,
        "model_id": model_id,
        "capabilities": capabilities,
        "sensitivity": resource_sensitivity,
        "cost_minor": profile.cost_minor,
        "price_currency": profile.price_currency,
        "quota_domain": resource.get("quota_domain"),
        "qualification_required": qualification_required,
        "qualification_confidence": qualification.confidence if qualification is not None else None,
        "billing_authority": "trusted_catalog",
        "billing_mode": profile.billing_mode,
        "overage_policy": profile.overage_policy,
        "no_charge_guaranteed": profile.no_charge_guaranteed,
        "billing_verified_at": profile.verified_at,
        "billing_expires_at": profile.expires_at,
        "allowance_amount": profile.allowance_amount,
        "allowance_currency": profile.allowance_currency,
        "allowance_period": profile.allowance_period,
        "privacy_profile": privacy_profile,
        "intelligence_tier": tier,
    }
    status = "current" if before == after else "repairable"
    return ResourceRepairPlan(resource_id, status, before, after)


def plan_resource_repairs(
    ledger: ResourceLedger,
    resolver: QualificationResolver,
    *,
    resource_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[ResourceRepairPlan]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    resources = ledger.list_resources()
    if resource_ids is not None:
        resources = [resource for resource in resources if resource["resource_id"] in resource_ids]
    return [_plan_one(resource, resolver, now=current) for resource in resources]


def apply_resource_repairs(
    ledger: ResourceLedger,
    resolver: QualificationResolver,
    *,
    operator_ref: str,
    resource_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[ResourceRepairPlan]:
    plans = plan_resource_repairs(ledger, resolver, resource_ids=resource_ids, now=now)
    repaired: list[ResourceRepairPlan] = []
    for plan in plans:
        if plan.status != "repairable":
            repaired.append(plan)
            continue
        resource = ledger.get_resource(plan.resource_id)
        metadata = dict(resource.get("metadata") or {})
        # Remove stale allowance keys before re-projecting so that renames or
        # removals in the billing catalog are applied cleanly.
        for _stale in ("allowance_amount", "allowance_currency", "allowance_period"):
            metadata.pop(_stale, None)
        metadata.update(
            {
                "provider_binding_id": plan.after["provider_binding_id"],
                "model_id": plan.after["model_id"],
                "qualification_required": plan.after["qualification_required"],
                "qualification_confidence": plan.after["qualification_confidence"],
                "billing_authority": plan.after["billing_authority"],
                "billing_mode": plan.after["billing_mode"],
                "overage_policy": plan.after["overage_policy"],
                "no_charge_guaranteed": plan.after["no_charge_guaranteed"],
                "billing_verified_at": plan.after["billing_verified_at"],
                "billing_expires_at": plan.after["billing_expires_at"],
                "privacy_profile": plan.after["privacy_profile"],
            }
        )
        if plan.after["allowance_amount"] is not None:
            metadata["allowance_amount"] = plan.after["allowance_amount"]
        if plan.after["allowance_currency"] is not None:
            metadata["allowance_currency"] = plan.after["allowance_currency"]
        if plan.after["allowance_period"] is not None:
            metadata["allowance_period"] = plan.after["allowance_period"]
        if plan.after["intelligence_tier"] is not None:
            metadata["intelligence_tier"] = plan.after["intelligence_tier"]
        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        ledger.repair_resource_projection(
            plan.resource_id,
            capabilities=plan.after["capabilities"],
            sensitivity=plan.after["sensitivity"],
            cost_minor=plan.after["cost_minor"],
            price_currency=plan.after["price_currency"],
            metadata=metadata,
            audit_id=str(uuid4()),
            operator_ref=operator_ref,
            before=plan.before,
            after=plan.after,
            reason="explicit operator resource projection repair",
            created_at=created_at,
        )
        repaired.append(ResourceRepairPlan(plan.resource_id, "repaired", plan.before, plan.after))
    return repaired


__all__ = ["ResourceRepairPlan", "apply_resource_repairs", "plan_resource_repairs"]
