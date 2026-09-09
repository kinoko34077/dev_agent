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


def test_paid_provider_server_error_waits_for_reconciliation(tmp_path):
    ledger = ResourceLedger(tmp_path / "provider-server-error.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("provider returned HTTP 503", category="provider_http", retryable=False, http_status=503)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(BrokenProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control).run(Task(objective="server error"))
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status is TaskStatus.WAITING_RECONCILIATION
    assert intent["status"] == "unknown"
    assert ledger.reservation_totals()["active_reservations"] == 1


def test_dispatcher_server_error_keeps_budget_unknown(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-server-error.sqlite3")
    ledger.register_resource("paid", provider_id="broken", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class BrokenProvider(FakeProvider):
        provider_id = "broken"

        def request(self, request):
            raise ProviderError("provider returned HTTP 503", category="provider_http", retryable=False, http_status=503)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher = ProviderDispatcher(ProviderRegistry([BrokenProvider()]), control)
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="dispatcher server error"))
        reservation = ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status is TaskStatus.WAITING_RECONCILIATION
    assert reservation["status"] == "unknown"
    assert intent["status"] == "unknown"


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
        audits = store.list_provider_audits()

    assert result.status.value == "completed"
    assert len(intents) == 1
    assert intents[0]["tool_name"] == "provider:paid"
    assert intents[0]["status"] == "succeeded"
    assert '"resource_id": "paid"' in intents[0]["result_payload"]
    assert len(audits) == 1
    assert audits[0]["provider_id"] == "paid"
    assert audits[0]["resource_id"] == "paid"
    assert audits[0]["estimated_cost_minor"] == 10
    assert audits[0]["outcome"] == "succeeded"


def test_dispatcher_holds_result_when_durable_success_audit_fails(tmp_path):
    ledger = ResourceLedger(tmp_path / "dispatcher-audit-failure.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["durable"], usage={"cost_minor": 10})

    dispatcher = ProviderDispatcher(ProviderRegistry([PaidProvider()]), control)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        original_record_audit = store.record_provider_audit

        def fail_durable_audit(**kwargs):
            if kwargs.get("outcome") == "succeeded":
                raise OSError("audit store unavailable")
            return original_record_audit(**kwargs)

        store.record_provider_audit = fail_durable_audit
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="audit persistence"))
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status.value == "waiting_reconciliation"
    assert intent["status"] == "succeeded"
    assert ledger.reservation_totals()["active_reservations"] == 0
    assert dispatcher.audits == []


def test_direct_provider_holds_result_when_durable_success_audit_fails(tmp_path):
    ledger = ResourceLedger(tmp_path / "direct-audit-failure.sqlite3")
    ledger.register_resource("paid", provider_id="paid", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class PaidProvider(FakeProvider):
        provider_id = "paid"

        def request(self, request):
            return ModelResponse(provider="paid", model="test", text_segments=["durable"], usage={"cost_minor": 10})

    class AuditFailureController(Controller):
        def _record_provider_audit(self, request, reservation, outcome, intent_key, *, details=None):
            if outcome == "succeeded":
                raise OSError("audit store unavailable")
            return super()._record_provider_audit(request, reservation, outcome, intent_key, details=details)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = AuditFailureController(PaidProvider(), ToolRuntime(ToolRegistry()), store, resource_policy=control).run(Task(objective="direct audit persistence"))
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status.value == "waiting_reconciliation"
    assert intent["status"] == "succeeded"
    assert ledger.reservation_totals()["active_reservations"] == 0


