"""Shared development-only Resource composition for DevFarm model roles.

Planner and Reviewer shadow runs have the same Host-owned admission and
temporary dispatch setup.  This module is the small public boundary for that
shared knowledge; it does not own production routing, budget, or task state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scripts.devfarm_errors import DevFarmError
from src.dev_agent.operation import OperationProviderBinding, configured_provider_pool_from_environment
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.billing_catalog import profile_for as default_profile_for
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.model_candidates import materialize_provider_bindings
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.ledger import ResourceLedger


class ResourcePoolError(DevFarmError):
    """A development Resource pool could not be admitted or composed."""


def make_binding(
    *,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
    credential_id: str | None = None,
    project_id: str | None = None,
    base_url: str | None = None,
    intelligence_tier: str | None = None,
) -> OperationProviderBinding:
    """Build one exact provider/binding/model execution identity."""

    return OperationProviderBinding(
        provider_id=provider_id,
        model=model_id,
        provider_binding_id=binding_id,
        api_key_env=api_key_env,
        quota_domain=quota_domain,
        timeout_seconds=timeout_seconds,
        credential_id=credential_id,
        project_id=project_id,
        base_url=base_url,
        intelligence_tier=intelligence_tier,
    )


def resolve_provider_pool(
    *,
    pool_json: str | None,
    use_configured_pool: bool,
    env: Callable[[str], str | None] = os.getenv,
) -> tuple[OperationProviderBinding, ...] | None:
    """Resolve one explicit, non-secret Provider pool source.

    This only materializes binding metadata.  Qualification, billing,
    privacy, quota, health, and model-candidate admission remain owned by
    :func:`admit_resource_pool`.  Keeping source selection here lets Planner
    and Reviewer use the same opt-in boundary without importing each other's
    private helpers.
    """

    if not isinstance(use_configured_pool, bool):
        raise ResourcePoolError("use_configured_pool must be a boolean")
    if not callable(env):
        raise ResourcePoolError("env must be callable")
    if pool_json is not None and not isinstance(pool_json, str):
        raise ResourcePoolError("pool-json must be a JSON string")
    if pool_json is not None and use_configured_pool:
        raise ResourcePoolError("pool-json and configured-pool cannot be combined")
    if pool_json is not None:
        try:
            raw_pool = json.loads(pool_json)
        except json.JSONDecodeError as exc:
            raise ResourcePoolError("pool-json must be valid JSON") from exc
        if not isinstance(raw_pool, list) or not raw_pool:
            raise ResourcePoolError("pool-json must be a non-empty JSON array")
        if any(not isinstance(entry, Mapping) for entry in raw_pool):
            raise ResourcePoolError("pool-json contains an invalid binding object")
        try:
            return tuple(OperationProviderBinding(**dict(entry)) for entry in raw_pool)
        except (TypeError, ValueError) as exc:
            raise ResourcePoolError(f"pool-json contains an invalid binding: {exc}") from exc
    if use_configured_pool:
        configured = configured_provider_pool_from_environment(env)
        if not configured:
            raise ResourcePoolError("configured provider pool is empty")
        return configured
    return None


def build_provider(*, binding: OperationProviderBinding) -> ModelProvider:
    """Construct a provider through the existing ProviderFactory boundary."""

    return ProviderFactory().create(
        ProviderDefinition(
            provider_id=binding.provider_id,
            model=binding.model,
            provider_binding_id=binding.binding_id,
            credential_id=binding.credential_id,
            api_key_env=binding.api_key_env,
            project_id=binding.project_id,
            base_url=binding.base_url,
            timeout_seconds=binding.timeout_seconds,
            intelligence_tier=binding.intelligence_tier,
        )
    )


def _resolved_model_admission(
    binding: OperationProviderBinding,
    resolver: ModelAdmissionResolver | None,
    *,
    now: datetime | None,
) -> Any | None:
    if resolver is None:
        return None
    kwargs: dict[str, Any] = {}
    if now is not None:
        kwargs["now"] = now
    return resolver.resolve(
        binding.provider_id,
        binding.credential_binding_id,
        binding.model,
        **kwargs,
    )


def admit_resource_pool(
    bindings: Sequence[OperationProviderBinding],
    *,
    resolver: QualificationResolver,
    required_tier: str = "L2",
    required_capabilities: Sequence[str] | None = None,
    no_charge_required: bool = True,
    model_admission_resolver: ModelAdmissionResolver | None = None,
    model_catalog: Any | None = None,
    expand_discovered_models: bool = False,
    now: datetime | None = None,
    profile_resolver: Callable[[str, str, str], Any] | None = None,
) -> tuple[tuple[OperationProviderBinding, Any, Any], ...]:
    """Admit exact current resources for any development model role.

    Qualification remains integration evidence, model admission supplies the
    benchmark-derived tier when present, and billing/privacy policy remains a
    separate Host-owned filter.  No role-specific Planner/Reviewer behavior
    lives here.
    """

    if not isinstance(required_tier, str) or not required_tier.strip():
        raise ResourcePoolError("required_tier must be a non-empty string")
    profile_lookup = profile_resolver or default_profile_for
    requested_capabilities = frozenset(required_capabilities or ())
    if any(not isinstance(item, str) or not item.strip() for item in requested_capabilities):
        raise ResourcePoolError("required_capabilities must contain non-empty strings")

    candidate_bindings: list[OperationProviderBinding] = []
    for binding in bindings:
        evidence_catalog = model_catalog
        if evidence_catalog is None and model_admission_resolver is not None:
            evidence_catalog = getattr(model_admission_resolver, "catalog", None)
        try:
            candidate_bindings.extend(
                materialize_provider_bindings(
                    binding,
                    evidence_catalog,
                    expand_discovered_models=expand_discovered_models,
                    now=now,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ResourcePoolError(f"unable to materialize model candidates: {exc}") from exc

    admitted: list[tuple[OperationProviderBinding, Any, Any]] = []
    for binding in candidate_bindings:
        qualification_kwargs: dict[str, Any] = {"min_confidence": "high"}
        if now is not None:
            qualification_kwargs["now"] = now
        qualification = resolver.resolve(
            binding.provider_id,
            binding.credential_binding_id,
            binding.model,
            **qualification_kwargs,
        )
        if qualification is None:
            continue
        model_admission = _resolved_model_admission(binding, model_admission_resolver, now=now)
        effective_tier = (
            model_admission.intelligence_tier
            if model_admission is not None
            else getattr(qualification, "intelligence_tier", None)
        )
        if effective_tier != required_tier:
            continue
        qualification_capabilities = frozenset(getattr(qualification, "routing_capabilities", ()))
        if not requested_capabilities.issubset(qualification_capabilities):
            continue
        if model_admission is not None:
            admission_capabilities = frozenset(getattr(model_admission, "capabilities", ()))
            if not requested_capabilities.issubset(admission_capabilities):
                continue
        profile = profile_lookup(
            binding.provider_id,
            binding.credential_binding_id,
            binding.model,
        )
        if profile is None:
            continue
        if no_charge_required and not profile.no_charge_guaranteed:
            continue
        if not binding.quota_domain:
            continue
        admitted.append((binding, qualification, profile))
    return tuple(admitted)


@dataclass(frozen=True)
class ResourcePoolRuntime:
    """Temporary Host-owned dispatch resources for one bounded shadow call."""

    ledger: ResourceLedger
    dispatcher: ProviderDispatcher
    admitted: tuple[tuple[OperationProviderBinding, Any, Any], ...]
    directory: Path


@contextmanager
def compose_resource_pool(
    admitted: Sequence[tuple[OperationProviderBinding, Any, Any]],
    *,
    resolver: QualificationResolver,
    model_admission_resolver: ModelAdmissionResolver | None = None,
    resource_id_prefix: str = "devfarm-shadow",
    provider_builder: Callable[..., ModelProvider] | None = None,
    router_factory: type[ResourceRouter] = ResourceRouter,
) -> Iterator[ResourcePoolRuntime]:
    """Compose the existing ledger/router/dispatcher for one bounded call."""

    if not admitted:
        raise ResourcePoolError("resource pool must not be empty")
    if not isinstance(resource_id_prefix, str) or not resource_id_prefix.strip():
        raise ResourcePoolError("resource_id_prefix must be non-empty")
    builder = provider_builder or build_provider

    with TemporaryDirectory(prefix=f"{resource_id_prefix}-") as directory:
        ledger = ResourceLedger(Path(directory) / "resources.sqlite3")
        try:
            concrete: list[ModelProvider] = []
            resolved_admissions: list[Any | None] = []
            for binding, qualification, _profile in admitted:
                model_admission = _resolved_model_admission(
                    binding,
                    model_admission_resolver,
                    now=None,
                )
                if (
                    model_admission is None
                    and model_admission_resolver is not None
                    and getattr(qualification, "intelligence_tier", None) != "L1"
                ):
                    # Known L1 Worker bindings may be admitted from exact,
                    # current qualification evidence even when the optional
                    # discovery/benchmark catalog has no entry for legacy
                    # resources such as the OpenRouter free lane.  Keep the
                    # stricter model-evidence requirement for L2/L3 so a
                    # Planner or Reviewer cannot silently outlive its
                    # benchmark admission window.
                    raise ResourcePoolError("model evidence expired after resource admission")
                resolved_admissions.append(model_admission)

            # A legacy L1 resource is intentionally allowed to use its exact,
            # current qualification record when no model-evidence row exists.
            # Do not pass the optional resolver to ResourceRouter in that
            # all-legacy case: the Router's strict resolver mode quite
            # correctly excludes resources with missing model evidence, which
            # would otherwise contradict the L1 admission contract above.
            # Mixed pools are rejected rather than weakening model evidence
            # checks for only part of a composed pool.
            has_legacy_l1 = any(model_admission is None for model_admission in resolved_admissions)
            has_model_evidence = any(model_admission is not None for model_admission in resolved_admissions)
            if has_legacy_l1 and has_model_evidence:
                raise ResourcePoolError("model evidence is incomplete for a mixed resource pool")
            router_model_admission_resolver = (
                None if has_legacy_l1 else model_admission_resolver
            )

            for (binding, qualification, profile), model_admission in zip(
                admitted,
                resolved_admissions,
                strict=True,
            ):
                effective_capabilities = sorted(
                    frozenset(getattr(qualification, "routing_capabilities", ()))
                    & (
                        frozenset(getattr(model_admission, "capabilities", ()))
                        if model_admission is not None
                        else frozenset(getattr(qualification, "routing_capabilities", ()))
                    )
                )
                effective_tier = (
                    model_admission.intelligence_tier
                    if model_admission is not None
                    else qualification.intelligence_tier
                )
                concrete.append(builder(binding=binding))
                resource_id = f"{resource_id_prefix}:{binding.binding_id}"
                ledger.register_resource(
                    resource_id,
                    provider_id=binding.provider_id,
                    provider_binding_id=binding.binding_id,
                    native_unit="request",
                    capacity=1,
                    capabilities=effective_capabilities,
                    sensitivity="normal",
                    cost_minor=profile.cost_minor,
                    price_currency=profile.price_currency,
                    quota_domain=binding.quota_domain,
                    intelligence_tier=effective_tier,
                    metadata={
                        "provider_binding_id": binding.binding_id,
                        "qualification_binding_id": binding.credential_binding_id,
                        "model_id": binding.model,
                        "intelligence_tier": effective_tier,
                        "privacy_profile": "remote_cloud",
                        "qualification_required": True,
                        "billing_authority": "trusted_catalog",
                        "billing_mode": profile.billing_mode,
                        "overage_policy": profile.overage_policy,
                        "no_charge_guaranteed": profile.no_charge_guaranteed,
                        "billing_expires_at": profile.expires_at,
                        "allowance_amount": profile.allowance_amount,
                        "allowance_currency": profile.allowance_currency,
                        "allowance_period": profile.allowance_period,
                    },
                )
                ledger.observe(resource_id, available=1, health="healthy", concurrency_limit=1)
            policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
            BudgetAuthority.configure(ledger, policy)
            control = ResourceControlPlane(
                router_factory(
                    ledger,
                    qualification_resolver=resolver,
                    model_admission_resolver=router_model_admission_resolver,
                ),
                BudgetGovernor(ledger, policy),
            )
            dispatcher = ProviderDispatcher(ProviderRegistry(concrete), control)
            yield ResourcePoolRuntime(ledger, dispatcher, tuple(admitted), Path(directory))
        finally:
            ledger.close()


__all__ = [
    "ResourcePoolError",
    "ResourcePoolRuntime",
    "admit_resource_pool",
    "build_provider",
    "compose_resource_pool",
    "make_binding",
    "resolve_provider_pool",
]
