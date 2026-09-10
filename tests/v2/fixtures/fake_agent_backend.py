from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from src.dev_agent.backends import (
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendSession,
    AgentBackendStatus,
)


class FakeAgentBackend:
    """Deterministic test backend; it has no production state authority."""

    identity = AgentBackendIdentity(backend_id="fake", backend_version="1")

    def __init__(self, *, events: Iterable[AgentBackendEvent] = (), result_status: AgentBackendStatus = AgentBackendStatus.COMPLETED) -> None:
        self._events = list(events)
        self._result_status = result_status
        self.result_value: AgentBackendResult | None = None
        self.session_id: str | None = None
        self.start_calls = 0
        self.cancel_calls: list[str] = []

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        self.start_calls += 1
        self.session_id = str(uuid4())
        self.result_value = AgentBackendResult(session_id=self.session_id, status=self._result_status)
        return AgentBackendSession(session_id=self.session_id, task_id=request.task_id, backend_id=self.identity.backend_id, status=AgentBackendStatus.RUNNING)

    def events(self, session_id: str) -> Iterable[AgentBackendEvent]:
        if session_id != self.session_id:
            raise RuntimeError("unknown session")
        return tuple(self._events)

    def cancel(self, session_id: str) -> None:
        if session_id != self.session_id:
            raise RuntimeError("unknown session")
        self.cancel_calls.append(session_id)

    def result(self, session_id: str) -> AgentBackendResult:
        if session_id != self.session_id or self.result_value is None:
            raise RuntimeError("unknown session")
        return self.result_value

    def rebind_event_sessions(self, session_id: str) -> None:
        self._events = [
            AgentBackendEvent(session_id=session_id, sequence=event.sequence, event_type=event.event_type, status=event.status, payload=event.payload)
            for event in self._events
        ]
