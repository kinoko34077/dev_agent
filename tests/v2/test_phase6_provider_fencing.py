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

def test_stale_provider_dispatch_is_fenced_across_independent_processes(tmp_path):
    resource_path = tmp_path / "resources.sqlite3"
    state_path = tmp_path / "shared-state.sqlite3"
    marker_path = tmp_path / "provider-entered.marker"
    ledger = ResourceLedger(resource_path)
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0))
    ledger.close()

    queue = DurableQueue(state_path)
    task = Task(objective="cross-process stale provider")
    with SQLiteStateStore(state_path) as store:
        store.save_task(task)
    queue.enqueue(task.task_id)
    first = queue.claim("worker-a", lease_seconds=0.05)
    time.sleep(0.08)
    second = queue.claim("worker-b", lease_seconds=30)
    assert second.lease_owner == "worker-b"
    proof = first.lease_proof
    assert proof is not None
    queue.close()

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_dispatch_with_stale_lease_in_child,
        args=(str(resource_path), str(state_path), marker_path, ModelRequest(task_id=task.task_id, messages=[{"role": "user", "content": "x"}]).to_dict(), proof.__dict__, result_queue),
    )
    process.start()
    process.join(10)
    assert process.exitcode == 0
    assert result_queue.get(timeout=2)["category"] == "lease_lost"
    assert not marker_path.exists()

    with SQLiteStateStore(state_path) as store:
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()
        assert intent["status"] == "confirmed_failed"
    with ResourceLedger(resource_path) as ledger:
        assert ledger.reservation_totals()["active_reservations"] == 0


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


def test_dispatcher_holds_result_when_lease_is_lost_after_provider_call(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-post-call-lease.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    calls = []
    checks = 0

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["must reconcile"], usage={"cost_minor": 10})

    def lease_guard():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise RuntimeError("stale worker")

    request = ModelRequest(task_id=Task(objective="post-call stale").task_id, messages=[{"role": "user", "content": "hello"}])
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher.bind_runtime(state_store=store, lease_guard=lease_guard)
        with pytest.raises(ProviderError) as exc_info:
            dispatcher.request(request)
        assert exc_info.value.category == "reconciliation_required"
        key = store.connection.execute("SELECT idempotency_key FROM effect_intents").fetchone()[0]
        assert store.get_effect_intent(key)["status"] == "unknown"

    assert calls == [request.request_id]
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_direct_provider_holds_result_when_lease_is_lost_after_provider_call(tmp_path):
    ledger = ResourceLedger(tmp_path / "direct-post-call-lease.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    checks = 0

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["must reconcile"], usage={"cost_minor": 10})

    def lease_guard():
        nonlocal checks
        checks += 1
        if checks == 4:
            raise RuntimeError("stale worker")

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="direct post-call stale")
        result = Controller(PaidProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control, lease_guard=lease_guard).run(task)
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status.value == "waiting_reconciliation"
    assert intent["status"] == "unknown"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_dispatch_intent_transition_fences_reclaimed_queue_lease(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-lease-proof-resources.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    calls = []

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            calls.append(request.request_id)
            return ModelResponse(provider="paid", model="test", text_segments=["must not run"], usage={"cost_minor": 10})

    queue_path = tmp_path / "shared-queue-state.sqlite3"
    queue = DurableQueue(queue_path)
    task = Task(objective="reclaimed lease")
    queue.enqueue(task.task_id)
    first = queue.claim("worker-a", lease_seconds=1)
    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)
    with SQLiteStateStore(queue_path) as store:
        store.save_task(task)
        queue.claim("worker-b", now=first.lease_until.timestamp() + 1, lease_seconds=30)
        dispatcher.bind_runtime(state_store=store, lease_proof=lambda: first.lease_proof)
        request = ModelRequest(task_id=task.task_id, messages=[{"role": "user", "content": "hello"}])
        with pytest.raises(ProviderError, match="stale lease") as exc_info:
            dispatcher.request(request)
        assert exc_info.value.category == "lease_lost"
        key = store.connection.execute("SELECT idempotency_key FROM effect_intents").fetchone()[0]
        assert store.get_effect_intent(key)["status"] == "confirmed_failed"

    assert calls == []
    assert ledger.reservation_totals()["active_reservations"] == 0


