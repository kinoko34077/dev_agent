from __future__ import annotations

from uuid import uuid4

import pytest

from src.dev_agent.backends import (
    BackendAdmission,
    AgentBackendDispatcher,
    AgentBackendDispatchError,
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendScope,
    AgentBackendStatus,
    BackendDispatchUncertain,
)
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from tests.v2.fixtures.fake_agent_backend import FakeAgentBackend


class _DiscoverableBackend(FakeAgentBackend):
    def __init__(self, sessions, **kwargs):
        super().__init__(**kwargs)
        self._sessions = sessions

    def start(self, request):
        session = super().start(request)
        self._sessions[request.client_session_key] = session
        return session

    def discover(self, client_session_key):
        return self._sessions.get(client_session_key)


def _request(task_id: str) -> AgentBackendRequest:
    return AgentBackendRequest(
        task_id=task_id,
        objective="bounded backend task",
        scope=AgentBackendScope(workspace_id="isolated-001", allowed_paths=("src/example.py",)),
        input_artifacts=("artifact://input-001",),
        metadata={"approval_id": "approval-001"},
    )


@pytest.fixture
def store(tmp_path):
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        yield state


@pytest.fixture
def task(store):
    value = Task(objective="bounded backend task", status=TaskStatus.READY)
    store.save_task(value)
    return value


def _dispatcher(store, *, authorize=None):
    def admission(task, request, identity):
        return BackendAdmission(
            task_id=identity.task_id,
            dispatch_id=identity.dispatch_id,
            workspace_id=identity.workspace_id,
            allowed_paths=identity.allowed_paths,
            sensitivity=request.sensitivity,
            lease_proof_ref=f"lease:{identity.dispatch_id}",
            budget_admission_ref=f"budget:{identity.dispatch_id}",
            approval_ref=f"approval:{identity.dispatch_id}",
            lease_admitted=True,
            budget_admitted=True,
            approval_granted=True,
            privacy_allowed=True,
            allowed_capabilities=tuple(task.required_capabilities),
        )

    return AgentBackendDispatcher(
        store,
        authorize=authorize or (lambda task, request: True),
        admission=admission,
    )


def test_dispatch_persists_identity_and_does_not_restart_existing_session(store, task):
    backend = FakeAgentBackend()
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    dispatcher = _dispatcher(store)

    session = dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    dispatcher.result(dispatch_id, backend)
    same_session = _dispatcher(store).dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)

    assert session.session_id == same_session.session_id
    assert backend.start_calls == 1
    intent = store.get_effect_intent(dispatcher.effect_key(dispatch_id))
    assert intent["arguments"]["task_id"] == task.task_id
    assert intent["arguments"]["backend_id"] == "fake"
    assert intent["result"]["session"]["session_id"] == session.session_id
    assert intent["arguments"]["attempt"] == 1
    assert intent["arguments"]["request_fingerprint"]


def test_start_persistence_crash_is_recovered_by_client_session_discovery(store, task, monkeypatch):
    sessions = {}
    backend = _DiscoverableBackend(sessions)
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    dispatcher = _dispatcher(store)
    original_transition = store.transition_effect_intent
    crashed = False

    def fail_session_persistence(key, *, to_status, result, lease_proof=None):
        nonlocal crashed
        if not crashed and isinstance(result, dict) and "session" in result:
            crashed = True
            raise RuntimeError("simulated process death before session persistence")
        return original_transition(key, to_status=to_status, result=result, lease_proof=lease_proof)

    monkeypatch.setattr(store, "transition_effect_intent", fail_session_persistence)
    with pytest.raises(RuntimeError, match="process death"):
        dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)

    intent = store.get_effect_intent(dispatcher.effect_key(dispatch_id))
    assert intent["status"] == "dispatching"
    assert intent["result"] == {"request_fingerprint": intent["result"]["request_fingerprint"]}

    monkeypatch.setattr(store, "transition_effect_intent", original_transition)
    recovered = _dispatcher(store).reconcile_start(
        dispatch_id,
        _DiscoverableBackend(sessions),
        actor="operator",
        source="restart-reconcile",
    )

    assert recovered.session_id == next(iter(sessions.values())).session_id
    assert recovered.task_id == task.task_id
    assert store.get_effect_intent(dispatcher.effect_key(dispatch_id))["result"]["session"]["session_id"] == recovered.session_id
    assert store.has_event(task.task_id, "agent_backend.session_discovered")


def test_start_persistence_crash_without_discovery_becomes_unknown_without_restart(store, task, monkeypatch):
    backend = FakeAgentBackend()
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    dispatcher = _dispatcher(store)
    original_transition = store.transition_effect_intent
    crashed = False

    def fail_session_persistence(key, *, to_status, result, lease_proof=None):
        nonlocal crashed
        if not crashed and isinstance(result, dict) and "session" in result:
            crashed = True
            raise RuntimeError("simulated process death")
        return original_transition(key, to_status=to_status, result=result, lease_proof=lease_proof)

    monkeypatch.setattr(store, "transition_effect_intent", fail_session_persistence)
    with pytest.raises(RuntimeError, match="process death"):
        dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    monkeypatch.setattr(store, "transition_effect_intent", original_transition)

    with pytest.raises(BackendDispatchUncertain, match="discovery"):
        dispatcher.reconcile_start(dispatch_id, FakeAgentBackend(), actor="operator", source="restart-reconcile")

    assert store.get_effect_intent(dispatcher.effect_key(dispatch_id))["status"] == "unknown"
    assert backend.start_calls == 1


def test_dispatch_rejects_missing_task_scope_and_authority_before_backend_start(store):
    backend = FakeAgentBackend()
    missing_task = str(uuid4())
    with pytest.raises(AgentBackendDispatchError, match="task"):
        _dispatcher(store).dispatch(_request(missing_task), backend, dispatch_id=str(uuid4()), attempt=1)

    task = Task(objective="scope check", status=TaskStatus.READY)
    store.save_task(task)
    unauthorized = _dispatcher(store, authorize=lambda task, request: False)
    with pytest.raises(AgentBackendDispatchError, match="authority"):
        unauthorized.dispatch(_request(task.task_id), backend, dispatch_id=str(uuid4()), attempt=1)
    assert backend.start_calls == 0


def test_dispatch_rejects_unknown_authority_result_before_backend_start(store, task):
    backend = FakeAgentBackend()
    request = _request(task.task_id)
    dispatcher = AgentBackendDispatcher(store, authorize=lambda task, request: None)

    with pytest.raises(AgentBackendDispatchError, match="authority"):
        dispatcher.dispatch(request, backend, dispatch_id=str(uuid4()), attempt=1)

    assert backend.start_calls == 0


def test_dispatch_requires_typed_admission_evidence_even_when_boolean_authorized(store, task):
    backend = FakeAgentBackend()
    request = _request(task.task_id)
    dispatcher = AgentBackendDispatcher(store, authorize=lambda task, request: True)

    with pytest.raises(AgentBackendDispatchError, match="admission"):
        dispatcher.dispatch(request, backend, dispatch_id=str(uuid4()), attempt=1)

    assert backend.start_calls == 0


def test_dispatch_rejects_admission_that_does_not_cover_task_capabilities(store):
    backend = FakeAgentBackend()
    task = Task(
        objective="backend capability check",
        status=TaskStatus.READY,
        required_capabilities=["coding"],
    )
    store.save_task(task)
    request = _request(task.task_id)

    def admission(task, request, identity):
        return BackendAdmission(
            task_id=identity.task_id,
            dispatch_id=identity.dispatch_id,
            workspace_id=identity.workspace_id,
            allowed_paths=identity.allowed_paths,
            sensitivity=request.sensitivity,
            lease_proof_ref=f"lease:{identity.dispatch_id}",
            budget_admission_ref=f"budget:{identity.dispatch_id}",
            approval_ref=f"approval:{identity.dispatch_id}",
            lease_admitted=True,
            budget_admitted=True,
            approval_granted=True,
            privacy_allowed=True,
            allowed_capabilities=(),
        )

    dispatcher = AgentBackendDispatcher(store, authorize=lambda task, request: True, admission=admission)
    with pytest.raises(AgentBackendDispatchError, match="capabilities"):
        dispatcher.dispatch(request, backend, dispatch_id=str(uuid4()), attempt=1)

    assert backend.start_calls == 0


@pytest.mark.parametrize("field", ("lease_admitted", "budget_admitted", "approval_granted", "privacy_allowed"))
def test_dispatch_rejects_admission_without_explicit_authority_flag(store, task, field):
    backend = FakeAgentBackend()
    request = _request(task.task_id)

    def admission(task, request, identity):
        values = {
            "task_id": identity.task_id,
            "dispatch_id": identity.dispatch_id,
            "workspace_id": identity.workspace_id,
            "allowed_paths": identity.allowed_paths,
            "sensitivity": request.sensitivity,
            "lease_proof_ref": f"lease:{identity.dispatch_id}",
            "budget_admission_ref": f"budget:{identity.dispatch_id}",
            "approval_ref": f"approval:{identity.dispatch_id}",
            "lease_admitted": True,
            "budget_admitted": True,
            "approval_granted": True,
            "privacy_allowed": True,
            "allowed_capabilities": tuple(task.required_capabilities),
        }
        values[field] = False
        return BackendAdmission(**values)

    dispatcher = AgentBackendDispatcher(store, authorize=lambda task, request: True, admission=admission)
    with pytest.raises(AgentBackendDispatchError, match="admission"):
        dispatcher.dispatch(request, backend, dispatch_id=str(uuid4()), attempt=1)

    assert backend.start_calls == 0


def test_dispatch_rejects_backend_identity_that_lacks_task_capabilities(store):
    class _TextOnlyBackend(FakeAgentBackend):
        identity = AgentBackendIdentity(backend_id="text-only", backend_version="1", capabilities=("text",))

    backend = _TextOnlyBackend()
    task = Task(
        objective="backend identity capability check",
        status=TaskStatus.READY,
        required_capabilities=["coding"],
    )
    store.save_task(task)

    with pytest.raises(AgentBackendDispatchError, match="capabilities"):
        _dispatcher(store).dispatch(_request(task.task_id), backend, dispatch_id=str(uuid4()), attempt=1)

    assert backend.start_calls == 0


def test_dispatch_rejects_authority_callback_exception_before_backend_start(store, task):
    backend = FakeAgentBackend()
    request = _request(task.task_id)

    def broken_authority(task, request):
        raise RuntimeError("authority service unavailable")

    with pytest.raises(AgentBackendDispatchError, match="authority"):
        AgentBackendDispatcher(store, authorize=broken_authority).dispatch(
            request,
            backend,
            dispatch_id=str(uuid4()),
            attempt=1,
        )

    assert backend.start_calls == 0


@pytest.mark.parametrize("path", ("../outside.py", "/absolute.py", ".env", ".git/config", "secrets/key.pem"))
def test_dispatch_rejects_unscoped_or_protected_paths(store, task, path):
    request = AgentBackendRequest(
        task_id=task.task_id,
        objective="scope check",
        scope=AgentBackendScope(workspace_id="isolated-001", allowed_paths=(path,)),
    )
    with pytest.raises(AgentBackendDispatchError, match="scope"):
        _dispatcher(store).dispatch(request, FakeAgentBackend(), dispatch_id=str(uuid4()), attempt=1)


def test_events_are_ordered_durable_and_deduplicated_after_dispatcher_restart(store, task):
    backend = FakeAgentBackend(
        events=(
            AgentBackendEvent(session_id="pending", sequence=1, event_type="started", status=AgentBackendStatus.RUNNING),
            AgentBackendEvent(session_id="pending", sequence=2, event_type="approval.requested", status=AgentBackendStatus.WAITING_APPROVAL),
        )
    )
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    dispatcher = _dispatcher(store)
    session = dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    backend.rebind_event_sessions(session.session_id)

    first = dispatcher.events(dispatch_id, backend)
    second = _dispatcher(store).events(dispatch_id, backend)

    assert [event.sequence for event in first] == [1, 2]
    assert second == ()
    persisted = [event for event in store.snapshot()["events"] if event["event_type"] == "agent_backend.event"]
    assert [event["payload"]["sequence"] for event in persisted] == [1, 2]


def test_conflicting_event_sequence_is_rejected_before_any_event_is_persisted(store, task):
    backend = FakeAgentBackend(
        events=(
            AgentBackendEvent(session_id="pending", sequence=1, event_type="started", status=AgentBackendStatus.RUNNING),
            AgentBackendEvent(session_id="pending", sequence=1, event_type="tampered", status=AgentBackendStatus.FAILED),
        )
    )
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    dispatcher = _dispatcher(store)
    session = dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    backend.rebind_event_sessions(session.session_id)

    with pytest.raises(AgentBackendDispatchError, match="sequence"):
        dispatcher.events(dispatch_id, backend)

    assert not [event for event in store.snapshot()["events"] if event["event_type"] == "agent_backend.event"]


def test_result_completed_is_durable_and_unknown_is_not_replayed(store, task):
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    backend = FakeAgentBackend(result_status=AgentBackendStatus.UNKNOWN)
    dispatcher = _dispatcher(store)
    session = dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)

    result = dispatcher.result(dispatch_id, backend)
    assert result.status is AgentBackendStatus.UNKNOWN
    assert store.get_effect_intent(dispatcher.effect_key(dispatch_id))["status"] == "unknown"
    with pytest.raises(BackendDispatchUncertain):
        _dispatcher(store).dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    assert backend.start_calls == 1
    assert session.session_id == backend.session_id


def test_explicit_reconciliation_can_confirm_result_without_restarting_backend(store, task):
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    backend = FakeAgentBackend(result_status=AgentBackendStatus.UNKNOWN)
    dispatcher = _dispatcher(store)
    dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)
    dispatcher.result(dispatch_id, backend)
    backend.result_value = AgentBackendResult(session_id=backend.session_id, status=AgentBackendStatus.COMPLETED, output_artifacts=("artifact://patch",))

    result = dispatcher.reconcile(dispatch_id, backend, actor="operator", source="codex-review")

    assert result.status is AgentBackendStatus.COMPLETED
    assert store.get_effect_intent(dispatcher.effect_key(dispatch_id))["status"] == "succeeded"
    assert _dispatcher(store).result(dispatch_id, backend).status is AgentBackendStatus.COMPLETED
    assert backend.start_calls == 1


def test_cancel_is_forwarded_and_backend_failure_becomes_unknown(store, task):
    request = _request(task.task_id)
    dispatch_id = str(uuid4())
    backend = FakeAgentBackend()
    dispatcher = _dispatcher(store)
    dispatcher.dispatch(request, backend, dispatch_id=dispatch_id, attempt=1)

    dispatcher.cancel(dispatch_id, backend)

    assert backend.cancel_calls == [backend.session_id]
    assert store.has_event(task.task_id, "agent_backend.cancel_requested")
