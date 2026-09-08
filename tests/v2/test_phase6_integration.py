import pytest

from src.dev_agent.resources.budget import BudgetAuthority, BudgetExceeded, BudgetGovernor, BudgetPolicy
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


def _governor(ledger, policy):
    BudgetAuthority.configure(ledger, policy)
    return BudgetGovernor(ledger, policy)


def test_phase6_control_plane_reserves_before_paid_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "phase6.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], sensitivity="internal", cost_minor=50)
    ledger.observe("paid", available=10, health="healthy")
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=50, recovery_reserve_minor=10))
    reservation = governor.reserve("task-1", "paid", estimated_cost_minor=40)
    assert governor.snapshot()["active_reservations"] == 1
    governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert governor.snapshot()["normal_committed_minor"] == 40


def test_controller_reserves_and_reconciles_before_each_provider_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller.sqlite3")
    ledger.register_resource("fake-resource", provider_id="fake", native_unit="request", capacity=10, capabilities=["text"], sensitivity="normal", cost_minor=10)
    ledger.observe("fake-resource", available=10, health="healthy")
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=5))
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
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
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
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
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

    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([FailingProvider(), SecondaryProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    assert dispatcher.request(request).provider == "secondary"
    assert [(entry.provider_id, entry.outcome) for entry in dispatcher.audits] == [("primary", "rate_limit"), ("secondary", "succeeded")]


def test_dispatcher_does_not_retry_transport_after_external_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatch-transport-no-retry.sqlite3")
    for resource_id, provider_id in (("primary", "primary"), ("secondary", "secondary")):
        ledger.register_resource(resource_id, provider_id=provider_id, native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
        ledger.observe(resource_id, available=10, health="healthy")
    calls = []

    class TransportProvider(FakeProvider):
        provider_id = "primary"

        def request(self, request):
            calls.append("primary")
            raise ProviderError("connection lost", category="transport", retryable=True)

    class SecondaryProvider(FakeProvider):
        provider_id = "secondary"

        def request(self, request):
            calls.append("secondary")
            return ModelResponse(provider="secondary", model="test", text_segments=["must not retry"])

    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([TransportProvider(), SecondaryProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000009", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(ProviderError, match="connection lost") as exc:
        dispatcher.request(request)
    assert exc.value.category == "transport"
    assert calls == ["primary"]
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_dispatcher_persists_dispatching_before_provider_call(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatching-state.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    observed_statuses = []

    class InspectingProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            observed_statuses.append(ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()[0])
            return ModelResponse(provider="paid", model="test", text_segments=["ok"], usage={"cost_minor": 10})

    dispatcher = ProviderDispatcher(ProviderRegistry([InspectingProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000010", messages=[{"role": "user", "content": "x"}])
    assert dispatcher.request(request).provider == "paid"
    assert observed_statuses == ["dispatching"]
    assert ledger.reservation_totals()["active_reservations"] == 0


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
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)

    class SecondaryProvider(FakeProvider):
        provider_id = "secondary"

        def request(self, request):
            return ModelResponse(provider="secondary", model="test", text_segments=["ok"])

    dispatcher = ProviderDispatcher(ProviderRegistry([SecondaryProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000002", messages=[{"role": "user", "content": "hello"}])

    assert dispatcher.request("00000000-0000-0000-0000-000000000002", request).provider == "secondary"


def test_provider_registry_rejects_duplicate_provider_ids():
    with pytest.raises(ValueError, match="duplicate provider_id"):
        ProviderRegistry([FakeProvider(), FakeProvider()])


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
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
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
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)))
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

    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
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

    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([SecondaryProvider()]), control)
    registry = ToolRegistry()
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(registry), store).run(Task(objective="dispatch e2e"))
    assert result.status.value == "completed"
    assert dispatcher.audits[-1].provider_id == "secondary"


def test_controller_maps_dispatcher_budget_denial_to_blocked_budget(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-budget-denial.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)))

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            raise AssertionError("budget-denied provider must not be invoked")

    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="budget blocked"))
        events = [event for event in store.snapshot()["events"] if event["event_type"] == "task.blocked_budget"]

    assert result.status.value == "blocked_budget"
    assert events


def test_controller_maps_dispatcher_no_route_to_blocked_budget(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-no-route.sqlite3")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([FakeProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="no route"))

    assert result.status.value == "blocked_budget"


def test_controller_paid_dispatch_reserves_and_reconciles_actual_cost(tmp_path):
    ledger = ResourceLedger(tmp_path / "paid-controller.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=20)
    ledger.observe("paid", available=10, health="healthy")

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["paid"], usage={"cost_minor": 20})

    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=0))
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), ResourceControlPlane(ResourceRouter(ledger), governor))
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="paid e2e"))
    assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20


def test_paid_provider_timeout_waits_for_reconciliation_instead_of_failing(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-timeout.sqlite3")
    ledger.register_resource("paid", provider_id="slow", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

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
        assert store.connection.execute("SELECT status FROM effect_intents").fetchone()[0] == "unknown"
        resumed = controller.resume(task.task_id)
    assert resumed.status.value == "waiting_reconciliation"
    assert len(calls) == 1


def test_paid_provider_transport_error_waits_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("connection lost", category="transport", retryable=True)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(BrokenProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control).run(Task(objective="transport"))
    assert result.status.value == "waiting_reconciliation"


def test_paid_provider_decode_failure_after_dispatch_waits_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-decode.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class DecodeFailureProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise TypeError("provider response could not be decoded")

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(DecodeFailureProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control).run(Task(objective="decode failure"))
        assert store.connection.execute("SELECT status FROM effect_intents").fetchone()[0] == "unknown"

    assert result.status.value == "waiting_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1
    assert ledger.reservation_row(next(iter(ledger.connection.execute("SELECT reservation_id FROM budget_reservations").fetchone()))) ["status"] == "unknown"


def test_dispatcher_persists_provider_intent_and_selection_audit(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-intent.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["durable"], usage={"cost_minor": 10})

    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="durable provider audit"))
        intents = store.connection.execute("SELECT tool_name, status, result_payload FROM effect_intents").fetchall()

    assert result.status.value == "completed"
    assert len(intents) == 1
    assert intents[0]["tool_name"] == "provider:paid"
    assert intents[0]["status"] == "succeeded"
    assert '"resource_id": "paid"' in intents[0]["result_payload"]


def test_dispatcher_replays_durable_success_without_duplicate_provider_call(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-replay.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    calls = []

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["once"], usage={"cost_minor": 10})

    request = ModelRequest(task_id=Task(objective="replay-task").task_id, messages=[{"role": "user", "content": "hello"}])
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher.bind_runtime(state_store=store)
        first = dispatcher.request(request)
        second = dispatcher.request(request)

    assert first.to_dict() == second.to_dict()
    assert calls == [request.request_id]
    assert ledger.reservation_totals()["active_reservations"] == 0


def test_dispatcher_rejects_stale_lease_before_provider_call(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-lease.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    calls = []

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["must not run"], usage={"cost_minor": 10})

    request = ModelRequest(task_id=Task(objective="stale-task").task_id, messages=[{"role": "user", "content": "hello"}])
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher.bind_runtime(state_store=store, lease_guard=lambda: (_ for _ in ()).throw(RuntimeError("stale worker")))
        with pytest.raises(ProviderError, match="stale lease") as exc_info:
            dispatcher.request(request)
        assert exc_info.value.category == "lease_lost"
        key = store.connection.execute("SELECT idempotency_key FROM effect_intents").fetchone()[0]
        intent = store.get_effect_intent(key)

    assert intent["status"] == "confirmed_failed"
    assert calls == []
    assert ledger.reservation_totals()["active_reservations"] == 0


def test_controller_waits_when_dispatcher_owns_transport_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("connection lost", category="transport", retryable=True)

    dispatcher = ProviderDispatcher(ProviderRegistry([BrokenProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="dispatcher transport"))

    assert result.status.value == "waiting_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_controller_maps_dispatcher_timeout_to_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-timeout.sqlite3")
    ledger.register_resource("paid", provider_id="slow", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class SlowProvider(FakeProvider):
        provider_id = "slow"

        def request(self, request):
            import time
            time.sleep(0.2)
            return ModelResponse(provider="slow", model="test", text_segments=["late"])

    dispatcher = ProviderDispatcher(ProviderRegistry([SlowProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="dispatcher timeout", limits={"max_wall_time_seconds": 0.03})
        controller = Controller(dispatcher, ToolRuntime(ToolRegistry()), store)
        result = controller.run(task)
        assert result.status.value == "waiting_reconciliation"
        resumed = controller.resume(task.task_id)
        assert resumed.status.value == "waiting_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_dispatcher_wraps_untyped_provider_failure_as_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-raw-transport.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class RawBrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ConnectionError("socket closed")

    dispatcher = ProviderDispatcher(ProviderRegistry([RawBrokenProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="raw dispatcher transport"))

    assert result.status.value == "waiting_reconciliation"


def test_dispatcher_holds_over_budget_observed_charge_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-over-budget.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class OverBudgetProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["ok"], usage={"cost_minor": 30})

    dispatcher = ProviderDispatcher(ProviderRegistry([OverBudgetProvider()]), control)
    task = Task(objective="dispatcher over-budget response")
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(task)

    assert result.status.value == "waiting_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_maintenance_mode_denies_new_provider_reservations(tmp_path):
    ledger = ResourceLedger(tmp_path / "maintenance.sqlite3")
    ledger.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("free", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    control.set_maintenance(True)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-1", "free", request)
    assert exc.value.category == "maintenance"


def test_runtime_maintenance_is_shared_across_ledger_connections(tmp_path):
    path = tmp_path / "shared-maintenance.sqlite3"
    first = ResourceLedger(path)
    first.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    first.observe("free", available=10, health="healthy")
    first_control = ResourceControlPlane(ResourceRouter(first), _governor(first, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    second = ResourceLedger(path)
    second_control = ResourceControlPlane(ResourceRouter(second), BudgetGovernor(second, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    first_control.set_maintenance(True)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000005", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(DispatchDenied) as exc:
        second_control.reserve_for_provider(request.task_id, "free", request)

    assert exc.value.category == "maintenance"
    first_control.set_maintenance(False)
    assert second_control.reserve_for_provider(request.task_id, "free", request).budget.reservation_id


def test_native_capacity_denial_has_unavailable_category(tmp_path):
    ledger = ResourceLedger(tmp_path / "capacity-category.sqlite3")
    ledger.register_resource("tiny", provider_id="tiny", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    ledger.observe("tiny", available=1, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    control.reserve_for_provider("task-1", "tiny", request)
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-2", "tiny", request)
    assert exc.value.category == "unavailable"
