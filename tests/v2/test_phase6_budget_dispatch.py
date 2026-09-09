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
    class CostedFakeProvider(FakeProvider):
        def request(self, request):
            response = super().request(request)
            response.usage["cost_minor"] = 10
            return response

    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo", handler=lambda args: args))
    task = Task(objective="phase 6", limits={"max_cost": 25})
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(CostedFakeProvider(), ToolRuntime(registry), store, resource_policy=control).run(task)
        assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20


def test_controller_reuses_request_and_budget_intent_after_crash_before_intent_persist(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller-dispatch-crash.sqlite3")
    ledger.register_resource("paid", provider_id="direct", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    requests = []

    class DirectProvider(FakeProvider):
        provider_id = "direct"

        def request(self, request):
            requests.append(request.request_id)
            return ModelResponse(provider="direct", model="test", text_segments=["recovered"], usage={"cost_minor": 10})

    provider = DirectProvider()
    task = Task(objective="direct dispatch crash", limits={"max_steps": 1})
    with SQLiteStateStore(tmp_path / "controller-dispatch-state.sqlite3") as store:
        first = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)

        def crash_before_intent(*args, **kwargs):
            raise KeyboardInterrupt("simulated process crash")

        first._prepare_provider_intent = crash_before_intent
        with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
            first.run(task)

        checkpoint = store.load_latest_checkpoint(task.task_id)
        assert checkpoint is not None
        request_id = checkpoint["state"]["active_request_id"]
        assert isinstance(request_id, str) and request_id
        assert store.load_task(task.task_id).status.value == "running"
        assert ledger.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
        assert requests == []

        second = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)
        result = second.resume(task.task_id)

        assert result.status.value == "completed"
        assert requests == [request_id]
        assert ledger.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_controller_replays_succeeded_direct_provider_intent_after_final_commit_crash(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller-replay.sqlite3")
    ledger.register_resource("paid", provider_id="direct", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    requests = []

    class DirectProvider(FakeProvider):
        provider_id = "direct"

        def request(self, request):
            requests.append(request.request_id)
            return ModelResponse(provider="direct", model="test", text_segments=["once"], usage={"cost_minor": 10})

    provider = DirectProvider()
    task = Task(objective="direct provider replay", limits={"max_steps": 1})
    with SQLiteStateStore(tmp_path / "controller-replay-state.sqlite3") as store:
        first = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)
        original_commit = first._commit

        def crash_after_final_transition(**kwargs):
            checkpoint = kwargs.get("checkpoint") or {}
            if checkpoint.get("phase") == "after_model":
                raise KeyboardInterrupt("simulated final commit crash")
            return original_commit(**kwargs)

        first._commit = crash_after_final_transition
        with pytest.raises(KeyboardInterrupt, match="simulated final commit crash"):
            first.run(task)
        assert len(requests) == 1
        assert ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()[0] == "reconciled"

        second = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)
        result = second.resume(task.task_id)

        assert result.status.value == "completed"
        assert len(requests) == 1
        assert ledger.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_controller_holds_direct_dispatching_intent_after_provider_process_crash(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller-dispatching-crash.sqlite3")
    ledger.register_resource("paid", provider_id="direct", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    requests = []

    class CrashingProvider(FakeProvider):
        provider_id = "direct"

        def request(self, request):
            requests.append(request.request_id)
            raise KeyboardInterrupt("simulated provider process crash")

    provider = CrashingProvider()
    task = Task(objective="direct provider process crash", limits={"max_steps": 1})
    with SQLiteStateStore(tmp_path / "controller-dispatching-state.sqlite3") as store:
        first = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)
        with pytest.raises(KeyboardInterrupt, match="simulated provider process crash"):
            first.run(task)
        assert ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()[0] == "dispatching"

        second = Controller(provider, ToolRuntime(ToolRegistry()), store, resource_policy=control)
        result = second.resume(task.task_id)

        assert result.status.value == "waiting_reconciliation"
        assert len(requests) == 1
        assert ledger.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_control_never_converts_generic_float_ceiling_and_requires_missing_charge_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "control.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10, price_currency="JPY")
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}], cost_ceiling=0.5)
    reservation = control.reserve_for_provider("task-1", "remote", request)
    with pytest.raises(BudgetReconciliationRequired, match="usage.cost_minor"):
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


