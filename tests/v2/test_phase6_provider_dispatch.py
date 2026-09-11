import multiprocessing
import json
import time

import pytest

from src.dev_agent.resources.budget import BudgetAuthority, BudgetExceeded, BudgetGovernor, BudgetPolicy, BudgetReconciliationRequired
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.control import DispatchDenied
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.survival import SurvivalGovernor
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime
from src.dev_agent.runtime.controller import Controller, ExecutionContext
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.scheduler.queue import DurableQueue


def _dispatch_with_stale_lease_in_child(resource_path, state_path, marker_path, request_payload, proof_payload, result_queue):
    """Attempt the concrete provider dispatch from a separate OS process."""
    from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
    from src.dev_agent.providers.base import ProviderError
    from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
    from src.dev_agent.providers.fake.provider import FakeProvider
    from src.dev_agent.resources.budget import BudgetGovernor
    from src.dev_agent.resources.control import ResourceControlPlane
    from src.dev_agent.resources.ledger import ResourceLedger
    from src.dev_agent.resources.router import ResourceRouter
    from src.dev_agent.scheduler.queue import LeaseProof
    from src.dev_agent.state.sqlite_store import SQLiteStateStore

    class MarkerProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            marker_path.write_text("provider-entered", encoding="utf-8")
            return ModelResponse(provider="paid", model="test", text_segments=["must not run"], usage={"cost_minor": 10})

    proof = LeaseProof(**proof_payload)
    ledger = ResourceLedger(resource_path)
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    dispatcher = ProviderDispatcher(ProviderRegistry([MarkerProvider()]), control)
    try:
        with SQLiteStateStore(state_path) as store:
            dispatcher.bind_runtime(state_store=store, lease_proof=lambda: proof)
            dispatcher.request(ModelRequest.from_dict(request_payload))
    except ProviderError as exc:
        result_queue.put({"category": exc.category, "message": str(exc)})
    except BaseException as exc:
        result_queue.put({"category": type(exc).__name__, "message": str(exc)})
    else:
        result_queue.put({"category": "unexpected_success"})
    finally:
        ledger.close()


def _governor(ledger, policy):
    BudgetAuthority.configure(ledger, policy)
    return BudgetGovernor(ledger, policy)



# Tests split mechanically from test_phase6_integration.py; semantics are unchanged.

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
            return ModelResponse(provider="secondary", model="test", text_segments=["ok"], usage={"cost_minor": 0})

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


@pytest.mark.parametrize(
    ("response_provider", "response_model"),
    [("other-provider", "model-a"), ("paid", "model-b")],
)
def test_dispatcher_rejects_response_identity_mismatch_as_reconciliation(tmp_path, response_provider, response_model):
    ledger = ResourceLedger(tmp_path / "response-identity.sqlite3")
    ledger.register_resource(
        "paid",
        provider_id="paid",
        provider_binding_id="paid:binding",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=10,
        metadata={"model_id": "model-a"},
    )
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class MismatchedProvider(FakeProvider):
        provider_id = "paid"
        provider_binding_id = "paid:binding"
        model_id = "model-a"

        def request(self, request):
            return ModelResponse(provider=response_provider, model=response_model, text_segments=["must not be accepted"], usage={"cost_minor": 10})

    dispatcher = ProviderDispatcher(ProviderRegistry([MismatchedProvider()]), control)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000013", messages=[{"role": "user", "content": "identity"}])

    with pytest.raises(ProviderError, match="identity mismatch") as exc:
        dispatcher.request(request)

    assert exc.value.category == "provider_decode"
    assert ledger.reservation_totals()["active_reservations"] == 1
    assert ledger.reservation_row(ledger.connection.execute("SELECT reservation_id FROM budget_reservations").fetchone()[0])["status"] == "unknown"


def test_controller_persists_dispatch_lease_with_provider_intent(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatch-lease-resource.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    queue = DurableQueue(tmp_path / "dispatch-lease-state.sqlite3")
    task = Task(objective="lease-bound dispatch")
    queue.enqueue(task.task_id)
    item = queue.claim("worker-a", lease_seconds=30)

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["ok"], usage={"cost_minor": 10})

    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "dispatch-lease-state.sqlite3") as store:
        store.save_task(task)
        controller = Controller(dispatcher, ToolRuntime(ToolRegistry()), store)

        def assert_active_lease():
            queue.assert_lease(task.task_id, worker_id="worker-a", state_version=item.state_version, lease_token=item.lease_token)

        result = controller.run(task, execution_context=ExecutionContext(lease_guard=assert_active_lease, lease_proof=item.lease_proof))
        assert result.status == TaskStatus.COMPLETED
        queue.complete(task.task_id, worker_id="worker-a", state_version=item.state_version)
        intent = store.connection.execute("SELECT result_payload FROM effect_intents").fetchone()
        assert intent is not None
        payload = json.loads(intent["result_payload"])
        assert payload["dispatch_lease"]["worker_id"] == "worker-a"
        assert payload["dispatch_lease"]["state_version"] == item.state_version


def test_dispatcher_converts_unknown_budget_replay_into_reconciliation_required(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatch-unknown-budget.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    policy = BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)
    governor = _governor(ledger, policy)
    control = ResourceControlPlane(ResourceRouter(ledger), governor)
    calls = []

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["must not retry"], usage={"cost_minor": 10})

    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000012", messages=[{"role": "user", "content": "unknown budget"}])
    intent_key = f"provider:{request.request_id}:paid"
    with SQLiteStateStore(tmp_path / "dispatch-unknown-state.sqlite3") as store:
        store.create_effect_intent(intent_key, task_id=request.task_id, tool_name="provider:paid", arguments={"request_id": request.request_id})
        store.transition_effect_intent(intent_key, to_status="prepared")
        reservation = governor.reserve(request.task_id, "paid", estimated_cost_minor=10, intent_key=intent_key)
        governor.mark_unknown(reservation.reservation_id)

        dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
        dispatcher.bind_runtime(state_store=store)
        with pytest.raises(ProviderError) as exc:
            dispatcher.request(request)

        assert exc.value.category == "reconciliation_required"
        assert calls == []
        assert store.get_effect_intent(intent_key)["status"] == "unknown"
        assert ledger.reservation_row(reservation.reservation_id)["status"] == "unknown"


def test_dispatcher_reuses_budget_reservation_after_crash_between_budget_and_intent_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatch-crash-restart.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    calls = []

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["recovered"], usage={"cost_minor": 10})

    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000011", messages=[{"role": "user", "content": "crash boundary"}])
    registry = ProviderRegistry([PaidProvider()])
    with SQLiteStateStore(tmp_path / "dispatch-state.sqlite3") as store:
        first = ProviderDispatcher(registry, control)
        first.bind_runtime(state_store=store)
        original_intent = first._intent

        def crash_after_budget(key, *, status, result):
            if status == "dispatching":
                raise KeyboardInterrupt("simulated process crash")
            return original_intent(key, status=status, result=result)

        first._intent = crash_after_budget
        with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
            first.request(request)

        intent_key = f"provider:{request.request_id}:paid"
        assert store.get_effect_intent(intent_key)["status"] == "prepared"
        assert ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()[0] == "dispatching"
        assert calls == []

        second = ProviderDispatcher(registry, control)
        second.bind_runtime(state_store=store)
        assert second.request(request).text_segments == ["recovered"]
        assert calls == [request.request_id]
        assert ledger.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


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
            return ModelResponse(provider="secondary", model="test", text_segments=["ok"], usage={"cost_minor": 0})

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
            return ModelResponse(provider=self.provider_id, model="test", text_segments=[self.provider_id], usage={"cost_minor": 0})

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
            return ModelResponse(provider="secondary", model="test", text_segments=["dispatcher complete"], usage={"cost_minor": 0})

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


def test_controller_maps_dispatcher_no_route_to_waiting_resource(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-no-route.sqlite3")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0)))
    dispatcher = ProviderDispatcher(ProviderRegistry([FakeProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="no route"))

    assert result.status.value == "waiting_dependency"


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
        audits = store.list_provider_audits()
    assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20
    assert [item["outcome"] for item in audits] == ["succeeded"]
    assert audits[0]["estimated_cost_minor"] == 20


def test_dispatcher_does_not_accept_provider_response_without_observed_cost(tmp_path):
    ledger = ResourceLedger(tmp_path / "paid-missing-cost.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=20)
    ledger.observe("paid", available=10, health="healthy")

    class MissingCostProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["not enough evidence"], usage={})

    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)
    dispatcher = ProviderDispatcher(ProviderRegistry([MissingCostProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="missing cost"))
        intent = store.connection.execute("SELECT status, result_payload FROM effect_intents").fetchone()
        audits = store.list_provider_audits()

    assert result.status is TaskStatus.WAITING_RECONCILIATION
    assert intent["status"] == "unknown"
    assert "cost_minor" in intent["result_payload"]
    assert audits[0]["outcome"] == "budget_reconciliation"
    assert ledger.reservation_totals()["active_reservations"] == 1
    assert ledger.reservation_row(next(iter(ledger.connection.execute("SELECT reservation_id FROM budget_reservations").fetchone()))) ["status"] == "unknown"


