from collections.abc import Iterable

import pytest

from src.dev_agent.backends import (
    AgentBackend,
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendScope,
    AgentBackendSession,
    AgentBackendStatus,
)


def test_agent_backend_contract_carries_session_events_and_terminal_result():
    scope = AgentBackendScope(workspace_id="worker-001", allowed_paths=("src/example.py",))
    request = AgentBackendRequest(task_id="task-001", objective="inspect one file", scope=scope)
    identity = AgentBackendIdentity(backend_id="fixture", backend_version="1", capabilities=("start", "cancel"))
    session = AgentBackendSession(session_id="session-001", task_id=request.task_id, backend_id=identity.backend_id)
    event = AgentBackendEvent(
        session_id=session.session_id,
        sequence=1,
        event_type="approval.requested",
        status=AgentBackendStatus.WAITING_APPROVAL,
        payload={"approval_id": "approval-001"},
    )
    result = AgentBackendResult(
        session_id=session.session_id,
        status=AgentBackendStatus.COMPLETED,
        output_artifacts=("result.json",),
        reconciliation_metadata={"unknown": False},
    )

    assert request.scope.allowed_paths == ("src/example.py",)
    assert session.backend_id == identity.backend_id
    assert event.status is AgentBackendStatus.WAITING_APPROVAL
    assert result.status is AgentBackendStatus.COMPLETED
    assert result.reconciliation_metadata["unknown"] is False


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: AgentBackendIdentity(backend_id="", backend_version="1"), "backend_id"),
        (lambda: AgentBackendScope(workspace_id="", allowed_paths=()), "workspace_id"),
        (lambda: AgentBackendRequest(task_id="", objective="x", scope=AgentBackendScope(workspace_id="w")), "task_id"),
        (lambda: AgentBackendEvent(session_id="s", sequence=0, event_type="x", status=AgentBackendStatus.RUNNING), "sequence"),
    ],
)
def test_agent_backend_contract_rejects_invalid_boundary_values(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def test_agent_backend_protocol_is_separate_from_model_provider():
    class FixtureBackend:
        identity = AgentBackendIdentity(backend_id="fixture", backend_version="1")

        def start(self, request):
            return AgentBackendSession(session_id="s", task_id=request.task_id, backend_id=self.identity.backend_id)

        def events(self, session_id: str) -> Iterable[AgentBackendEvent]:
            return iter(())

        def cancel(self, session_id: str) -> None:
            return None

        def result(self, session_id: str) -> AgentBackendResult:
            return AgentBackendResult(session_id=session_id, status=AgentBackendStatus.UNKNOWN)

    assert isinstance(FixtureBackend(), AgentBackend)


def test_agent_backend_records_can_rehydrate_enum_statuses_from_durable_json():
    session = AgentBackendSession(session_id="s", task_id="t", backend_id="fixture", status="running")
    event = AgentBackendEvent(session_id="s", sequence=1, event_type="started", status="waiting_approval")
    result = AgentBackendResult(session_id="s", status="completed")

    assert session.status is AgentBackendStatus.RUNNING
    assert event.status is AgentBackendStatus.WAITING_APPROVAL
    assert result.status is AgentBackendStatus.COMPLETED
