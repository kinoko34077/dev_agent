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
            time.sleep(0.5)
            return ModelResponse(provider="slow", model="test", text_segments=["late"])

    dispatcher = ProviderDispatcher(ProviderRegistry([SlowProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="dispatcher timeout", limits={"max_wall_time_seconds": 0.25})
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


