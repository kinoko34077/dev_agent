"""Small human-facing Operation Layer for the v2 control plane.

This module is intentionally a composition boundary, not another runtime.  It
opens the existing durable state, queue, resource control, Dispatcher, and
WorkerRunner, then exposes only the first operational commands needed to use
them from a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from threading import Event, RLock
from typing import Any, Callable, Mapping

from ._sqlite import connect
from .domain.protocol import Task, TaskStatus
from .providers.dispatch import ProviderDispatcher
from .providers.factory import ProviderDefinition, ProviderFactory
from .providers.fake import FakeProvider
from .providers.registry import ProviderRegistry
from .resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from .resources.billing_catalog import (
    TRUSTED_RESOURCE_CATALOG,
    TrustedResourceProfile,
    default_binding_id as _catalog_default_binding_id,
    profile_for as _catalog_profile_for,
)
from .resources.control import ResourceControlPlane
from .resources.ledger import ResourceLedger
from .resources.router import ResourceRouter
from .scheduler.queue import DurableQueue
from .scheduler.quota import QuotaRequalificationCoordinator, QuotaWakeScheduler
from .scheduler.worker import WorkerRunner
from .state.sqlite_store import SQLiteStateStore
from .tools.registry import ToolRegistry, ToolSpec
from .tools.runtime import ToolRuntime
from .runtime.controller import Controller


class OperationError(RuntimeError):
    """A user-facing Operation Layer error."""


_OperationResourceProfile = TrustedResourceProfile
_OPERATION_RESOURCE_CATALOG = TRUSTED_RESOURCE_CATALOG


def _default_binding_id(provider_id: str, model: str) -> str:
    """Return a known binding only for an exact catalog entry."""

    return _catalog_default_binding_id(provider_id, model)


def _operation_resource_profile(provider_id: str, binding_id: str, model_id: str) -> _OperationResourceProfile | None:
    return _catalog_profile_for(provider_id, binding_id, model_id)


def _positive_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


@dataclass(frozen=True)
class OperationConfig:
    """Non-secret settings for the local Operation Layer.

    The default provider is the deterministic ``fake`` adapter so invoking a
    local smoke command cannot unexpectedly spend quota or money.  A real
    adapter is selected explicitly with ``DEV_AGENT_PROVIDER`` and
    ``DEV_AGENT_MODEL`` or the corresponding CLI flags.
    """

    data_dir: Path = field(default_factory=lambda: Path(".dev_agent"))
    provider_id: str = "fake"
    model: str = "deterministic"
    provider_binding_id: str | None = None
    quota_domain: str | None = None
    intelligence_tier: str | None = None
    worker_id: str = field(default_factory=lambda: f"operation-{os.getpid()}")
    lease_seconds: float = 30.0
    idle_sleep_seconds: float = 1.0

    def __post_init__(self) -> None:
        data_dir = Path(self.data_dir).expanduser()
        if not str(data_dir).strip():
            raise ValueError("data_dir must not be empty")
        provider_id = self.provider_id.strip().lower() if isinstance(self.provider_id, str) else ""
        model = self.model.strip() if isinstance(self.model, str) else ""
        worker_id = self.worker_id.strip() if isinstance(self.worker_id, str) else ""
        binding = self.provider_binding_id.strip() if isinstance(self.provider_binding_id, str) and self.provider_binding_id.strip() else None
        quota_domain = self.quota_domain.strip() if isinstance(self.quota_domain, str) and self.quota_domain.strip() else None
        tier = self.intelligence_tier.strip() if isinstance(self.intelligence_tier, str) and self.intelligence_tier.strip() else None
        if not provider_id:
            raise ValueError("provider_id must be a non-empty string")
        if not model:
            raise ValueError("model must be a non-empty string")
        if not worker_id:
            raise ValueError("worker_id must be a non-empty string")
        if tier is not None and tier not in {"L0", "L1", "L2", "L3"}:
            raise ValueError("intelligence_tier must be one of L0, L1, L2, or L3")
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "provider_binding_id", binding)
        object.__setattr__(self, "quota_domain", quota_domain)
        object.__setattr__(self, "intelligence_tier", tier)
        object.__setattr__(self, "worker_id", worker_id)
        object.__setattr__(self, "lease_seconds", _positive_number(self.lease_seconds, "lease_seconds"))
        object.__setattr__(self, "idle_sleep_seconds", _positive_number(self.idle_sleep_seconds, "idle_sleep_seconds"))

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.sqlite3"

    @property
    def queue_path(self) -> Path:
        # StateStore.commit_transition performs the final lease-proof check
        # against queue_items when both durable components share a database.
        # Keep that safety boundary intact for the human-facing runtime.
        return self.state_path

    @property
    def resources_path(self) -> Path:
        return self.data_dir / "resources.sqlite3"

    @property
    def binding_id(self) -> str:
        return self.provider_binding_id or _default_binding_id(self.provider_id, self.model)

    @classmethod
    def from_environment(cls, *, data_dir: str | Path | None = None, provider_id: str | None = None, model: str | None = None, provider_binding_id: str | None = None, quota_domain: str | None = None, worker_id: str | None = None, lease_seconds: float | None = None, idle_sleep_seconds: float | None = None) -> "OperationConfig":
        def env(name: str) -> str | None:
            value = os.environ.get(name)
            return value.strip() if isinstance(value, str) and value.strip() else None

        def env_float(name: str, fallback: float) -> float:
            value = env(name)
            if value is None:
                return fallback
            try:
                return float(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be numeric") from exc

        return cls(
            data_dir=Path(data_dir or env("DEV_AGENT_DATA_DIR") or ".dev_agent"),
            provider_id=provider_id or env("DEV_AGENT_PROVIDER") or "fake",
            model=model or env("DEV_AGENT_MODEL") or "deterministic",
            provider_binding_id=provider_binding_id or env("DEV_AGENT_PROVIDER_BINDING_ID"),
            quota_domain=quota_domain or env("DEV_AGENT_QUOTA_DOMAIN"),
            intelligence_tier=env("DEV_AGENT_INTELLIGENCE_TIER"),
            worker_id=worker_id or env("DEV_AGENT_WORKER_ID") or f"operation-{os.getpid()}",
            lease_seconds=lease_seconds if lease_seconds is not None else env_float("DEV_AGENT_LEASE_SECONDS", 30.0),
            idle_sleep_seconds=idle_sleep_seconds if idle_sleep_seconds is not None else env_float("DEV_AGENT_IDLE_SLEEP_SECONDS", 1.0),
        )


class OperationControl:
    """Durable stop signal shared by separate ``start`` and ``stop`` calls."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS operation_control (
        id INTEGER PRIMARY KEY CHECK (id=1),
        stop_requested INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect(self.path)
        self._lock = RLock()
        self.connection.executescript(self._SCHEMA)
        self.connection.execute("INSERT OR IGNORE INTO operation_control(id, stop_requested) VALUES (1, 0)")
        self.connection.commit()

    def request_stop(self) -> None:
        with self._lock:
            self.connection.execute("UPDATE operation_control SET stop_requested=1 WHERE id=1")
            self.connection.commit()

    def clear_stop(self) -> None:
        with self._lock:
            self.connection.execute("UPDATE operation_control SET stop_requested=0 WHERE id=1")
            self.connection.commit()

    def stop_requested(self) -> bool:
        with self._lock:
            row = self.connection.execute("SELECT stop_requested FROM operation_control WHERE id=1").fetchone()
            return bool(row and row[0])

    def close(self) -> None:
        with self._lock:
            self.connection.close()


def _echo(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"echo": arguments["value"]}


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Return a supplied value without side effects.",
            handler=_echo,
            required_arguments=frozenset({"value"}),
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {"echo": {"type": "string"}}, "required": ["echo"], "additionalProperties": False},
        )
    )
    return registry


def _inferred_tier(config: OperationConfig) -> str | None:
    if config.intelligence_tier:
        return config.intelligence_tier
    model = config.model.lower()
    if "flash-lite" in model:
        return "L1"
    if "3.8-flash" in model or "3.7-flash" in model:
        return "L2"
    if config.provider_id == "fake":
        return "L1"
    return None


class OperationService:
    """Compose existing v2 components for the minimum operational commands."""

    def __init__(self, config: OperationConfig, *, store: SQLiteStateStore, queue: DurableQueue, ledger: ResourceLedger, control: OperationControl, controller: Controller, worker: WorkerRunner) -> None:
        self.config = config
        self.store = store
        self.queue = queue
        self.ledger = ledger
        self.control = control
        self.controller = controller
        self.worker = worker
        self._closed = False

    @classmethod
    def open(cls, config: OperationConfig | None = None) -> "OperationService":
        config = config or OperationConfig.from_environment()
        store = queue = ledger = control = None
        try:
            store = SQLiteStateStore(config.state_path)
            queue = DurableQueue(config.queue_path)
            ledger = ResourceLedger(config.resources_path)
            control = OperationControl(config.queue_path)
            cls._ensure_budget(ledger)
            provider = cls._build_provider(config)
            cls._ensure_resource(ledger, provider, config)
            resource_control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
            dispatcher = ProviderDispatcher(ProviderRegistry([provider]), resource_control)
            controller = Controller(
                dispatcher,
                ToolRuntime(_tool_registry()),
                store,
                resource_policy=resource_control,
            )
            worker = WorkerRunner(
                queue,
                controller,
                worker_id=config.worker_id,
                lease_seconds=config.lease_seconds,
            )
            return cls(config, store=store, queue=queue, ledger=ledger, control=control, controller=controller, worker=worker)
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

    @staticmethod
    def _ensure_budget(ledger: ResourceLedger) -> None:
        try:
            ledger.budget_config()
        except ValueError as exc:
            if str(exc) != "budget is not configured":
                raise
            # Operation Layer defaults to a free-only protected cap.  This is
            # an explicit initialisation of an empty local ledger, not a
            # runtime budget override; an existing persisted policy is never
            # changed by opening the service.
            BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0, currency="JPY"))

    @staticmethod
    def _build_provider(config: OperationConfig):
        if config.provider_id == "fake":
            provider = FakeProvider()
            provider_binding_id = config.binding_id
            setattr(provider, "provider_binding_id", provider_binding_id)
            setattr(provider, "model_id", config.model)
            setattr(provider, "intelligence_tier", _inferred_tier(config))
            return provider
        definition = ProviderDefinition(
            provider_id=config.provider_id,
            model=config.model,
            provider_binding_id=config.binding_id,
            intelligence_tier=_inferred_tier(config),
        )
        return ProviderFactory().create(definition)

    @staticmethod
    def _ensure_resource(ledger: ResourceLedger, provider: Any, config: OperationConfig) -> None:
        binding_id = getattr(provider, "provider_binding_id", None) or config.binding_id
        model_id = getattr(provider, "model_id", None) or config.model
        profile = _operation_resource_profile(config.provider_id, binding_id, model_id)
        tier = getattr(provider, "intelligence_tier", None) or (profile.intelligence_tier if profile else None) or _inferred_tier(config)
        try:
            existing = ledger.get_resource(binding_id)
        except KeyError:
            existing = None
        if existing is not None:
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            existing_model = metadata.get("model_id")
            existing_binding = existing.get("provider_binding_id") or metadata.get("provider_binding_id")
            if existing["provider_id"] != config.provider_id or existing_binding != binding_id or (existing_model is not None and existing_model != model_id):
                raise OperationError(
                    f"resource binding already belongs to another provider/model: {binding_id}"
                )
            existing_domain = existing.get("quota_domain")
            if config.quota_domain is not None and existing_domain not in {None, config.quota_domain}:
                raise OperationError(f"resource quota_domain is operator-owned and differs: {binding_id}")
            if profile is not None and profile.quota_required and not existing_domain:
                # Opening the service is not an administrative catalog
                # migration.  Do not silently leave a known cloud binding
                # without its account/project quota domain just because the
                # caller supplied one in the current environment; an operator
                # must repair the persisted resource explicitly.
                raise OperationError(f"existing resource requires an operator quota_domain configuration: {binding_id}")
            if profile is not None and existing_model is None:
                # A binding without a persisted model identity cannot prove
                # that its price belongs to this exact catalog entry.  Do not
                # reinterpret a historical provider-level free row as a
                # model-qualified free resource during normal startup.
                raise OperationError(
                    f"existing resource model identity is not trusted for binding/model: {binding_id}/{model_id}"
                )
            if profile is None and existing.get("cost_minor") == 0:
                # A historical/provider-name bootstrap may have marked an
                # unqualified model as free.  Preserve the record for an
                # explicit admin repair, but never let normal Operation use a
                # zero-cost assumption that is not backed by the exact
                # binding/model catalog.
                raise OperationError(
                    f"existing resource billing metadata is not trusted for binding/model: {binding_id}/{model_id}"
                )
            # Existing catalog, pricing, quota, health, and operator metadata
            # are authoritative.  Opening Operation must never upsert them.
            return
        if profile is not None and profile.quota_required and not config.quota_domain:
            raise OperationError(f"quota_domain is required for cloud resource binding: {binding_id}")
        # Unknown or potentially paid binding/model pairs deliberately keep
        # an unknown price.  They cannot be silently treated as free.
        cost_minor = profile.cost_minor if profile is not None else None
        price_currency = profile.price_currency if profile is not None else None
        ledger.register_resource(
            binding_id,
            provider_id=config.provider_id,
            provider_binding_id=binding_id,
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            sensitivity="normal",
            cost_minor=cost_minor,
            price_currency=price_currency,
            quota_domain=config.quota_domain,
            metadata={"provider_binding_id": binding_id, "model_id": model_id, "intelligence_tier": tier} if tier else {"provider_binding_id": binding_id, "model_id": model_id},
            intelligence_tier=tier,
        )
        if existing is None:
            # Catalog registration is not a provider health probe.  A remote
            # binding with a quota domain remains unroutable until a fresh
            # quota observation exists; local/fake bindings without quota can
            # use this degraded bootstrap row for their first bounded request.
            ledger.observe(binding_id, available=1, health="degraded", confidence=0.0, concurrency_limit=1)

    @staticmethod
    def submit(config: OperationConfig | None, objective: str, *, priority: int = 0, sensitivity: str = "normal") -> Task:
        config = config or OperationConfig.from_environment()
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be a non-empty string")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        store = SQLiteStateStore(config.state_path)
        queue = DurableQueue(config.queue_path)
        try:
            task = Task(objective=objective.strip(), sensitivity=sensitivity)
            store.save_task(task)
            try:
                queue.enqueue(task.task_id, priority=priority, max_attempts=task.limits.max_retries + 1)
            except Exception:
                # The durable Task is intentionally retained.  ``start``
                # repairs this exact partial-submit state before claiming.
                raise
            return task
        finally:
            queue.close()
            store.close()

    @staticmethod
    def read_status(config: OperationConfig | None, task_id: str) -> dict[str, Any]:
        config = config or OperationConfig.from_environment()
        store = SQLiteStateStore(config.state_path)
        queue = DurableQueue(config.queue_path)
        try:
            return OperationService._status(store, queue, task_id)
        finally:
            queue.close()
            store.close()

    @staticmethod
    def _status(store: SQLiteStateStore, queue: DurableQueue, task_id: str) -> dict[str, Any]:
        task = store.load_task(task_id)
        if task is None:
            raise OperationError(f"task not found: {task_id}")
        try:
            item = queue.snapshot(task_id)
        except KeyError:
            item = None
        snapshot = store.snapshot()
        events = [event for event in snapshot.get("events", []) if event.get("task_id") == task_id]
        last_event = events[-1] if events else None
        audits = store.list_provider_audits(task_id=task_id)
        latest_audit = audits[-1] if audits else None
        provider_id = latest_audit.get("provider_id") if latest_audit else None
        details = latest_audit.get("details") if latest_audit and isinstance(latest_audit.get("details"), dict) else {}
        selected_binding = details.get("provider_binding_id")
        selected_model = details.get("model_id")
        if last_event and last_event.get("event_type") == "model.responded":
            response = last_event.get("payload", {}).get("response", {})
            provider_id = provider_id or response.get("provider")
            selected_model = selected_model or response.get("model")
        state = task.status.value
        waiting = state in {TaskStatus.WAITING_DEPENDENCY.value, TaskStatus.WAITING_APPROVAL.value, TaskStatus.WAITING_RECONCILIATION.value, TaskStatus.BLOCKED_QUOTA.value, TaskStatus.BLOCKED_BUDGET.value}
        return {
            "task_id": task.task_id,
            "state": state,
            "queue_state": item.state if item is not None else None,
            "current_attempt": item.attempts if item is not None else 0,
            "active": state in {TaskStatus.QUEUED.value, TaskStatus.PLANNING.value, TaskStatus.READY.value, TaskStatus.RUNNING.value},
            "waiting": waiting,
            "completed": state == TaskStatus.COMPLETED.value,
            "failed": state == TaskStatus.FAILED.value,
            "reconciliation": state == TaskStatus.WAITING_RECONCILIATION.value,
            "selected_provider": provider_id,
            "selected_binding": selected_binding,
            "selected_model": selected_model,
            "last_event": OperationService._event_summary(last_event),
            "cancellation_requested": bool(task.metadata.get("cancellation_requested")),
        }

    @staticmethod
    def _event_summary(event: dict[str, Any] | None) -> dict[str, Any] | None:
        if event is None:
            return None
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        summary = {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "timestamp": event.get("timestamp"),
            "request_id": event.get("request_id"),
        }
        for key in ("category", "cause", "decision", "source", "status", "cancellation_state", "dispatch_id", "plan_id"):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None and key in {"category", "cause"}:
                if value is not None:
                    summary[key] = value
        return summary

    def restore_queue(self) -> list[str]:
        """Repair durable queued Tasks whose queue insert was interrupted."""
        repaired: list[str] = []
        tasks = self.store.snapshot().get("tasks", {})
        for task_id, payload in tasks.items():
            if payload.get("status") != TaskStatus.QUEUED.value:
                continue
            try:
                self.queue.snapshot(task_id)
            except KeyError:
                limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
                max_attempts = int(limits.get("max_retries", 2)) + 1
                self.queue.enqueue(task_id, max_attempts=max_attempts)
                repaired.append(task_id)
        return repaired

    def request_stop(self) -> None:
        self.control.request_stop()

    def maintenance_tick(
        self,
        *,
        now: datetime | None = None,
        max_probes: int = 1,
        probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one bounded quota maintenance pass at the operation boundary.

        The existing wake scheduler supplies only due quota domains.  This
        method supplies the missing operational composition: at most
        ``max_probes`` due resources are sent through the existing one-shot
        requalification coordinator.  A caller may provide the adapter's
        provider-neutral probe callback; when omitted, an adapter exposing a
        compatible ``probe_quota(resource_id, quota_domain)`` method is used.
        No callback means no external request is made.
        """
        if isinstance(max_probes, bool) or not isinstance(max_probes, int) or max_probes < 0:
            raise ValueError("max_probes must be a non-negative integer")
        if probe is not None and not callable(probe):
            raise TypeError("probe must be callable or None")
        current = now or datetime.now(timezone.utc)
        scheduler = QuotaWakeScheduler(self.ledger, self.queue)
        coordinator = QuotaRequalificationCoordinator(self.ledger, scheduler)
        results: list[dict[str, Any]] = []
        if max_probes == 0:
            return results
        for domain in scheduler.due_domains(now=current):
            observations = self.ledger.list_quota_observations(quota_domain=domain)
            for observation in observations:
                if len(results) >= max_probes:
                    return results
                resource_id = observation.get("resource_id")
                if not isinstance(resource_id, str) or not resource_id.strip():
                    continue
                callback = probe or self._provider_quota_probe(resource_id.strip())
                if callback is None:
                    continue
                result = coordinator.probe_once(
                    resource_id.strip(),
                    callback,
                    now=current,
                )
                results.append(result.to_dict())
        return results

    def _provider_quota_probe(self, resource_id: str) -> Callable[[str, str], Mapping[str, Any]] | None:
        """Resolve an optional provider-neutral probe without provider branches."""
        registry = getattr(self.controller.provider, "registry", None)
        if registry is None:
            return None
        resource = self.ledger.get_resource(resource_id)
        binding_id = resource.get("provider_binding_id") or resource.get("provider_id")
        if not isinstance(binding_id, str) or not binding_id.strip():
            return None
        provider = registry.get_binding(binding_id.strip())
        callback = getattr(provider, "probe_quota", None)
        if not callable(callback):
            return None
        return callback

    def start(
        self,
        *,
        once: bool = False,
        stop_event: Event | None = None,
        quota_probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ) -> Task | dict[str, Any] | None:
        """Run one claim or an idle-light foreground Worker loop.

        ``stop`` only sets the durable stop flag.  A current WorkerRunner
        invocation is allowed to finish its existing Controller boundary;
        unknown external outcomes are never converted into a synthetic task
        failure by this loop.
        """
        if self._closed:
            raise OperationError("operation service is closed")
        stop_event = stop_event or Event()
        self.control.clear_stop()
        self.restore_queue()
        self.queue.reap_expired()
        if once:
            self.maintenance_tick(probe=quota_probe)
            return self.worker.run_once()
        last_task_id = None
        last_error = None
        while not stop_event.is_set() and not self.control.stop_requested():
            result = None
            try:
                self.maintenance_tick(probe=quota_probe)
                result = self.worker.run_once()
                if result is not None:
                    last_task_id = result.task_id
                last_error = None
            except Exception as exc:
                # WorkerRunner has already applied its finite queue outcome;
                # keep the foreground process available for other tasks.
                last_error = f"{type(exc).__name__}: {exc}"
            if result is None:
                stop_event.wait(self.config.idle_sleep_seconds)
            if self.control.stop_requested():
                break
        return {"stopped": True, "last_task_id": last_task_id, "last_error": last_error}

    def stop_task(self, task_id: str) -> dict[str, Any]:
        self.control.request_stop()
        return self._cancel_task(self.store, self.queue, self.controller, task_id)

    @staticmethod
    def _cancel_task(store: SQLiteStateStore, queue: DurableQueue, controller: Controller, task_id: str) -> dict[str, Any]:
        task = controller.cancel(task_id)
        try:
            item = queue.snapshot(task_id)
        except KeyError:
            item = None
        if task.status is TaskStatus.CANCELLED and item is not None and item.state in {"queued", "waiting"}:
            queue.cancel(task_id)
        return OperationService._status(store, queue, task_id)

    @staticmethod
    def cancel_task(config: OperationConfig | None, task_id: str) -> dict[str, Any]:
        """Cancel without constructing a configured Provider or Ledger.

        A stop command must remain usable when the provider credential is
        missing or the provider configuration has changed since the Task was
        submitted.  Controller.cancel only needs the durable StateStore, so a
        non-networking fake provider is sufficient for this narrow boundary.
        """
        config = config or OperationConfig.from_environment()
        store = SQLiteStateStore(config.state_path)
        queue = DurableQueue(config.queue_path)
        control = OperationControl(config.queue_path)
        try:
            control.request_stop()
            controller = Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store)
            return OperationService._cancel_task(store, queue, controller, task_id)
        finally:
            control.close()
            queue.close()
            store.close()

    def stop(self, task_id: str | None = None) -> dict[str, Any] | None:
        """Request loop shutdown and optionally apply one Task stop."""
        self.request_stop()
        return self.stop_task(task_id) if task_id is not None else None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.control.close()
        self.ledger.close()
        self.queue.close()
        self.store.close()

    def __enter__(self) -> "OperationService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _add_common_arguments(parser) -> None:
    parser.add_argument("--data-dir", default=None, help="directory for durable operation state (default: .dev_agent)")
    parser.add_argument("--provider", dest="provider_id", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--binding", dest="provider_binding_id", default=None)
    parser.add_argument("--quota-domain", dest="quota_domain", default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=float, default=None)
    parser.add_argument("--idle-sleep-seconds", type=float, default=None)


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="dev-agent", description="Operate the dev_agent v2 control plane")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="start the durable Worker loop")
    _add_common_arguments(start)
    start.add_argument("--once", action="store_true", help="claim at most one task and exit")
    submit = subparsers.add_parser("submit", help="persist and enqueue one Task")
    _add_common_arguments(submit)
    submit.add_argument("objective")
    submit.add_argument("--priority", type=int, default=0)
    submit.add_argument("--sensitivity", choices=("public", "normal", "internal", "sensitive"), default="normal")
    status = subparsers.add_parser("status", help="show durable Task state")
    _add_common_arguments(status)
    status.add_argument("task_id")
    stop = subparsers.add_parser("stop", help="request Worker stop, optionally cancel one Task")
    _add_common_arguments(stop)
    stop.add_argument("task_id", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = OperationConfig.from_environment(
            data_dir=args.data_dir,
            provider_id=getattr(args, "provider_id", None),
            model=getattr(args, "model", None),
            provider_binding_id=getattr(args, "provider_binding_id", None),
            quota_domain=getattr(args, "quota_domain", None),
            worker_id=getattr(args, "worker_id", None),
            lease_seconds=getattr(args, "lease_seconds", None),
            idle_sleep_seconds=getattr(args, "idle_sleep_seconds", None),
        )
        if args.command == "submit":
            task = OperationService.submit(config, args.objective, priority=args.priority, sensitivity=args.sensitivity)
            print(json.dumps({"task_id": task.task_id, "state": task.status.value}, ensure_ascii=False))
            return 0
        if args.command == "status":
            print(json.dumps(OperationService.read_status(config, args.task_id), ensure_ascii=False))
            return 0
        if args.command == "stop" and args.task_id is None:
            control = OperationControl(config.queue_path)
            try:
                control.request_stop()
            finally:
                control.close()
            print(json.dumps({"stop_requested": True}, ensure_ascii=False))
            return 0
        if args.command == "stop":
            status = OperationService.cancel_task(config, args.task_id)
            print(json.dumps(status, ensure_ascii=False))
            return 0
        with OperationService.open(config) as service:
            if args.command == "start":
                result = service.start(once=args.once)
                if isinstance(result, Task):
                    result = OperationService._status(service.store, service.queue, result.task_id)
                print(json.dumps(result, ensure_ascii=False))
                return 0
    except (OperationError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 2


__all__ = ["OperationConfig", "OperationControl", "OperationError", "OperationService", "build_parser", "main"]
