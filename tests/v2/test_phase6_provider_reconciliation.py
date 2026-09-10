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



def test_model_turn_executor_bounds_uninterruptible_timeout_threads():
    from concurrent.futures import TimeoutError as FutureTimeoutError
    from threading import Event
    from src.dev_agent.runtime.model_turn import ModelTurnExecutor, ProviderExecutionSaturated

    started = Event()
    release = Event()

    class BlockingProvider(FakeProvider):
        provider_id = "blocking"

        def request(self, request):
            started.set()
            release.wait(2)
            return ModelResponse(provider="blocking", model="test", text_segments=["late"])

    executor = ModelTurnExecutor(
        BlockingProvider(),
        lease_guard=lambda: None,
        max_orphaned_requests=1,
    )
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(FutureTimeoutError):
        executor.request(request, time.time() + 0.05, Event())
    assert started.wait(1)
    assert executor.orphaned_requests == 1

    with pytest.raises(ProviderExecutionSaturated) as saturated:
        executor.request(request, time.time() + 1, Event())
    assert saturated.value.binding_id is None
    assert executor.orphaned_requests == 1

    release.set()
    deadline = time.time() + 2
    while executor.orphaned_requests and time.time() < deadline:
        time.sleep(0.01)
    assert executor.orphaned_requests == 0


def test_model_turn_executor_reports_binding_lane_on_saturation():
    from concurrent.futures import TimeoutError as FutureTimeoutError
    from threading import Event
    from src.dev_agent.runtime.model_turn import ModelTurnExecutor, ProviderExecutionSaturated

    started = Event()
    release = Event()

    class BlockingProvider(FakeProvider):
        provider_id = "binding-lane"

        def request(self, request):
            started.set()
            release.wait(2)
            return ModelResponse(provider="binding-lane", model="test", text_segments=["late"])

    executor = ModelTurnExecutor(
        BlockingProvider(),
        lease_guard=lambda: None,
        binding_id="gemini:worker",
    )
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000013", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(FutureTimeoutError):
        executor.request(request, time.time() + 0.05, Event())
    assert started.wait(1)
    with pytest.raises(ProviderExecutionSaturated) as saturated:
        executor.request(request, time.time() + 1, Event())
    assert saturated.value.binding_id == "gemini:worker"
    release.set()




def test_model_turn_executor_notifies_capacity_after_orphan_finishes():
    from concurrent.futures import TimeoutError as FutureTimeoutError
    from threading import Event
    from src.dev_agent.runtime.model_turn import ModelTurnExecutor

    started = Event()
    release = Event()
    capacity_available = Event()

    class BlockingProvider(FakeProvider):
        provider_id = "blocking-wake"

        def request(self, request):
            started.set()
            release.wait(2)
            return ModelResponse(provider="blocking-wake", model="test", text_segments=["late"])

    executor = ModelTurnExecutor(
        BlockingProvider(),
        lease_guard=lambda: None,
        on_capacity_available=capacity_available.set,
    )
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000003", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(FutureTimeoutError):
        executor.request(request, time.time() + 0.05, Event())
    assert started.wait(1)
    release.set()
    assert capacity_available.wait(2)
    assert executor.orphaned_requests == 0


def test_model_turn_executor_tracks_unconfirmed_cancellation_as_orphan():
    from threading import Event
    from src.dev_agent.runtime.model_turn import ModelTurnExecutor, ProviderExecutionSaturated, ProviderRequestCancelled

    started = Event()
    release = Event()

    class BlockingProvider(FakeProvider):
        provider_id = "blocking-cancel"

        def request(self, request):
            started.set()
            release.wait(2)
            return ModelResponse(provider="blocking-cancel", model="test", text_segments=["late"])

    executor = ModelTurnExecutor(
        BlockingProvider(),
        lease_guard=lambda: None,
        max_orphaned_requests=1,
    )
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000002", messages=[{"role": "user", "content": "x"}])
    cancel_event = Event()

    def cancel_after_start():
        assert started.wait(1)
        cancel_event.set()

    import threading
    canceller = threading.Thread(target=cancel_after_start)
    canceller.start()
    with pytest.raises(ProviderRequestCancelled) as exc_info:
        executor.request(request, time.time() + 1, cancel_event)
    canceller.join(timeout=1)

    assert exc_info.value.unable_to_confirm is True
    assert executor.orphaned_requests == 1
    with pytest.raises(ProviderExecutionSaturated):
        executor.request(request, time.time() + 1, Event())

    release.set()
    deadline = time.time() + 2
    while executor.orphaned_requests and time.time() < deadline:
        time.sleep(0.01)
    assert executor.orphaned_requests == 0


def test_canonical_dispatcher_saturation_is_scoped_to_provider_binding(tmp_path):
    from threading import Event
    from src.dev_agent.runtime.controller import Controller
    from src.dev_agent.providers.base import ProviderError

    ledger = ResourceLedger(tmp_path / "binding-saturation.sqlite3")
    for resource_id, provider_id in (("a-gemini", "gemini"), ("b-cloudflare", "cloudflare")):
        ledger.register_resource(
            resource_id,
            provider_id=provider_id,
            provider_binding_id=provider_id,
            native_unit="request",
            capacity=2,
            capabilities=["text"],
            cost_minor=0,
            quota_domain=f"{provider_id}-domain",
        )
        ledger.observe(resource_id, available=2, health="healthy", concurrency_limit=2)
        ledger.observe_quota(resource_id, request_limit=100, request_remaining=99)
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)))
    started = Event()
    release = Event()
    calls = []

    class HangingGemini(FakeProvider):
        provider_id = "gemini"
        provider_binding_id = "gemini"
        model_id = "gemini-test"

        def request(self, request):
            calls.append("gemini")
            started.set()
            release.wait(2)
            return ModelResponse(provider="gemini", model="gemini-test", text_segments=["late"], usage={"cost_minor": 0})

    class HealthyCloudflare(FakeProvider):
        provider_id = "cloudflare"
        provider_binding_id = "cloudflare"
        model_id = "cloudflare-test"

        def request(self, request):
            calls.append("cloudflare")
            return ModelResponse(provider="cloudflare", model="cloudflare-test", text_segments=["ok"], usage={"cost_minor": 0})

    dispatcher = ProviderDispatcher(
        ProviderRegistry([HangingGemini(), HealthyCloudflare()]),
        control,
    )
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        controller = Controller(dispatcher, ToolRuntime(ToolRegistry()), store)
        first_request = ModelRequest(task_id="00000000-0000-0000-0000-000000000011", messages=[{"role": "user", "content": "hang"}])
        with pytest.raises(ProviderError, match="provider transport failed"):
            controller._provider_request(first_request, time.time() + 0.05, Event())
        assert started.wait(1)
        response = controller._provider_request(
            ModelRequest(task_id="00000000-0000-0000-0000-000000000012", messages=[{"role": "user", "content": "use fallback"}]),
            time.time() + 0.5,
            Event(),
        )
    release.set()
    assert response.provider == "cloudflare"
    assert calls == ["gemini", "cloudflare"]


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


def test_late_provider_completion_reconciles_once_and_resumes_task(tmp_path):
    from threading import Event

    ledger = ResourceLedger(tmp_path / "provider-late-success.sqlite3")
    ledger.register_resource(
        "paid",
        provider_id="slow",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=10,
    )
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(
        ResourceRouter(ledger),
        _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)),
    )
    release = Event()
    completed = Event()
    calls = []

    class SlowProvider(FakeProvider):
        provider_id = "slow"

        def request(self, request):
            calls.append(request.request_id)
            release.wait(2)
            return ModelResponse(
                provider="slow",
                model="test",
                text_segments=["late success"],
                usage={"cost_minor": 10},
            )

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher = ProviderDispatcher(ProviderRegistry([SlowProvider()]), control)
        controller = Controller(dispatcher, ToolRuntime(ToolRegistry()), store)
        task = Task(objective="resume late provider result", limits={"max_wall_time_seconds": 0.03})
        result = controller.run(task)
        assert result.status is TaskStatus.WAITING_RECONCILIATION
        intent = store.connection.execute("SELECT idempotency_key, status FROM effect_intents").fetchone()
        assert intent["status"] == "unknown"

        # Releasing the provider completes the already-started external call;
        # the runtime must reconcile that result, never issue a second call.
        release.set()
        deadline = time.time() + 2
        while time.time() < deadline:
            current = store.connection.execute("SELECT status FROM effect_intents").fetchone()
            if current["status"] == "succeeded":
                completed.set()
                break
            time.sleep(0.01)
        assert completed.is_set()

        resumed = controller.resume(task.task_id)
        reservation = ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()
        audits = store.list_provider_audits(task_id=task.task_id)

    assert resumed.status is TaskStatus.COMPLETED
    assert reservation["status"] == "reconciled"
    assert len(calls) == 1
    assert any(item["outcome"] == "late_succeeded" for item in audits)


def test_late_reconciliation_winning_timeout_race_keeps_terminal_accounting(tmp_path):
    from concurrent.futures import TimeoutError as FutureTimeoutError

    ledger = ResourceLedger(tmp_path / "provider-late-race.sqlite3")
    ledger.register_resource("paid", provider_id="race", native_unit="request", capacity=1, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=1, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))

    class RaceProvider(FakeProvider):
        provider_id = "race"

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        dispatcher = ProviderDispatcher(ProviderRegistry([RaceProvider()]), control)
        dispatcher.bind_runtime(state_store=store)

        def execute(_provider, request, on_late_completion):
            on_late_completion(ModelResponse(provider="race", model="test", text_segments=["already durable"], usage={"cost_minor": 10}))
            raise FutureTimeoutError()

        request = ModelRequest(task_id="00000000-0000-0000-0000-000000000099", messages=[{"role": "user", "content": "race"}])
        with pytest.raises(ProviderError) as raised:
            dispatcher.request_with_execution(request, execute=execute)
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()
        reservation = ledger.connection.execute("SELECT status FROM budget_reservations").fetchone()

    assert raised.value.category == "reconciliation_required"
    assert intent["status"] == "succeeded"
    assert reservation["status"] == "reconciled"


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


@pytest.mark.parametrize(
    ("response_provider", "response_model"),
    (("another-provider", "expected-model"), ("legacy", "another-model")),
)
def test_legacy_provider_response_identity_mismatch_waits_for_reconciliation(
    tmp_path, response_provider, response_model
):
    ledger = ResourceLedger(tmp_path / "legacy-provider-identity.sqlite3")
    ledger.register_resource(
        "paid",
        provider_id="legacy",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=10,
    )
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(
        ResourceRouter(ledger),
        _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)),
    )

    class MismatchedLegacyProvider(FakeProvider):
        provider_id = "legacy"
        model = "expected-model"

        def request(self, request):
            return ModelResponse(
                provider=response_provider,
                model=response_model,
                text_segments=["must not be accepted"],
                usage={"cost_minor": 10},
            )

    with SQLiteStateStore(tmp_path / "legacy-provider-identity-state.sqlite3") as store:
        result = Controller(
            MismatchedLegacyProvider(),
            ToolRuntime(ToolRegistry()),
            store,
            resource_policy=control,
        ).run(Task(objective="legacy identity"))
        intent = store.connection.execute("SELECT status FROM effect_intents").fetchone()

    assert result.status is TaskStatus.WAITING_RECONCILIATION
    assert intent["status"] == "unknown"
    assert ledger.reservation_totals()["active_reservations"] == 1


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


