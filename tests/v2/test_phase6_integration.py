import pytest

from src.dev_agent.resources.budget import BudgetExceeded, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.control import DispatchDenied
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.domain.protocol import Task
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.survival import SurvivalGovernor
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse


def test_phase6_control_plane_reserves_before_paid_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "phase6.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], sensitivity="internal", cost_minor=50)
    ledger.observe("paid", available=10, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=50, recovery_reserve_minor=10))
    reservation = governor.reserve("task-1", "paid", estimated_cost_minor=40)
    assert governor.snapshot()["active_reservations"] == 1
    governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert governor.snapshot()["normal_committed_minor"] == 40


def test_controller_reserves_and_reconciles_before_each_provider_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller.sqlite3")
    ledger.register_resource("fake-resource", provider_id="fake", native_unit="request", capacity=10, capabilities=["text"], sensitivity="normal", cost_minor=10)
    ledger.observe("fake-resource", available=10, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=5))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo", handler=lambda args: args))
    task = Task(objective="phase 6", limits={"max_cost": 25})
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(FakeProvider(), ToolRuntime(registry), store, resource_policy=control).run(task)
        assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20


def test_control_never_converts_generic_float_ceiling_and_holds_missing_charge_unknown(tmp_path):
    ledger = ResourceLedger(tmp_path / "control.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10, price_currency="JPY")
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}], cost_ceiling=0.5)
    reservation = control.reserve_for_provider("task-1", "remote", request)
    control.reconcile_response(reservation, ModelResponse(provider="remote", model="test", usage={}))
    assert ledger.reservation_row(reservation.budget.reservation_id)["status"] == "unknown"
    ledger.register_resource("usd", provider_id="usd", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10, price_currency="USD")
    ledger.observe("usd", available=10, health="healthy")
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-2", "usd", request)
    assert exc.value.category == "budget"


def test_over_budget_observed_charge_is_held_unknown_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "over-budget.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000004", messages=[{"role": "user", "content": "x"}])
    reservation = control.reserve_for_provider(request.task_id, "remote", request)

    with pytest.raises(BudgetExceeded, match="protected budget"):
        control.reconcile_response(reservation, ModelResponse(provider="remote", model="test", usage={"cost_minor": 30}))

    assert ledger.reservation_row(reservation.budget.reservation_id)["status"] == "unknown"


def test_dispatcher_routes_to_secondary_provider_after_retryable_primary_failure(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatch.sqlite3")
    for resource_id, provider_id in (("primary", "primary"), ("secondary", "secondary")):
        ledger.register_resource(resource_id, provider_id=provider_id, native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
        ledger.observe(resource_id, available=10, health="healthy")

    class FailingProvider(FakeProvider):
        provider_id = "primary"

        def request(self, request):
            raise ProviderError("rate limited", category="rate_limit", retryable=True)

    class SecondaryProvider(FakeProvider):
        provider_id = "secondary"

        def request(self, request):
            return ModelResponse(provider="secondary", model="test", text_segments=["ok"])

    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([FailingProvider(), SecondaryProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    assert dispatcher.request(request).provider == "secondary"
    assert [(entry.provider_id, entry.outcome) for entry in dispatcher.audits] == [("primary", "rate_limit"), ("secondary", "succeeded")]


def test_dispatcher_accepts_explicit_task_id_compatibility_entrypoint(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-task-id.sqlite3")
    ledger.register_resource(
        "secondary-resource",
        provider_id="secondary",
        native_unit="request",
        capacity=1,
        capabilities={"text"},
        cost_minor=0,
    )
    ledger.observe("secondary-resource", available=1, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)

    class SecondaryProvider(FakeProvider):
        provider_id = "secondary"

        def request(self, request):
            return ModelResponse(provider="secondary", model="test", text_segments=["ok"])

    dispatcher = ProviderDispatcher(ProviderRegistry([SecondaryProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000002", messages=[{"role": "user", "content": "hello"}])

    assert dispatcher.request("00000000-0000-0000-0000-000000000002", request).provider == "secondary"


def test_dispatcher_does_not_leak_reservation_when_registry_is_missing_provider(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-missing-provider.sqlite3")
    ledger.register_resource(
        "orphan-resource",
        provider_id="orphan",
        native_unit="request",
        capacity=1,
        capabilities={"text"},
        cost_minor=10,
    )
    ledger.observe("orphan-resource", available=1, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)
    dispatcher = ProviderDispatcher(ProviderRegistry([FakeProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000003", messages=[{"role": "user", "content": "hello"}])

    with pytest.raises(ProviderError, match="unsupported provider"):
        dispatcher.request(request)

    assert governor.snapshot()["active_reservations"] == 0
    assert ledger.get_resource("orphan-resource")["available"] == 1


def test_survival_dispatch_policy_prohibits_paid_provider_when_budget_is_exhausted(tmp_path):
    ledger = ResourceLedger(tmp_path / "survival.sqlite3")
    ledger.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    for resource_id in ("free", "paid"):
        ledger.observe(resource_id, available=10, health="healthy")

    class Provider(FakeProvider):
        def request(self, request):
            return ModelResponse(provider=self.provider_id, model="test", text_segments=[self.provider_id])

    free, paid = Provider(), Provider()
    free.provider_id, paid.provider_id = "free", "paid"
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([free, paid]), control, survival=SurvivalGovernor())
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    assert dispatcher.request(request).provider == "free"


def test_authentication_failure_does_not_open_short_retry_circuit(tmp_path):
    ledger = ResourceLedger(tmp_path / "auth.sqlite3")
    ledger.register_resource("primary", provider_id="primary", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("primary", available=10, health="healthy")

    class AuthProvider(FakeProvider):
        provider_id = "primary"

        def request(self, request):
            raise ProviderError("denied", category="authentication", retryable=False)

    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([AuthProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError):
        dispatcher.request(request)
    assert ledger.get_resource("primary")["consecutive_failures"] == 0


def test_controller_runs_through_provider_dispatcher_and_records_selected_provider(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller-dispatch.sqlite3")
    ledger.register_resource("secondary-resource", provider_id="secondary", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("secondary-resource", available=10, health="healthy")

    class SecondaryProvider(FakeProvider):
        provider_id = "secondary"

        def request(self, request):
            return ModelResponse(provider="secondary", model="test", text_segments=["dispatcher complete"])

    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([SecondaryProvider()]), control)
    registry = ToolRegistry()
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(registry), store).run(Task(objective="dispatch e2e"))
    assert result.status.value == "completed"
    assert dispatcher.audits[-1].provider_id == "secondary"


def test_controller_paid_dispatch_reserves_and_reconciles_actual_cost(tmp_path):
    ledger = ResourceLedger(tmp_path / "paid-controller.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=20)
    ledger.observe("paid", available=10, health="healthy")

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["paid"], usage={"cost_minor": 20})

    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=0))
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), ResourceControlPlane(ResourceRouter(ledger), governor))
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="paid e2e"))
    assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20


def test_paid_provider_timeout_waits_for_reconciliation_instead_of_failing(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-timeout.sqlite3")
    ledger.register_resource("paid", provider_id="slow", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    calls = []

    class SlowProvider(FakeProvider):
        provider_id = "slow"

        def request(self, request):
            import time
            calls.append(request.request_id)
            time.sleep(0.2)
            return ModelResponse(provider="slow", model="test", text_segments=["late"])

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        controller = Controller(SlowProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control)
        task = Task(objective="timeout", limits={"max_wall_time_seconds": 0.03})
        result = controller.run(task)
        assert result.status.value == "waiting_reconciliation"
        resumed = controller.resume(task.task_id)
    assert resumed.status.value == "waiting_reconciliation"
    assert len(calls) == 1


def test_paid_provider_transport_error_waits_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("connection lost", category="transport", retryable=True)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(BrokenProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control).run(Task(objective="transport"))
    assert result.status.value == "waiting_reconciliation"


def test_controller_waits_when_dispatcher_owns_transport_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("connection lost", category="transport", retryable=True)

    dispatcher = ProviderDispatcher(ProviderRegistry([BrokenProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="dispatcher transport"))

    assert result.status.value == "waiting_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_dispatcher_wraps_untyped_provider_failure_as_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-raw-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class RawBrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ConnectionError("socket closed")

    dispatcher = ProviderDispatcher(ProviderRegistry([RawBrokenProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="raw dispatcher transport"))

    assert result.status.value == "waiting_reconciliation"


def test_maintenance_mode_denies_new_provider_reservations(tmp_path):
    ledger = ResourceLedger(tmp_path / "maintenance.sqlite3")
    ledger.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("free", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    control.set_maintenance(True)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-1", "free", request)
    assert exc.value.category == "maintenance"


def test_native_capacity_denial_has_unavailable_category(tmp_path):
    ledger = ResourceLedger(tmp_path / "capacity-category.sqlite3")
    ledger.register_resource("tiny", provider_id="tiny", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    ledger.observe("tiny", available=1, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    control.reserve_for_provider("task-1", "tiny", request)
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-2", "tiny", request)
    assert exc.value.category == "unavailable"
