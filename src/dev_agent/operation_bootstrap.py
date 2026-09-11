"""Composition helpers for opening the v2 Operation Layer.

This module owns construction and cleanup of the existing durable components.
It deliberately does not introduce another runtime or scheduler; the public
``OperationService`` remains the human-facing facade.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable

from .intelligence.coordination import EvaluationCoordinator
from .intelligence.lifecycle import TaskLifecycleCoordinator
from .providers.dispatch import ProviderDispatcher
from .providers.registry import ProviderRegistry
from .resources.budget import BudgetGovernor
from .resources.control import ResourceControlPlane
from .resources.ledger import ResourceLedger
from .resources.qualification import QualificationResolver
from .resources.router import ResourceRouter
from .runtime.controller import Controller
from .scheduler.queue import DurableQueue
from .scheduler.worker import WorkerRunner
from .state.control_repository import OperationControl
from .state.sqlite_store import SQLiteStateStore
from .tools.runtime import ToolRuntime


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
        ensure_budget(ledger)

        providers: list[Any] = []
        trusted_free_binding_present = False
        for binding in config.provider_bindings:
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
            profile = resource_profile(binding.provider_id, binding.binding_id, binding.model)
            trusted_free_binding_present = trusted_free_binding_present or bool(profile is not None and profile.cost_minor == 0)
            providers.append(provider)

        resource_control = ResourceControlPlane(
            ResourceRouter(ledger, qualification_resolver=qualification_resolver),
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
