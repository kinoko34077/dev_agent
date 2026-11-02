"""Composition helpers for opening the v2 Operation Layer.

This module owns construction and cleanup of the existing durable components.
It deliberately does not introduce another runtime or scheduler; the public
``OperationService`` remains the human-facing facade.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
import logging
from typing import Any, Callable

from .intelligence.coordination import EvaluationCoordinator
from .intelligence.lifecycle import TaskLifecycleCoordinator
from .providers.dispatch import ProviderDispatcher
from .providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from .providers.registry import ProviderRegistry
from .resources.budget import BudgetGovernor
from .resources.control import ResourceControlPlane
from .resources.ledger import ResourceLedger
from .resources.model_admission import ModelAdmissionResolver
from .resources.model_candidates import materialize_provider_bindings
from .resources.model_catalog import ModelCatalog
from .resources.model_evidence import ModelEvidenceCatalog
from .resources.provider_policy import requires_qualification
from .resources.qualification import QualificationResolver
from .resources.router import ResourceRouter
from .runtime.controller import Controller
from .scheduler.queue import DurableQueue
from .scheduler.worker import WorkerRunner
from .state.control_repository import OperationControl
from .state.sqlite_store import SQLiteStateStore
from .tools.runtime import ToolRuntime


_LOGGER = logging.getLogger(__name__)


def _current_no_charge_profile(profile: Any) -> bool:
    return bool(
        profile is not None
        and getattr(profile, "no_charge_guaranteed", False) is True
        and callable(getattr(profile, "is_current", None))
        and profile.is_current()
    )


@dataclass(frozen=True)
class ProviderCandidateAdvisory:
    """Bounded operator-visible warning tied to one exact configured route."""

    provider_id: str
    binding_id: str
    model_id: str
    warning_category: str
    blocks_dispatch: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "binding_id": self.binding_id,
            "model_id": self.model_id,
            "warning_category": self.warning_category,
            "blocks_dispatch": self.blocks_dispatch,
        }


@dataclass(frozen=True)
class OperationComponents:
    """Already-composed runtime parts handed to ``OperationService``."""

    store: SQLiteStateStore
    queue: DurableQueue
    ledger: Any
    control: OperationControl
    controller: Controller
    worker: WorkerRunner
    dispatcher: ProviderDispatcher
    evaluation: EvaluationCoordinator
    lifecycle: TaskLifecycleCoordinator
    qualification_resolver: QualificationResolver
    model_admission_resolver: ModelAdmissionResolver | None
    provider_candidate_advisories: tuple[ProviderCandidateAdvisory, ...] = ()


def open_components(
    config: Any,
    *,
    build_provider: Callable[..., Any],
    ensure_resource: Callable[..., None],
    ensure_budget: Callable[[Any], None],
    resource_profile: Callable[[str, str, str], Any],
    tool_registry_factory: Callable[[], Any],
    wake_provider_capacity: Callable[[DurableQueue, str | None], None],
) -> OperationComponents:
    """Open the existing Operation dependencies with one qualification view.

    ``build_provider`` and ``ensure_resource`` are injected from the facade so
    established embedding/test overrides keep their compatibility signatures.
    The function owns only composition and cleanup; policy remains in the
    existing components and callbacks.
    """

    store = queue = ledger = control = None
    try:
        store = SQLiteStateStore(config.state_path)
        queue = DurableQueue(config.queue_path)
        ledger = ResourceLedger(config.resources_path)
        control = OperationControl(config.queue_path)
        qualification_resolver = QualificationResolver()
        model_admission_resolver = None
        model_catalog = None
        model_evidence_directory = getattr(config, "model_evidence_directory", None)
        configured_bindings = tuple(config.provider_bindings)
        dynamic_discovery_requested = any(
            bool(getattr(binding, "expand_discovered_models", False))
            or bool(getattr(binding, "require_current_model_discovery", False))
            for binding in configured_bindings
        )
        if model_evidence_directory is not None:
            model_evidence = ModelEvidenceCatalog.load(model_evidence_directory)
            model_catalog = model_evidence.catalog
            model_admission_resolver = model_evidence.resolver
        elif dynamic_discovery_requested:
            # Dynamic configured bindings opt into the reviewed repository
            # catalog for candidate identity only. Discovery still grants no
            # routing authority: exact billing, privacy, quota and health
            # gates remain mandatory; qualification may be advisory only on
            # proposal-only text requests.
            model_catalog = ModelEvidenceCatalog.load_default().catalog
        ensure_budget(ledger)

        runtime_bindings: list[Any] = []
        candidate_advisories: dict[tuple[str, str, str, str], ProviderCandidateAdvisory] = {}

        def record_candidate_advisory(
            provider_id: str,
            binding_id: str,
            model_id: str,
            warning_category: str,
            *,
            blocks_dispatch: bool,
        ) -> None:
            key = (provider_id, binding_id, model_id, warning_category)
            candidate_advisories[key] = ProviderCandidateAdvisory(
                provider_id=provider_id,
                binding_id=binding_id,
                model_id=model_id,
                warning_category=warning_category,
                blocks_dispatch=blocks_dispatch,
            )

        proposal_candidate_identities: set[tuple[str, str, str]] = set()
        for binding in configured_bindings:
            requires_current_discovery = bool(
                getattr(binding, "require_current_model_discovery", False)
            )
            expands_discovered_models = bool(
                getattr(binding, "expand_discovered_models", False)
            )
            if not requires_current_discovery and not expands_discovered_models:
                runtime_bindings.append(binding)
                continue

            candidate_catalog = model_catalog
            # Refresh the factual candidate set at Operation startup through
            # the common discovery contract. A live model list grants no
            # execution authority; every row still passes exact
            # qualification/billing/quota/health gates below. A failed
            # refresh leaves the exact configured identity visible with a
            # blocking advisory; stale catalog entries cannot substitute.
            try:
                live = ProviderModelDiscovery().discover(
                    ModelDiscoveryBinding.from_operation_binding(binding)
                )
                candidate_catalog = ModelCatalog.from_document(live.to_document())
            except Exception:
                # Preserve the exact configured identity for operator
                # visibility, but do not dispatch it without current
                # inventory confirmation. Quality uncertainty can be
                # advisory for bounded proposals; identity uncertainty cannot.
                candidate_catalog = None
                record_candidate_advisory(
                    binding.provider_id,
                    binding.binding_id,
                    binding.model,
                    "MODEL_DISCOVERY_UNVERIFIED",
                    blocks_dispatch=True,
                )
                _LOGGER.warning(
                    "configured provider candidate retained as non-dispatchable because current model discovery failed",
                    extra={
                        "provider_id": binding.provider_id,
                        "binding_id": binding.binding_id,
                        "warning_category": "MODEL_DISCOVERY_UNVERIFIED",
                    },
                )
            if candidate_catalog is None:
                continue
            for candidate in materialize_provider_bindings(
                binding,
                candidate_catalog,
                expand_discovered_models=expands_discovered_models,
            ):
                # Discovery/static catalog identity may differ from the
                # qualification/billing lane (for example OpenRouter's
                # account catalog versus its reviewed free lane).  Keep both
                # identities explicit instead of making one stand in for the
                # other.
                catalog_binding_id = binding.binding_id
                qualification_binding_id = candidate.credential_binding_id
                # The seed model must not bypass current discovery merely
                # because it is present in configuration.
                discovered_candidate = candidate_catalog.lookup(
                    candidate.provider_id,
                    catalog_binding_id,
                    candidate.model,
                )
                profile = resource_profile(
                    candidate.provider_id,
                    qualification_binding_id,
                    candidate.model,
                )
                if not _current_no_charge_profile(profile):
                    record_candidate_advisory(
                        candidate.provider_id,
                        candidate.binding_id,
                        candidate.model,
                        "BILLING_UNVERIFIED",
                        blocks_dispatch=True,
                    )
                    _LOGGER.warning(
                        "provider model candidate retained in configuration but not dispatchable without exact current no-charge billing evidence",
                        extra={
                            "provider_id": candidate.provider_id,
                            "binding_id": candidate.binding_id,
                            "model_id": candidate.model,
                            "warning_category": "BILLING_UNVERIFIED",
                        },
                    )
                    continue
                if discovered_candidate is not None and not discovered_candidate.is_text_generation_candidate():
                    continue
                if discovered_candidate is None:
                    # A successful inventory that omits an exact configured
                    # pin is affirmative evidence that the route is stale or
                    # unavailable. Keep it visible, but fail closed for this
                    # route instead of guessing or silently substituting.
                    if candidate.model != binding.model:
                        continue
                    record_candidate_advisory(
                        candidate.provider_id,
                        candidate.binding_id,
                        candidate.model,
                        "MODEL_NOT_IN_CURRENT_INVENTORY",
                        blocks_dispatch=True,
                    )
                    _LOGGER.warning(
                        "configured exact model is not present in current provider inventory",
                        extra={
                            "provider_id": candidate.provider_id,
                            "binding_id": candidate.binding_id,
                            "model_id": candidate.model,
                            "warning_category": "MODEL_NOT_IN_CURRENT_INVENTORY",
                        },
                    )
                    continue
                qualification = qualification_resolver.resolve(
                    candidate.provider_id,
                    qualification_binding_id,
                    candidate.model,
                )
                if qualification is None:
                    candidate = replace(candidate, proposal_only_candidate=True)
                    proposal_candidate_identities.add(
                        (candidate.provider_id, candidate.credential_binding_id, candidate.model)
                    )
                    record_candidate_advisory(
                        candidate.provider_id,
                        candidate.binding_id,
                        candidate.model,
                        "QUALIFICATION_UNVERIFIED",
                        blocks_dispatch=False,
                    )
                    _LOGGER.warning(
                        "provider candidate retained for text proposals with unverified qualification",
                        extra={
                            "provider_id": candidate.provider_id,
                            "binding_id": candidate.binding_id,
                            "model_id": candidate.model,
                            "warning_category": "QUALIFICATION_UNVERIFIED",
                        },
                    )
                # Static benchmark/alias/capability snapshots enrich routing
                # quality and narrow capabilities when current. They do not
                # duplicate the fresh Provider inventory, exact qualification,
                # and billing checks above as a second blanket admission gate.
                runtime_bindings.append(candidate)

        # Exact operator-selected remote pins that do not request dynamic
        # model discovery use the same low-tier proposal rule. A current exact
        # no-charge profile remains mandatory; absence of quality evidence is
        # surfaced as a warning instead of dropping the only candidate.
        normalized_bindings: list[Any] = []
        for binding in runtime_bindings:
            if not requires_qualification(binding.provider_id):
                normalized_bindings.append(binding)
                continue
            profile = resource_profile(
                binding.provider_id,
                binding.credential_binding_id,
                binding.model,
            )
            if not _current_no_charge_profile(profile):
                record_candidate_advisory(
                    binding.provider_id,
                    binding.binding_id,
                    binding.model,
                    "BILLING_UNVERIFIED",
                    blocks_dispatch=True,
                )
                _LOGGER.warning(
                    "provider candidate unavailable because exact current no-charge billing evidence is missing",
                    extra={
                        "provider_id": binding.provider_id,
                        "binding_id": binding.binding_id,
                        "model_id": binding.model,
                        "warning_category": "BILLING_UNVERIFIED",
                    },
                )
                continue
            qualification = qualification_resolver.resolve(
                binding.provider_id,
                binding.credential_binding_id,
                binding.model,
            )
            if qualification is None and not getattr(binding, "proposal_only_candidate", False):
                binding = replace(binding, proposal_only_candidate=True)
                record_candidate_advisory(
                    binding.provider_id,
                    binding.binding_id,
                    binding.model,
                    "QUALIFICATION_UNVERIFIED",
                    blocks_dispatch=False,
                )
                _LOGGER.warning(
                    "explicit provider candidate retained for text proposals with unverified qualification",
                    extra={
                        "provider_id": binding.provider_id,
                        "binding_id": binding.binding_id,
                        "model_id": binding.model,
                        "warning_category": "QUALIFICATION_UNVERIFIED",
                    },
                )
            normalized_bindings.append(binding)
            if getattr(binding, "proposal_only_candidate", False):
                proposal_candidate_identities.add(
                    (binding.provider_id, binding.credential_binding_id, binding.model)
                )
        runtime_bindings = normalized_bindings

        providers: list[Any] = []
        trusted_free_binding_present = False
        for binding in runtime_bindings:
            try:
                accepts_resolver = "qualification_resolver" in inspect.signature(build_provider).parameters
            except (TypeError, ValueError):
                accepts_resolver = False
            provider = (
                build_provider(binding, qualification_resolver=qualification_resolver)
                if accepts_resolver
                else build_provider(binding)
            )
            ensure_resource(ledger, provider, binding, qualification_resolver=qualification_resolver)
            profile = resource_profile(
                binding.provider_id,
                binding.credential_binding_id,
                binding.model,
            )
            trusted_free_binding_present = trusted_free_binding_present or bool(
                profile is not None and profile.no_charge_guaranteed and profile.is_current()
            )
            providers.append(provider)

        resource_control = ResourceControlPlane(
            ResourceRouter(
                ledger,
                qualification_resolver=qualification_resolver,
                model_admission_resolver=model_admission_resolver,
                proposal_candidate_identities=proposal_candidate_identities,
            ),
            BudgetGovernor(ledger),
        )
        dispatcher = ProviderDispatcher(ProviderRegistry(providers), resource_control)
        intelligence_routing = config.provider_pool is not None or config.provider_id != "fake"
        controller = Controller(
            dispatcher,
            ToolRuntime(tool_registry_factory()),
            store,
            resource_policy=resource_control,
            intelligence_routing=intelligence_routing,
            allow_unknown_quota=trusted_free_binding_present,
            provider_capacity_wakeup=lambda binding_id: wake_provider_capacity(queue, binding_id),
        )
        worker = WorkerRunner(
            queue,
            controller,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
        )
        return OperationComponents(
            store=store,
            queue=queue,
            ledger=ledger,
            control=control,
            controller=controller,
            worker=worker,
            dispatcher=dispatcher,
            evaluation=EvaluationCoordinator(store),
            lifecycle=TaskLifecycleCoordinator(store),
            qualification_resolver=qualification_resolver,
            model_admission_resolver=model_admission_resolver,
            provider_candidate_advisories=tuple(
                candidate_advisories[key] for key in sorted(candidate_advisories)
            ),
        )
    except Exception:
        if control is not None:
            control.close()
        if ledger is not None:
            ledger.close()
        if queue is not None:
            queue.close()
        if store is not None:
            store.close()
        raise
