import json
from datetime import datetime, timezone

import pytest

from src.dev_agent.domain.protocol import ModelResponse, TaskStatus, TaskType
from src.dev_agent.operation import OperationConfig, OperationProviderBinding, OperationService
from src.dev_agent.providers.base import ModelProvider, ProviderError


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="operation-test-worker",
        idle_sleep_seconds=0.01,
    )


def test_submit_is_durable_and_status_reads_queue_and_events(tmp_path):
    config = _config(tmp_path)

    task = OperationService.submit(config, "persist this task", priority=4)

    status = OperationService.read_status(config, task.task_id)

    assert status["task_id"] == task.task_id
    assert status["state"] == TaskStatus.QUEUED.value
    assert status["queue_state"] == "queued"
    assert status["current_attempt"] == 0
    assert status["waiting"] is False
    assert status["completed"] is False


def test_operation_root_submission_defaults_to_reasoning_not_worker(tmp_path):
    config = _config(tmp_path)

    task = OperationService.submit(config, "plan a multi-step change")

    assert task.parent_task_id is None
    assert task.root_task_id == task.task_id
    assert task.task_type is TaskType.REASONING


def test_operation_child_submission_requires_explicit_worker_classification(tmp_path):
    config = _config(tmp_path)
    root = OperationService.submit(config, "plan a multi-step change")

    child = OperationService.submit_child(config, root.task_id, "add the focused regression test")

    assert child.parent_task_id == root.task_id
    assert child.root_task_id == root.task_id
    assert child.depth == 1
    assert child.task_type is TaskType.WORKER


def test_start_once_uses_canonical_dispatcher_and_finishes_task(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "complete through the operation layer")

    with OperationService.open(config) as service:
        result = service.start(once=True)

    assert result is not None
    assert result.task_id == task.task_id
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.COMPLETED.value
    assert status["queue_state"] == "completed"
    assert status["completed"] is True
    assert status["selected_provider"] == "fake"
    assert status["selected_binding"] == "fake:default"
    assert status["last_event"]["event_type"] == "task.completed"


def test_start_once_runs_the_bounded_maintenance_boundary(tmp_path, monkeypatch):
    config = _config(tmp_path)
    task = OperationService.submit(config, "run after maintenance")
    calls = []

    with OperationService.open(config) as service:
        monkeypatch.setattr(
            service,
            "maintenance_tick",
            lambda **kwargs: calls.append(kwargs) or [],
        )
        result = service.start(once=True)

    assert result is not None and result.task_id == task.task_id
    assert calls == [{"probe": None}]


def test_restart_restores_queued_task_when_enqueue_was_interrupted(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "repair an interrupted enqueue")

    # Simulate the only partial-submit state the operation layer can safely
    # repair: durable Task exists while its queue row is absent.
    from src.dev_agent.scheduler.queue import DurableQueue

    with DurableQueue(config.queue_path) as queue:
        queue.connection.execute("DELETE FROM queue_items WHERE task_id=?", (task.task_id,))
        queue.connection.commit()
        with pytest.raises(KeyError):
            queue.snapshot(task.task_id)

    with OperationService.open(config) as service:
        restored = service.restore_queue()
        assert restored == [task.task_id]
        result = service.start(once=True)
        assert result is not None

    assert OperationService.read_status(config, task.task_id)["state"] == TaskStatus.COMPLETED.value


def test_process_restart_resumes_durable_queue_and_leaves_terminal_state(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "resume after operation process restart")

    # The first service instance represents the process that accepted the
    # Task.  It closes before a Worker claim, so the second instance must use
    # only the durable Task and Queue rows.
    with OperationService.open(config):
        pass

    with OperationService.open(config) as restarted:
        result = restarted.start(once=True)

    assert result is not None and result.task_id == task.task_id
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.COMPLETED.value
    assert status["queue_state"] == "completed"
    assert status["last_event"]["event_type"] == "task.completed"


def test_process_restart_preserves_waiting_reconciliation_in_operation_status(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state import SQLiteStateStore

    config = _config(tmp_path)
    task = Task(objective="preserve external uncertainty", status=TaskStatus.WAITING_RECONCILIATION)
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(task)
        store.checkpoint(
            task_id=task.task_id,
            step_id="provider-step",
            phase="waiting_reconciliation",
            state={"provider_reconciliation": {"status": "unknown"}},
        )
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(task.task_id)

    with OperationService.open(config) as service:
        result = service.start(once=True)

    assert result is not None and result.status is TaskStatus.WAITING_RECONCILIATION
    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.WAITING_RECONCILIATION.value
    assert status["queue_state"] == "waiting"
    assert status["reconciliation"] is True


def test_stop_queued_task_is_cancelled_not_failed(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "cancel before execution")

    with OperationService.open(config) as service:
        status = service.stop(task.task_id)

    assert status["state"] == TaskStatus.CANCELLED.value
    assert status["queue_state"] == "cancelled"
    assert status["failed"] is False
    assert status["last_event"]["event_type"] == "task.cancelled"


def test_stop_requests_worker_shutdown_without_deleting_task(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "leave durable state after stop")

    with OperationService.open(config) as service:
        from threading import Event

        stop_event = Event()
        stop_event.set()
        result = service.start(stop_event=stop_event)

    assert result["stopped"] is True
    assert OperationService.read_status(config, task.task_id)["state"] == TaskStatus.QUEUED.value


def test_durable_stop_signal_stops_a_foreground_loop_from_another_process(tmp_path):
    from threading import Thread
    from time import sleep
    from src.dev_agent.operation import OperationControl

    config = _config(tmp_path)
    result_box = []
    with OperationService.open(config) as service:
        runner = Thread(target=lambda: result_box.append(service.start()), daemon=True)
        runner.start()
        sleep(0.05)
        external_control = OperationControl(config.queue_path)
        try:
            external_control.request_stop()
        finally:
            external_control.close()
        runner.join(2)

    assert not runner.is_alive()
    assert result_box and result_box[0]["stopped"] is True


def test_status_is_json_serializable(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(config, "json status")

    status = OperationService.read_status(config, task.task_id)

    json.dumps(status, ensure_ascii=False)


def test_operation_maintenance_tick_requalifies_due_quota_and_wakes_queue(tmp_path):
    from src.dev_agent.scheduler.quota import QuotaWakeScheduler

    config = _config(tmp_path)
    task = OperationService.submit(config, "wait for quota recovery")
    with OperationService.open(config) as service:
        service.ledger.register_resource(
            "fake:default",
            provider_id="fake",
            provider_binding_id="fake:default",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            quota_domain="fake-domain",
            metadata={"provider_binding_id": "fake:default", "model_id": "deterministic", "intelligence_tier": "L1"},
            intelligence_tier="L1",
        )
        service.ledger.observe_quota(
            "fake:default",
            unit="requests",
            metric="rpm",
            window="minute",
            request_limit=10,
            request_remaining=0,
            blocked_until="2026-09-10T11:59:00+00:00",
            block_reason="rate_limit",
            observed_at="2026-09-10T11:00:00+00:00",
        )
        item = service.queue.claim("quota-maintenance-worker", lease_seconds=30)
        QuotaWakeScheduler(service.ledger, service.queue).park(
            task.task_id,
            worker_id="quota-maintenance-worker",
            state_version=item.state_version,
            wake_at=datetime(2026, 9, 10, 11, 59, tzinfo=timezone.utc),
            quota_domain="fake-domain",
        )
        calls = []

        results = service.maintenance_tick(
            now=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            max_probes=1,
            probe=lambda resource_id, domain: calls.append((resource_id, domain)) or {
                "unit": "requests",
                "metric": "rpm",
                "window": "minute",
                "request_limit": 10,
                "request_remaining": 9,
            },
        )

        assert results[0]["status"] == "requalified"
        assert calls == [("fake:default", "fake-domain")]
        assert service.ledger.get_quota_observation("fake:default")["block_reason"] is None
        assert service.ledger.get_resource("fake:default")["observed_at"] == "2026-09-10T12:00:00+00:00"
        assert results[0]["woken_tasks"] == 1


def test_external_cancel_of_running_task_is_only_a_durable_request(tmp_path):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.runtime.controller import Controller
    from src.dev_agent.state import SQLiteStateStore
    from src.dev_agent.tools import ToolRegistry, ToolRuntime

    path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(path) as store:
        task = Task(objective="cancel from another operation process", status=TaskStatus.RUNNING)
        store.save_task(task)
        controller = Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store)

        requested = controller.cancel(task.task_id, reason="operator stop")

        assert requested.status == TaskStatus.RUNNING
        persisted = store.load_task(task.task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING
        assert persisted.metadata["cancellation_requested"] is True
        assert persisted.metadata["cancellation_reason"] == "operator stop"


def test_operation_resource_bootstrap_is_read_only_for_existing_resource(tmp_path):
    from src.dev_agent.operation import OperationService
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    path = tmp_path / "resources.sqlite3"
    config = _config(tmp_path)
    with ResourceLedger(path) as ledger:
        ledger.register_resource(
            "fake:default",
            provider_id="fake",
            provider_binding_id="fake:default",
            native_unit="neurons",
            capacity=7,
            capabilities=["text", "tool_call"],
            sensitivity="sensitive",
            cost_minor=42,
            price_currency="USD",
            quota_domain="operator-domain",
            metadata={"operator_label": "keep", "model_id": "deterministic"},
            intelligence_tier="L2",
        )
        ledger.observe("fake:default", available=3, health="degraded", confidence=0.4, inflight=1, concurrency_limit=2)
        before = ledger.get_resource("fake:default")

        provider = FakeProvider()
        provider.provider_binding_id = "fake:default"
        provider.model_id = "deterministic"
        provider.intelligence_tier = "L1"
        OperationService._ensure_resource(ledger, provider, config)

        assert ledger.get_resource("fake:default") == before


def test_operation_only_marks_exact_known_binding_and_model_as_free(tmp_path):
    from src.dev_agent.operation import OperationService
    from src.dev_agent.resources.ledger import ResourceLedger

    path = tmp_path / "resources.sqlite3"
    with ResourceLedger(path) as ledger:
        known = OperationConfig(
            data_dir=tmp_path,
            provider_id="cloudflare",
            model="@cf/meta/llama-3.1-8b-instruct",
            provider_binding_id="cloudflare",
            quota_domain="cloudflare-account",
        )
        known_provider = type("Provider", (), {"provider_binding_id": "cloudflare", "model_id": known.model, "intelligence_tier": "L1"})()
        OperationService._ensure_resource(ledger, known_provider, known)
        assert ledger.get_resource("cloudflare")["cost_minor"] == 0
        assert ledger.get_resource("cloudflare")["quota_domain"] == "cloudflare-account"

        unknown = OperationConfig(
            data_dir=tmp_path,
            provider_id="cloudflare",
            model="a-paid-or-unqualified-model",
            provider_binding_id="cloudflare:unknown",
            quota_domain="cloudflare-account",
        )
        unknown_provider = type("Provider", (), {"provider_binding_id": "cloudflare:unknown", "model_id": unknown.model, "intelligence_tier": "L1"})()
        OperationService._ensure_resource(ledger, unknown_provider, unknown)
        assert ledger.get_resource("cloudflare:unknown")["cost_minor"] is None


def test_operation_composes_an_explicit_multi_provider_pool_with_bounded_routing(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path,
        provider_pool=(
            OperationProviderBinding(
                provider_id="gemini",
                model="gemini-3.5-flash-lite",
                provider_binding_id="gemini:worker",
                quota_domain="google-project",
            ),
            OperationProviderBinding(
                provider_id="cloudflare",
                model="@cf/meta/llama-3.1-8b-instruct",
                provider_binding_id="cloudflare",
                quota_domain="cloudflare-account",
            ),
        ),
        worker_id="multi-provider-operation",
    )

    with OperationService.open(config) as service:
        registry = service.controller.provider.registry
        assert registry.bindings_for_provider("gemini") == ("gemini:worker",)
        assert registry.bindings_for_provider("cloudflare") == ("cloudflare",)
        assert service.controller.intelligence_routing is True
        assert service.controller.allow_unknown_quota is True
        assert service.ledger.get_resource("gemini:worker")["quota_domain"] == "google-project"
        assert service.ledger.get_resource("cloudflare")["quota_domain"] == "cloudflare-account"


class _OperationPoolProvider(ModelProvider):
    def __init__(self, provider_id, binding_id, model_id, tier, *, fail_once=False):
        self.provider_id = provider_id
        self.provider_binding_id = binding_id
        self.model_id = model_id
        self.model = model_id
        self.intelligence_tier = tier
        self.fail_once = fail_once
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        if self.fail_once:
            self.fail_once = False
            raise ProviderError("temporary provider limit", category="rate_limit", retryable=True)
        return ModelResponse(provider=self.provider_id, model=self.model_id, text_segments=["pool-success"])


def test_operation_dispatches_l1_task_through_pool_and_falls_back_within_tier(tmp_path, monkeypatch):
    primary = _OperationPoolProvider(
        "cloudflare",
        "cloudflare",
        "@cf/meta/llama-3.1-8b-instruct",
        "L1",
        fail_once=True,
    )
    secondary = _OperationPoolProvider(
        "gemini",
        "gemini:worker",
        "gemini-3.5-flash-lite",
        "L1",
    )
    providers = {primary.provider_binding_id: primary, secondary.provider_binding_id: secondary}

    def build_provider(binding):
        return providers[binding.binding_id]

    monkeypatch.setattr(OperationService, "_build_provider", staticmethod(build_provider))
    config = OperationConfig(
        data_dir=tmp_path,
        provider_pool=(
            OperationProviderBinding(
                provider_id="cloudflare",
                model=primary.model_id,
                provider_binding_id=primary.provider_binding_id,
                quota_domain="cloudflare-account",
            ),
            OperationProviderBinding(
                provider_id="gemini",
                model=secondary.model_id,
                provider_binding_id=secondary.provider_binding_id,
                quota_domain="google-project",
            ),
        ),
        worker_id="operation-pool-fallback",
    )
    task = OperationService.submit(config, "run one bounded worker", task_type=TaskType.WORKER)

    with OperationService.open(config) as service:
        result = service.start(once=True)

    assert result is not None and result.status is TaskStatus.COMPLETED
    assert len(primary.requests) == 1
    assert len(secondary.requests) == 1
    assert primary.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]
    assert secondary.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]
    status = OperationService.read_status(config, task.task_id)
    assert status["selected_provider"] == "gemini"
    assert status["selected_binding"] == "gemini:worker"


def test_operation_rejects_existing_resource_with_untrusted_free_price(tmp_path):
    from src.dev_agent.operation import OperationError, OperationService
    from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="cloudflare",
        model="unqualified-model",
        provider_binding_id="cloudflare:unqualified",
        quota_domain="cloudflare-account",
    )
    provider = CloudflareWorkersAIHttpProvider(model=config.model)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "cloudflare:unqualified",
            provider_id="cloudflare",
            provider_binding_id="cloudflare:unqualified",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            quota_domain="cloudflare-account",
            metadata={"provider_binding_id": "cloudflare:unqualified", "model_id": config.model},
        )

        with pytest.raises(OperationError, match="billing"):
            OperationService._ensure_resource(ledger, provider, config)

        assert ledger.get_resource("cloudflare:unqualified")["cost_minor"] == 0


def test_operation_rejects_existing_free_resource_without_model_identity(tmp_path):
    from src.dev_agent.operation import OperationError, OperationService
    from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        provider_binding_id="cloudflare",
        quota_domain="cloudflare-account",
    )
    provider = CloudflareWorkersAIHttpProvider(model=config.model)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "cloudflare",
            provider_id="cloudflare",
            provider_binding_id="cloudflare",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            quota_domain="cloudflare-account",
        )

        with pytest.raises(OperationError, match="model identity"):
            OperationService._ensure_resource(ledger, provider, config)


def test_operation_rejects_existing_zero_cost_resource_without_catalog_authority(tmp_path):
    from src.dev_agent.operation import OperationError, OperationService
    from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        provider_binding_id="cloudflare",
        quota_domain="cloudflare-account",
    )
    provider = CloudflareWorkersAIHttpProvider(model=config.model)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "cloudflare",
            provider_id="cloudflare",
            provider_binding_id="cloudflare",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            quota_domain="cloudflare-account",
            metadata={"provider_binding_id": "cloudflare", "model_id": config.model},
        )

        with pytest.raises(OperationError, match="billing authority"):
            OperationService._ensure_resource(ledger, provider, config)


def test_operation_requires_quota_domain_for_remote_resource(tmp_path):
    from src.dev_agent.operation import OperationError, OperationService
    from src.dev_agent.resources.ledger import ResourceLedger

    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        provider_binding_id="cloudflare",
    )
    provider = type("Provider", (), {"provider_binding_id": "cloudflare", "model_id": config.model, "intelligence_tier": "L1"})()
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        with pytest.raises(OperationError, match="quota_domain"):
            OperationService._ensure_resource(ledger, provider, config)


def test_operation_does_not_fill_missing_existing_quota_domain_from_runtime_config(tmp_path):
    from src.dev_agent.operation import OperationError, OperationService
    from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        provider_binding_id="cloudflare",
        quota_domain="cloudflare-account",
    )
    provider = CloudflareWorkersAIHttpProvider(model=config.model)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "cloudflare",
            provider_id="cloudflare",
            provider_binding_id="cloudflare",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            sensitivity="normal",
            cost_minor=0,
            price_currency="JPY",
        )
        with pytest.raises(OperationError, match="operator quota_domain"):
            OperationService._ensure_resource(ledger, provider, config)
        assert ledger.get_resource("cloudflare")["quota_domain"] is None


def test_new_operation_resource_is_degraded_until_a_real_provider_observation(tmp_path):
    from src.dev_agent.operation import OperationService
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.resources.ledger import ResourceLedger

    config = _config(tmp_path)
    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        provider = FakeProvider()
        OperationService._ensure_resource(ledger, provider, config)
        observation = ledger.get_resource("fake:default")
        assert observation["health"] == "degraded"
        assert observation["confidence"] == 0


def test_successful_provider_observation_refreshes_resource_freshness(tmp_path):
    from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
    from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
    from src.dev_agent.resources.control import ResourceControlPlane
    from src.dev_agent.resources.ledger import ResourceLedger
    from src.dev_agent.resources.router import ResourceRouter

    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        ledger.register_resource(
            "fake:default",
            provider_id="fake",
            provider_binding_id="fake:default",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
        )
        ledger.observe("fake:default", available=1, health="healthy")
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
        control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
        reservation = control.reserve_for_provider("00000000-0000-0000-0000-000000000001", "fake", ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}]))
        ledger.observe("fake:default", available=1, health="healthy", observed_at="2020-01-01T00:00:00+00:00")
        before = ledger.get_resource("fake:default")["observed_at"]

        control.observe_provider_response(
            reservation,
            ModelResponse(provider="fake", model="deterministic", text_segments=["ok"], usage={"cost_minor": 0}),
        )

        after = ledger.get_resource("fake:default")["observed_at"]
        assert after != before
        assert after > "2020-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    ("category", "expected_status", "event_type"),
    [
        ("quota", TaskStatus.BLOCKED_QUOTA, "task.blocked_quota"),
        ("maintenance", TaskStatus.WAITING_DEPENDENCY, "task.waiting_maintenance"),
        ("invalid_request", TaskStatus.FAILED, "task.failed"),
        ("no_route", TaskStatus.WAITING_DEPENDENCY, "task.waiting_resource"),
    ],
)
def test_dispatch_denied_categories_do_not_all_become_budget_blocked(tmp_path, monkeypatch, category, expected_status, event_type):
    from src.dev_agent.domain.protocol import Task
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.resources.control import DispatchDenied
    from src.dev_agent.runtime.controller import Controller, RuntimeFailure
    from src.dev_agent.state import SQLiteStateStore
    from src.dev_agent.tools import ToolRegistry, ToolRuntime

    class RaisingProvider(FakeProvider):
        provider_id = "resource-router"
        handles_resource_policy = True

        def request(self, request):
            raise DispatchDenied(category, category)

    task = Task(objective=f"denied {category}")
    with SQLiteStateStore(tmp_path / f"{category}.sqlite3") as store:
        controller = Controller(RaisingProvider(), ToolRuntime(ToolRegistry()), store)
        if expected_status is TaskStatus.FAILED:
            with pytest.raises(RuntimeFailure):
                controller.run(task)
        else:
            controller.run(task)
        persisted = store.load_task(task.task_id)
        assert persisted is not None and persisted.status is expected_status
        event_types = [event["event_type"] for event in store.snapshot()["events"] if event.get("task_id") == task.task_id]
        assert event_type in event_types
        assert "task.blocked_budget" not in event_types
