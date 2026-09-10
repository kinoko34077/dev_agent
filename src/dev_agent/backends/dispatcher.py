"""Authority boundary for delegating work to an external AgentBackend.

This module deliberately does not implement a scheduler or a second task state
machine.  The existing StateStore effect-intent lifecycle is the durable
identity for one backend dispatch; backend-specific sessions remain opaque at
this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from ..domain.protocol import Event, Task, TaskStatus
from ..security.protected_paths import is_protected_path
from ..state.store import StateStore
from .protocol import (
    AgentBackend,
    AgentBackendEvent,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendSession,
    AgentBackendStatus,
)


class AgentBackendDispatchError(RuntimeError):
    """The Control Plane rejected an external backend dispatch."""


class BackendDispatchUncertain(AgentBackendDispatchError):
    """The external outcome is not safe to replay without reconciliation."""


@dataclass(frozen=True)
class AgentBackendDispatchIdentity:
    task_id: str
    backend_id: str
    dispatch_id: str
    attempt: int
    workspace_id: str
    allowed_paths: tuple[str, ...]
    request_fingerprint: str


AuthorizeBackend = Callable[[Task, AgentBackendRequest], bool | None]


@dataclass(frozen=True)
class BackendAdmission:
    """Typed evidence assembled by existing Control Plane authorities.

    The dispatcher does not mint lease, budget, approval, or privacy
    authority.  It requires the caller to provide non-empty references for
    each decision and verifies that the evidence is bound to this exact task,
    dispatch, workspace, and sensitivity before crossing the backend effect
    boundary.
    """

    task_id: str
    dispatch_id: str
    workspace_id: str
    allowed_paths: tuple[str, ...]
    sensitivity: str
    lease_proof_ref: str
    budget_admission_ref: str
    approval_ref: str
    allowed_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "task_id",
            "dispatch_id",
            "workspace_id",
            "lease_proof_ref",
            "budget_admission_ref",
            "approval_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        paths = tuple(path.strip() for path in self.allowed_paths if isinstance(path, str) and path.strip())
        if len(paths) != len(self.allowed_paths):
            raise ValueError("allowed_paths must contain non-empty strings")
        object.__setattr__(self, "allowed_paths", paths)
        sensitivity = self.sensitivity.strip().lower() if isinstance(self.sensitivity, str) else ""
        if sensitivity not in {"public", "normal", "internal", "sensitive"}:
            raise ValueError("sensitivity must be one of public, normal, internal, or sensitive")
        object.__setattr__(self, "sensitivity", sensitivity)
        capabilities = tuple(item.strip() for item in self.allowed_capabilities if isinstance(item, str) and item.strip())
        if len(capabilities) != len(self.allowed_capabilities):
            raise ValueError("allowed_capabilities must contain non-empty strings")
        object.__setattr__(self, "allowed_capabilities", capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "dispatch_id": self.dispatch_id,
            "workspace_id": self.workspace_id,
            "allowed_paths": list(self.allowed_paths),
            "sensitivity": self.sensitivity,
            "lease_proof_ref": self.lease_proof_ref,
            "budget_admission_ref": self.budget_admission_ref,
            "approval_ref": self.approval_ref,
            "allowed_capabilities": list(self.allowed_capabilities),
        }


BackendAdmissionCallback = Callable[[Task, AgentBackendRequest, AgentBackendDispatchIdentity], BackendAdmission | None]


class AgentBackendDispatcher:
    """Delegate one scoped backend session after Control Plane checks.

    ``authorize`` is intentionally injected and defaults to deny.  A caller
    must connect the existing task/approval/budget/permission authority here;
    this class never creates a parallel authority implementation.
    """

    _ACTIVE_TASK_STATES = {
        TaskStatus.QUEUED,
        TaskStatus.PLANNING,
        TaskStatus.READY,
        TaskStatus.RUNNING,
    }
    _TERMINAL_EFFECT_STATES = {"succeeded", "confirmed_failed"}
    _UNCERTAIN_EFFECT_STATES = {"unknown", "reconciling"}
    def __init__(
        self,
        store: StateStore,
        *,
        authorize: AuthorizeBackend | None = None,
        admission: BackendAdmissionCallback | None = None,
    ) -> None:
        self._store = store
        self._authorize = authorize or (lambda task, request: False)
        self._admission = admission or (lambda task, request, identity: None)

    @staticmethod
    def effect_key(dispatch_id: str) -> str:
        if not isinstance(dispatch_id, str) or not dispatch_id.strip():
            raise AgentBackendDispatchError("dispatch_id must be a non-empty string")
        return f"agent-backend:{dispatch_id.strip()}"

    def dispatch(
        self,
        request: AgentBackendRequest,
        backend: AgentBackend,
        *,
        dispatch_id: str,
        attempt: int,
    ) -> AgentBackendSession:
        task, identity, fingerprint, key, admission = self._validate(request, backend, dispatch_id=dispatch_id, attempt=attempt)
        expected = {
            **self._identity_arguments(identity),
            "admission": admission.to_dict(),
        }
        existing = self._store.get_effect_intent(key)
        if existing is not None:
            self._verify_existing(existing, expected)
            status = existing.get("status")
            if status in self._UNCERTAIN_EFFECT_STATES:
                raise BackendDispatchUncertain(f"backend dispatch outcome is {status}: {dispatch_id}")
            if status in self._TERMINAL_EFFECT_STATES:
                session = self._session_from_intent(existing)
                if session is None:
                    raise AgentBackendDispatchError("completed backend dispatch has no durable session")
                return session
            if status == "dispatching":
                session = self._session_from_intent(existing)
                if session is None:
                    raise BackendDispatchUncertain(f"backend dispatch has no durable session: {dispatch_id}")
                return session
            if status not in {"pending", "prepared"}:
                raise AgentBackendDispatchError(f"backend dispatch cannot resume from {status}")
        else:
            if not self._store.create_effect_intent(key, task_id=task.task_id, tool_name="agent_backend_dispatch", arguments=expected):
                raise AgentBackendDispatchUncertain(f"backend dispatch identity raced: {dispatch_id}")

        # Persist intent before crossing the external side-effect boundary.
        self._store.transition_effect_intent(key, to_status="prepared", result={"request_fingerprint": fingerprint})
        self._store.transition_effect_intent(key, to_status="dispatching", result={"request_fingerprint": fingerprint})
        self._append_event(
            task,
            "agent_backend.dispatching",
            {**expected, "request_fingerprint": fingerprint},
        )
        try:
            session = backend.start(request)
            self._validate_session(session, request, backend)
        except BaseException as exc:
            self._mark_unknown(key, task, "agent_backend.start_unknown", {"error_type": type(exc).__name__})
            raise BackendDispatchUncertain(f"backend start outcome is unknown: {dispatch_id}") from exc

        session_payload = {"session": session.__dict__, "request_fingerprint": fingerprint}
        self._store.transition_effect_intent(key, to_status="dispatching", result=session_payload)
        self._append_event(task, "agent_backend.started", {**expected, "backend_session_id": session.session_id})
        return session

    def events(self, dispatch_id: str, backend: AgentBackend) -> tuple[AgentBackendEvent, ...]:
        key = self.effect_key(dispatch_id)
        intent = self._load_intent(key)
        session = self._session_from_intent(intent)
        if session is None:
            raise BackendDispatchUncertain(f"backend dispatch has no durable session: {dispatch_id}")
        if intent["status"] in self._TERMINAL_EFFECT_STATES:
            return ()
        try:
            incoming = tuple(backend.events(session.session_id))
            by_sequence: dict[int, AgentBackendEvent] = {}
            for event in incoming:
                if not isinstance(event, AgentBackendEvent) or event.session_id != session.session_id:
                    raise AgentBackendDispatchError("backend returned an event for another session")
                prior = by_sequence.get(event.sequence)
                if prior is not None and self._event_fingerprint(prior) != self._event_fingerprint(event):
                    raise AgentBackendDispatchError(f"backend event sequence conflict: {event.sequence}")
                by_sequence[event.sequence] = event
            normalized = [by_sequence[sequence] for sequence in sorted(by_sequence)]
        except BackendDispatchUncertain:
            raise
        except AgentBackendDispatchError:
            raise
        except BaseException as exc:
            self._mark_unknown(key, self._task_for_intent(intent), "agent_backend.events_unknown", {"error_type": type(exc).__name__})
            raise BackendDispatchUncertain(f"backend event outcome is unknown: {dispatch_id}") from exc

        existing = self._persisted_event_sequences(self._task_for_intent(intent).task_id, dispatch_id)
        new_events: list[AgentBackendEvent] = []
        for event in normalized:
            prior = existing.get(event.sequence)
            if prior is not None:
                if prior != self._event_fingerprint(event):
                    raise AgentBackendDispatchError(f"backend event sequence conflict: {event.sequence}")
                continue
            self._append_event(
                self._task_for_intent(intent),
                "agent_backend.event",
                {
                    "dispatch_id": dispatch_id,
                    "backend_session_id": session.session_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "status": event.status.value if event.status else None,
                    "payload": dict(event.payload),
                },
            )
            existing[event.sequence] = self._event_fingerprint(event)
            new_events.append(event)
        return tuple(new_events)

    def result(self, dispatch_id: str, backend: AgentBackend) -> AgentBackendResult:
        key = self.effect_key(dispatch_id)
        intent = self._load_intent(key)
        task = self._task_for_intent(intent)
        if intent["status"] in self._UNCERTAIN_EFFECT_STATES:
            raise BackendDispatchUncertain(f"explicit reconciliation is required: {dispatch_id}")
        if intent["status"] in self._TERMINAL_EFFECT_STATES:
            result = self._result_from_intent(intent)
            if result is None:
                raise AgentBackendDispatchError("terminal backend dispatch has no durable result")
            return result
        session = self._session_from_intent(intent)
        if session is None:
            raise BackendDispatchUncertain(f"backend dispatch has no durable session: {dispatch_id}")
        try:
            result = backend.result(session.session_id)
            self._validate_result(result, session)
        except BaseException as exc:
            self._mark_unknown(key, task, "agent_backend.result_unknown", {"error_type": type(exc).__name__})
            return AgentBackendResult(session_id=session.session_id, status=AgentBackendStatus.UNKNOWN, reconciliation_metadata={"error_type": type(exc).__name__})

        payload = {"session": session.__dict__, "backend_result": result.__dict__}
        if result.status is AgentBackendStatus.COMPLETED:
            effect_status = "succeeded"
        elif result.status is AgentBackendStatus.FAILED or result.status is AgentBackendStatus.CANCELLED:
            effect_status = "confirmed_failed"
        elif result.status is AgentBackendStatus.UNKNOWN:
            effect_status = "unknown"
        elif result.status is AgentBackendStatus.RECONCILING:
            effect_status = "reconciling"
        else:
            self._store.transition_effect_intent(key, to_status="dispatching", result=payload)
            self._append_event(task, "agent_backend.result_pending", {"dispatch_id": dispatch_id, "status": result.status.value})
            return result
        self._store.transition_effect_intent(key, to_status=effect_status, result=payload)
        self._append_event(task, "agent_backend.result", {"dispatch_id": dispatch_id, "status": result.status.value})
        return result

    def cancel(self, dispatch_id: str, backend: AgentBackend) -> None:
        key = self.effect_key(dispatch_id)
        intent = self._load_intent(key)
        if intent["status"] in self._TERMINAL_EFFECT_STATES:
            return
        if intent["status"] in self._UNCERTAIN_EFFECT_STATES:
            raise BackendDispatchUncertain(f"explicit reconciliation is required: {dispatch_id}")
        session = self._session_from_intent(intent)
        if session is None:
            raise BackendDispatchUncertain(f"backend dispatch has no durable session: {dispatch_id}")
        task = self._task_for_intent(intent)
        try:
            backend.cancel(session.session_id)
        except BaseException as exc:
            self._mark_unknown(key, task, "agent_backend.cancel_unknown", {"error_type": type(exc).__name__})
            raise BackendDispatchUncertain(f"backend cancellation outcome is unknown: {dispatch_id}") from exc
        self._append_event(task, "agent_backend.cancel_requested", {"dispatch_id": dispatch_id, "backend_session_id": session.session_id})

    def reconcile(self, dispatch_id: str, backend: AgentBackend, *, actor: str, source: str) -> AgentBackendResult:
        if not isinstance(actor, str) or not actor.strip() or not isinstance(source, str) or not source.strip():
            raise AgentBackendDispatchError("reconciliation actor and source are required")
        key = self.effect_key(dispatch_id)
        intent = self._load_intent(key)
        if intent["status"] not in self._UNCERTAIN_EFFECT_STATES:
            raise AgentBackendDispatchError(f"dispatch is not awaiting reconciliation: {dispatch_id}")
        session = self._session_from_intent(intent)
        if session is None:
            raise BackendDispatchUncertain(f"cannot reconcile dispatch without session: {dispatch_id}")
        try:
            result = backend.result(session.session_id)
            self._validate_result(result, session)
        except BaseException as exc:
            result = AgentBackendResult(session_id=session.session_id, status=AgentBackendStatus.UNKNOWN, reconciliation_metadata={"error_type": type(exc).__name__})
        status = "succeeded" if result.status is AgentBackendStatus.COMPLETED else "confirmed_failed" if result.status in {AgentBackendStatus.FAILED, AgentBackendStatus.CANCELLED} else "unknown"
        self._store.reconcile_effect_intent(
            key,
            status=status,
            actor=actor.strip(),
            source=source.strip(),
            external_id=session.session_id,
            evidence={"backend_result": result.__dict__},
        )
        self._append_event(self._task_for_intent(intent), "agent_backend.reconciled", {"dispatch_id": dispatch_id, "status": result.status.value, "actor": actor.strip(), "source": source.strip()})
        return result

    def _validate(
        self,
        request: AgentBackendRequest,
        backend: AgentBackend,
        *,
        dispatch_id: str,
        attempt: int,
    ) -> tuple[Task, AgentBackendDispatchIdentity, str, str, BackendAdmission]:
        if not isinstance(request, AgentBackendRequest):
            raise AgentBackendDispatchError("request must be an AgentBackendRequest")
        if not isinstance(backend, AgentBackend):
            raise AgentBackendDispatchError("backend does not implement AgentBackend")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
            raise AgentBackendDispatchError("attempt must be a positive integer")
        task = self._store.load_task(request.task_id)
        if task is None:
            raise AgentBackendDispatchError(f"task not found: {request.task_id}")
        if task.status not in self._ACTIVE_TASK_STATES:
            raise AgentBackendDispatchError(f"task is not dispatchable in state {task.status.value}")
        if request.sensitivity != task.sensitivity:
            raise AgentBackendDispatchError("backend request sensitivity does not match task classification")
        self._validate_scope(request)
        try:
            authorized = self._authorize(task, request)
        except BaseException as exc:
            raise AgentBackendDispatchError("backend dispatch authority rejected request") from exc
        # Authority is fail-closed: only the literal boolean ``True`` is an
        # approval.  ``None`` and truthy objects must not silently bypass the
        # caller-owned admission boundary.
        if authorized is not True:
            raise AgentBackendDispatchError("backend dispatch authority rejected request")
        backend_id = backend.identity.backend_id
        fingerprint = self._fingerprint(request)
        identity = AgentBackendDispatchIdentity(
            task_id=task.task_id,
            backend_id=backend_id,
            dispatch_id=dispatch_id.strip() if isinstance(dispatch_id, str) else dispatch_id,
            attempt=attempt,
            workspace_id=request.scope.workspace_id,
            allowed_paths=request.scope.allowed_paths,
            request_fingerprint=fingerprint,
        )
        try:
            admission = self._admission(task, request, identity)
        except BaseException as exc:
            raise AgentBackendDispatchError("backend dispatch admission rejected request") from exc
        if not isinstance(admission, BackendAdmission):
            raise AgentBackendDispatchError("backend dispatch requires typed admission evidence")
        self._validate_admission(admission, task, request, identity)
        return task, identity, fingerprint, self.effect_key(dispatch_id), admission

    @staticmethod
    def _validate_admission(
        admission: BackendAdmission,
        task: Task,
        request: AgentBackendRequest,
        identity: AgentBackendDispatchIdentity,
    ) -> None:
        expected = {
            "task_id": identity.task_id,
            "dispatch_id": identity.dispatch_id,
            "workspace_id": identity.workspace_id,
            "allowed_paths": identity.allowed_paths,
            "sensitivity": request.sensitivity,
        }
        for name, value in expected.items():
            if getattr(admission, name) != value:
                raise AgentBackendDispatchError(f"backend admission does not match {name}")
        if task.sensitivity != admission.sensitivity:
            raise AgentBackendDispatchError("backend admission privacy classification does not match task")

    def _validate_scope(self, request: AgentBackendRequest) -> None:
        for value in request.scope.allowed_paths:
            normalized = value.replace("\\", "/")
            parsed = PurePosixPath(normalized)
            if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
                raise AgentBackendDispatchError("backend scope contains an unsafe path")
            if is_protected_path(normalized):
                raise AgentBackendDispatchError("backend scope contains a protected path")

    @staticmethod
    def _fingerprint(request: AgentBackendRequest) -> str:
        payload = {
            "task_id": request.task_id,
            "objective": request.objective,
            "workspace_id": request.scope.workspace_id,
            "allowed_paths": list(request.scope.allowed_paths),
            "input_artifacts": list(request.input_artifacts),
            "session_id": request.session_id,
            "sensitivity": request.sensitivity,
            "metadata": dict(request.metadata),
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AgentBackendDispatchError("backend request metadata must be JSON serializable") from exc
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _identity_arguments(identity: AgentBackendDispatchIdentity) -> dict[str, Any]:
        return {
            "task_id": identity.task_id,
            "backend_id": identity.backend_id,
            "dispatch_id": identity.dispatch_id,
            "attempt": identity.attempt,
            "workspace_id": identity.workspace_id,
            "allowed_paths": list(identity.allowed_paths),
            "request_fingerprint": identity.request_fingerprint,
        }

    @staticmethod
    def _verify_existing(intent: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
        current = intent.get("arguments") or {}
        for key, value in expected.items():
            if current.get(key) != value:
                raise AgentBackendDispatchError(f"backend dispatch identity mismatch: {key}")

    def _load_intent(self, key: str) -> dict[str, Any]:
        intent = self._store.get_effect_intent(key)
        if intent is None:
            raise AgentBackendDispatchError(f"backend dispatch not found: {key}")
        return intent

    @staticmethod
    def _session_from_intent(intent: Mapping[str, Any]) -> AgentBackendSession | None:
        result = intent.get("result") or {}
        value = result.get("session") if isinstance(result, Mapping) else None
        if not isinstance(value, Mapping):
            return None
        try:
            return AgentBackendSession(**dict(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _result_from_intent(intent: Mapping[str, Any]) -> AgentBackendResult | None:
        result = intent.get("result") or {}
        value = result.get("backend_result") if isinstance(result, Mapping) else None
        if not isinstance(value, Mapping) and isinstance(result, Mapping):
            evidence = result.get("evidence")
            value = evidence.get("backend_result") if isinstance(evidence, Mapping) else None
        if not isinstance(value, Mapping):
            return None
        try:
            return AgentBackendResult(**dict(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _validate_session(session: AgentBackendSession, request: AgentBackendRequest, backend: AgentBackend) -> None:
        if not isinstance(session, AgentBackendSession) or session.task_id != request.task_id or session.backend_id != backend.identity.backend_id:
            raise AgentBackendDispatchError("backend returned an invalid session identity")

    @staticmethod
    def _validate_result(result: AgentBackendResult, session: AgentBackendSession) -> None:
        if not isinstance(result, AgentBackendResult) or result.session_id != session.session_id:
            raise AgentBackendDispatchError("backend returned an invalid result identity")

    def _task_for_intent(self, intent: Mapping[str, Any]) -> Task:
        task_id = intent.get("task_id")
        task = self._store.load_task(task_id) if isinstance(task_id, str) else None
        if task is None:
            raise AgentBackendDispatchError("backend dispatch task is no longer available")
        return task

    def _append_event(self, task: Task, event_type: str, payload: Mapping[str, Any]) -> None:
        self._store.commit_transition(event=Event(task_id=task.task_id, event_type=event_type, payload=dict(payload)))

    def _mark_unknown(self, key: str, task: Task, event_type: str, payload: Mapping[str, Any]) -> None:
        self._store.transition_effect_intent(key, to_status="unknown", result=dict(payload))
        self._append_event(task, event_type, payload)

    def _persisted_event_sequences(self, task_id: str, dispatch_id: str) -> dict[int, str]:
        sequences: dict[int, str] = {}
        for event in self._store.snapshot().get("events", []):
            if event.get("task_id") != task_id or event.get("event_type") != "agent_backend.event":
                continue
            payload = event.get("payload") or {}
            if payload.get("dispatch_id") != dispatch_id:
                continue
            sequence = payload.get("sequence")
            if isinstance(sequence, int):
                sequences[sequence] = json.dumps(
                    {
                        "event_type": payload.get("event_type"),
                        "status": payload.get("status"),
                        "payload": payload.get("payload") or {},
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
        return sequences

    @staticmethod
    def _event_fingerprint(event: AgentBackendEvent) -> str:
        return json.dumps(
            {"event_type": event.event_type, "status": event.status.value if event.status else None, "payload": dict(event.payload)},
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = [
    "AgentBackendDispatchError",
    "AgentBackendDispatchIdentity",
    "AgentBackendDispatcher",
    "BackendAdmission",
    "BackendDispatchUncertain",
]
