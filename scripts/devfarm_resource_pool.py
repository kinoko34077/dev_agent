"""Shared development-only Resource composition for DevFarm model roles.

Planner and Reviewer shadow runs have the same Host-owned admission and
temporary dispatch setup.  This module is the small public boundary for that
shared knowledge; it does not own production routing, budget, or task state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any

from scripts.devfarm_errors import DevFarmError
from src.dev_agent.operation import OperationProviderBinding, configured_provider_pool_from_environment, ollama_local_routing_priority
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.billing_catalog import profile_for as default_profile_for
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.model_candidates import current_no_charge_candidate_ids, materialize_provider_bindings
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.model_catalog import ModelCatalog
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.provider_policy import privacy_profile, requires_qualification
from src.dev_agent.resources.tier_authority import production_tier_authority
from src.dev_agent.intelligence.evidence_routing import (
    EvidenceBasedRoutingPolicy,
    EvidenceCandidate,
    EvidenceRouteDecision,
    EvidenceRoutingError,
)
from scripts.devfarm_metrics import WorkerMetricsError, WorkerMetricsStore


@dataclass(frozen=True)
class _LocalQualification:
    """Conservative local capability projection for one explicit binding."""

    intelligence_tier: str | None
    routing_capabilities: frozenset[str]


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
            keep_alive=binding.keep_alive,
            think=binding.think,
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


def _current_no_charge_profile(profile: Any, *, now: datetime | None) -> bool:
    if profile is None or getattr(profile, "no_charge_guaranteed", False) is not True:
        return False
    is_current = getattr(profile, "is_current", None)
    if not callable(is_current):
        return False
    return bool(is_current() if now is None else is_current(now=now))


def admit_resource_pool(
    bindings: Sequence[OperationProviderBinding],
    *,
    resolver: QualificationResolver,
    required_tier: str = "L2",
    quality_tier_advisory: bool = False,
    required_capabilities: Sequence[str] | None = None,
    no_charge_required: bool = True,
    model_admission_resolver: ModelAdmissionResolver | None = None,
    model_catalog: Any | None = None,
    expand_discovered_models: bool = False,
    refresh_model_discovery: bool = False,
    now: datetime | None = None,
    profile_resolver: Callable[[str, str, str], Any] | None = None,
) -> tuple[tuple[OperationProviderBinding, Any, Any], ...]:
    """Admit exact current resources for any development model role.

    Qualification remains exact current execution evidence. Tier is a
    quality preference only when ``quality_tier_advisory`` is explicitly set
    for an untrusted proposal path. Billing/privacy policy remains a separate
    Host-owned filter.
    """

    if not isinstance(required_tier, str) or not required_tier.strip():
        raise ResourcePoolError("required_tier must be a non-empty string")
    if not isinstance(quality_tier_advisory, bool):
        raise ResourcePoolError("quality_tier_advisory must be a boolean")
    if not isinstance(refresh_model_discovery, bool):
        raise ResourcePoolError("refresh_model_discovery must be a boolean")
    profile_lookup = profile_resolver or default_profile_for
    requested_capabilities = frozenset(required_capabilities or ())
    if any(not isinstance(item, str) or not item.strip() for item in requested_capabilities):
        raise ResourcePoolError("required_capabilities must contain non-empty strings")

    candidate_bindings: list[OperationProviderBinding] = []
    for binding in bindings:
        evidence_catalog = model_catalog
        if evidence_catalog is None and model_admission_resolver is not None:
            evidence_catalog = getattr(model_admission_resolver, "catalog", None)
        candidate_catalog = evidence_catalog
        live_model_ids: set[str] | None = None
        if refresh_model_discovery and expand_discovered_models:
            try:
                live = ProviderModelDiscovery().discover(
                    ModelDiscoveryBinding.from_operation_binding(binding),
                    now=now,
                )
                candidate_catalog = ModelCatalog.from_document(live.to_document())
                live_model_ids = {
                    entry.model_id
                    for entry in candidate_catalog.entries_for_binding(
                        binding.provider_id,
                        binding.binding_id,
                        now=now,
                    )
                    if entry.is_text_generation_candidate()
                }
            except Exception:
                # A failed current inventory read excludes this binding only;
                # unrelated configured Providers remain available.
                continue
            if not live_model_ids:
                continue
        try:
            preferred_model_ids = (
                current_no_charge_candidate_ids(binding, candidate_catalog, profile_lookup, now=now)
                if isinstance(candidate_catalog, ModelCatalog)
                else ()
            )
            materialized = materialize_provider_bindings(
                binding,
                candidate_catalog,
                expand_discovered_models=expand_discovered_models,
                preferred_model_ids=preferred_model_ids,
                now=now,
            )
            if live_model_ids is not None:
                materialized = tuple(candidate for candidate in materialized if candidate.model in live_model_ids)
            candidate_bindings.extend(materialized)
        except (TypeError, ValueError) as exc:
            raise ResourcePoolError(f"unable to materialize model candidates: {exc}") from exc

    admitted: list[tuple[OperationProviderBinding, Any, Any]] = []
    for binding in candidate_bindings:
        if privacy_profile(binding.provider_id) == "local_only":
            profile = profile_lookup(
                binding.provider_id,
                binding.credential_binding_id,
                binding.model,
            )
            if profile is None or (no_charge_required and not profile.no_charge_guaranteed):
                continue
            tier_decision = production_tier_authority.resolve(
                provider_id=binding.provider_id,
                provider_binding_id=binding.credential_binding_id,
                model_id=binding.model,
                configured_tier=binding.intelligence_tier,
                profile=profile,
                allow_unverified_quality_proposal=quality_tier_advisory,
            )
            if not tier_decision.eligible or (
                not quality_tier_advisory and tier_decision.tier != required_tier
            ):
                continue
            local_capabilities = frozenset({"text"})
            if not requested_capabilities.issubset(local_capabilities):
                continue
            admitted.append(
                (
                    binding,
                    _LocalQualification(tier_decision.tier, local_capabilities),
                    profile,
                )
            )
            continue
        qualification_kwargs: dict[str, Any] = {"min_confidence": "high"}
        if now is not None:
            qualification_kwargs["now"] = now
        qualification = resolver.resolve(
            binding.provider_id,
            binding.credential_binding_id,
            binding.model,
            **qualification_kwargs,
        )
        profile = profile_lookup(
            binding.provider_id,
            binding.credential_binding_id,
            binding.model,
        )
        if profile is None:
            continue
        if no_charge_required and not _current_no_charge_profile(profile, now=now):
            continue
        if qualification is None:
            proposal_candidate = (
                quality_tier_advisory
                and required_tier in {"L0", "L1"}
                and requested_capabilities.issubset({"text"})
                and bool(binding.quota_domain)
                and _current_no_charge_profile(profile, now=now)
            )
            if not proposal_candidate:
                continue
            binding = replace(binding, proposal_only_candidate=True)
            admitted.append((binding, None, profile))
            continue
        model_admission = _resolved_model_admission(binding, model_admission_resolver, now=now)
        tier_decision = production_tier_authority.resolve(
            provider_id=binding.provider_id,
            provider_binding_id=binding.credential_binding_id,
            model_id=binding.model,
            configured_tier=binding.intelligence_tier,
            qualification=qualification,
            model_admission=model_admission,
            profile=profile_lookup(
                binding.provider_id,
                binding.credential_binding_id,
                binding.model,
            ),
            allow_unverified_quality_proposal=quality_tier_advisory,
        )
        if not tier_decision.eligible or (
            not quality_tier_advisory and tier_decision.tier != required_tier
        ):
            continue
        qualification_capabilities = frozenset(getattr(qualification, "routing_capabilities", ()))
        if not requested_capabilities.issubset(qualification_capabilities):
            continue
        if model_admission is not None and getattr(model_admission, "capabilities", None) is not None:
            admission_capabilities = frozenset(model_admission.capabilities)
            if not requested_capabilities.issubset(admission_capabilities):
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
    outcome_routing_status: str = "disabled"
    outcome_routing: tuple[EvidenceRouteDecision, ...] = ()


def _host_outcome_decisions(
    admitted: Sequence[tuple[OperationProviderBinding, Any, Any]],
    *,
    task_type: str | None,
    metrics_path: str | Path | None,
) -> tuple[str, tuple[EvidenceRouteDecision, ...]]:
    if task_type is None:
        return "disabled", ()
    path = (
        Path(metrics_path)
        if metrics_path is not None
        else Path(__file__).resolve().parents[1] / ".devfarm" / "metrics.sqlite3"
    )
    if not path.is_file():
        return "metrics_store_unavailable", ()
    candidates = tuple(
        EvidenceCandidate(binding.provider_id, binding.binding_id, binding.model)
        for binding, _qualification, _profile in admitted
    )
    try:
        with WorkerMetricsStore.open_read_only(path) as store:
            decisions = EvidenceBasedRoutingPolicy(
                store,
                minimum_samples=3,
                max_age_seconds=86_400,
                minimum_acceptance_rate=0.8,
            ).rank(task_type, candidates)
    except (WorkerMetricsError, EvidenceRoutingError, OSError, sqlite3.Error):
        return "metrics_store_invalid", ()
    return "evaluated", decisions


@contextmanager
def compose_resource_pool(
    admitted: Sequence[tuple[OperationProviderBinding, Any, Any]],
    *,
    resolver: QualificationResolver,
    model_admission_resolver: ModelAdmissionResolver | None = None,
    quality_tier_advisory: bool = False,
    resource_id_prefix: str = "devfarm-shadow",
    provider_builder: Callable[..., ModelProvider] | None = None,
    router_factory: type[ResourceRouter] = ResourceRouter,
    quota_observations: Mapping[str, Mapping[str, Any]] | None = None,
    task_type: str | None = None,
    outcome_metrics_path: str | Path | None = None,
) -> Iterator[ResourcePoolRuntime]:
    """Compose the existing ledger/router/dispatcher for one bounded call.

    ``quota_observations`` carries already-observed, provider-neutral quota
    evidence into the temporary ledger.  It is deliberately an explicit
    caller-owned input: this helper never invents headroom or enables
    unknown-quota routing.  The observation is still validated by
    ``ResourceLedger.ingest_quota_observation`` against the composed resource
    and therefore retains the normal quota-domain and freshness checks.
    """

    if not admitted:
        raise ResourcePoolError("resource pool must not be empty")
    if not isinstance(resource_id_prefix, str) or not resource_id_prefix.strip():
        raise ResourcePoolError("resource_id_prefix must be non-empty")
    if not isinstance(quality_tier_advisory, bool):
        raise ResourcePoolError("quality_tier_advisory must be a boolean")
    if quota_observations is not None and not isinstance(quota_observations, Mapping):
        raise ResourcePoolError("quota_observations must be a mapping or None")
    if task_type is not None and (
        not isinstance(task_type, str)
        or not task_type.strip()
        or len(task_type.strip()) > 64
    ):
        raise ResourcePoolError("task_type must be a non-empty string of at most 64 characters or None")
    if outcome_metrics_path is not None and not isinstance(outcome_metrics_path, (str, Path)):
        raise ResourcePoolError("outcome_metrics_path must be a path or None")
    task_type = task_type.strip() if isinstance(task_type, str) else None
    outcome_routing_status, outcome_routing = _host_outcome_decisions(
        admitted,
        task_type=task_type,
        metrics_path=outcome_metrics_path,
    )
    outcome_by_identity = {
        (decision.provider_id, decision.binding_id, decision.model_id): decision
        for decision in outcome_routing
    }
    outcome_rank_by_identity = {
        (decision.provider_id, decision.binding_id, decision.model_id): rank
        for rank, decision in enumerate(
            decision for decision in outcome_routing if decision.preferred
        )
    }
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
                resolved_admissions.append(model_admission)

            # Performance evidence can annotate or rank a route, but its
            # absence never overrides exact current qualification. The
            # Router enforces interface capability evidence independently.
            router_model_admission_resolver = model_admission_resolver

            for (binding, qualification, profile), model_admission in zip(
                admitted,
                resolved_admissions,
                strict=True,
            ):
                unqualified_proposal = (
                    binding.proposal_only_candidate
                    and qualification is None
                )
                capability_ceiling = (
                    frozenset({"text"})
                    if unqualified_proposal
                    else frozenset(getattr(qualification, "routing_capabilities", ()))
                )
                if model_admission is not None and getattr(model_admission, "capabilities", None) is not None:
                    capability_ceiling &= frozenset(model_admission.capabilities)
                effective_capabilities = sorted(capability_ceiling)
                tier_decision = production_tier_authority.resolve(
                    provider_id=binding.provider_id,
                    provider_binding_id=binding.credential_binding_id,
                    model_id=binding.model,
                    configured_tier=binding.intelligence_tier,
                    qualification=qualification,
                    model_admission=model_admission,
                    profile=_profile,
                    allow_unverified_quality_proposal=quality_tier_advisory,
                    allow_unqualified_proposal_candidate=unqualified_proposal,
                )
                if not tier_decision.eligible or (
                    tier_decision.tier is None and not quality_tier_advisory
                ):
                    raise ResourcePoolError("production tier evidence changed after admission")
                effective_tier = tier_decision.tier
                concrete.append(builder(binding=binding))
                resource_id = f"{resource_id_prefix}:{binding.binding_id}"
                routing_priority = ollama_local_routing_priority(binding.model) if binding.provider_id == "ollama" else None
                outcome_decision = outcome_by_identity.get(
                    (binding.provider_id, binding.binding_id, binding.model)
                )
                outcome_metadata = {}
                if outcome_decision is not None and task_type is not None:
                    outcome_metadata = {
                        "host_outcome_source": "host_verified_metrics",
                        "host_outcome_task_type": task_type,
                        "host_outcome_preferred": outcome_decision.preferred,
                        "host_outcome_rank": outcome_rank_by_identity.get(
                            (binding.provider_id, binding.binding_id, binding.model)
                        ),
                        "host_outcome_reason": outcome_decision.reason,
                        "host_outcome_sample_count": outcome_decision.sample_count,
                        "host_outcome_acceptance_rate": outcome_decision.acceptance_rate,
                        "host_outcome_average_elapsed_ms": outcome_decision.average_elapsed_ms,
                        "host_outcome_average_retry_count": outcome_decision.average_retry_count,
                        "host_outcome_latest_recorded_at": outcome_decision.latest_recorded_at,
                    }
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
                        "intelligence_tier_source": tier_decision.source,
                        "privacy_profile": privacy_profile(binding.provider_id),
                        "qualification_required": requires_qualification(binding.provider_id),
                        "proposal_only_candidate": unqualified_proposal,
                        **(
                            {"candidate_admission_warning": "QUALIFICATION_UNVERIFIED"}
                            if unqualified_proposal
                            else {}
                        ),
                        "billing_authority": "trusted_catalog",
                        "billing_mode": profile.billing_mode,
                        "overage_policy": profile.overage_policy,
                        "no_charge_guaranteed": profile.no_charge_guaranteed,
                        "billing_expires_at": profile.expires_at,
                        "allowance_amount": profile.allowance_amount,
                        "allowance_currency": profile.allowance_currency,
                        "allowance_period": profile.allowance_period,
                        **outcome_metadata,
                        **({"routing_priority": routing_priority} if routing_priority is not None else {}),
                    },
                )
                ledger.observe(resource_id, available=1, health="healthy", concurrency_limit=1)
                if quota_observations is not None and resource_id in quota_observations:
                    observation = quota_observations[resource_id]
                    if not isinstance(observation, Mapping):
                        raise ResourcePoolError(f"quota observation for {resource_id} must be a mapping")
                    accepted = ledger.ingest_quota_observation(
                        resource_id,
                        {"quota_observation": dict(observation)},
                        observed_at=observation.get("observed_at") if isinstance(observation.get("observed_at"), str) else None,
                        source=observation.get("source", "admission-observation") if isinstance(observation.get("source", "admission-observation"), str) else "admission-observation",
                    )
                    if not accepted:
                        raise ResourcePoolError(f"invalid quota observation for {resource_id}")
            policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
            BudgetAuthority.configure(ledger, policy)
            control = ResourceControlPlane(
                router_factory(
                    ledger,
                    qualification_resolver=resolver,
                    model_admission_resolver=router_model_admission_resolver,
                    proposal_candidate_identities={
                        (binding.provider_id, binding.credential_binding_id, binding.model)
                        for binding, qualification, _profile in admitted
                        if binding.proposal_only_candidate and qualification is None
                    },
                ),
                BudgetGovernor(ledger, policy),
            )
            dispatcher = ProviderDispatcher(ProviderRegistry(concrete), control)
            yield ResourcePoolRuntime(
                ledger,
                dispatcher,
                tuple(admitted),
                Path(directory),
                outcome_routing_status,
                outcome_routing,
            )
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
