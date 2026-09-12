"""First concrete AgentBackend adapter: CodexExecBackend.

Covers the adapter in isolation (subprocess lifecycle, exit-code-only
normalization, cancellation, unknown-session/workspace errors) and through
the real AgentBackendDispatcher authority boundary end-to-end, using an
injected command_builder so no real `codex` binary is required.
"""

from __future__ import annotations

import sys
import time

import pytest

from src.dev_agent.backends import (
    AgentBackendDispatcher,
    AgentBackendRequest,
    AgentBackendScope,
    AgentBackendStatus,
    BackendAdmission,
)
from src.dev_agent.backends.codex_exec import CodexExecBackend, CodexExecBackendError
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _echo_builder(text: str):
    def builder(request: AgentBackendRequest):
        return (sys.executable, "-c", f"print({text!r})")

    return builder


def _exit_code_builder(code: int):
    def builder(request: AgentBackendRequest):
        return (sys.executable, "-c", f"import sys; sys.exit({code})")

    return builder


def _sleep_builder(seconds: float):
    def builder(request: AgentBackendRequest):
        return (sys.executable, "-c", f"import time; time.sleep({seconds})")

    return builder


def _request(tmp_path, task_id="00000000-0000-0000-0000-000000000001"):
    return AgentBackendRequest(
        task_id=task_id,
        objective="run a bounded codex exec turn",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )


# ---------------------------------------------------------------------------
# Adapter unit tests
# ---------------------------------------------------------------------------

def test_start_returns_running_session_immediately(tmp_path):
    backend = CodexExecBackend(command_builder=_sleep_builder(1.0))
    session = backend.start(_request(tmp_path))
    assert session.status == AgentBackendStatus.RUNNING
    assert session.backend_id == "codex-exec"
    backend.result(session.session_id, wait_seconds=3.0)  # drain thread before teardown


def test_successful_exit_normalizes_to_completed(tmp_path):
    backend = CodexExecBackend(command_builder=_echo_builder("ok"))
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.COMPLETED
    assert result.reconciliation_metadata["returncode"] == 0


def test_nonzero_exit_normalizes_to_failed_not_trusting_self_report(tmp_path):
    """The subprocess's own stdout could claim success; only exit code counts."""
    def builder(request):
        return (sys.executable, "-c", "print('status: success'); import sys; sys.exit(1)")

    backend = CodexExecBackend(command_builder=builder)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.FAILED
    assert result.reconciliation_metadata["returncode"] == 1


def test_incomplete_session_returns_unknown_not_a_guess(tmp_path):
    backend = CodexExecBackend(command_builder=_sleep_builder(3.0))
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=0.1)
    assert result.status == AgentBackendStatus.UNKNOWN
    assert result.reconciliation_metadata["reason"] == "still_running"
    backend.cancel(session.session_id)


def test_cancel_terminates_process_and_result_reports_cancelled(tmp_path):
    backend = CodexExecBackend(command_builder=_sleep_builder(30.0))
    session = backend.start(_request(tmp_path))
    backend.cancel(session.session_id)
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.CANCELLED


def test_unknown_session_id_raises():
    backend = CodexExecBackend()
    with pytest.raises(CodexExecBackendError, match="unknown codex-exec session"):
        backend.result("does-not-exist")
    with pytest.raises(CodexExecBackendError):
        backend.events("does-not-exist")
    with pytest.raises(CodexExecBackendError):
        backend.cancel("does-not-exist")


def test_nonexistent_workspace_is_rejected_before_spawning(tmp_path):
    backend = CodexExecBackend(command_builder=_echo_builder("should not run"))
    bad_request = AgentBackendRequest(
        task_id="00000000-0000-0000-0000-000000000002",
        objective="x",
        scope=AgentBackendScope(workspace_id=str(tmp_path / "does-not-exist")),
    )
    with pytest.raises(CodexExecBackendError, match="not an existing directory"):
        backend.start(bad_request)


def test_events_emit_started_then_exited_exactly_once(tmp_path):
    backend = CodexExecBackend(command_builder=_echo_builder("ok"))
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)  # ensure process has exited
    first = backend.events(session.session_id)
    assert [event.event_type for event in first] == ["process_started", "process_exited"]
    second = backend.events(session.session_id)
    assert second == ()  # already emitted, no duplicates


def test_custom_backend_version_is_reflected_in_identity():
    backend = CodexExecBackend(backend_version="0.1.0-test")
    assert backend.identity.backend_version == "0.1.0-test"
    assert backend.identity.backend_id == "codex-exec"


def test_no_discover_method_is_intentional():
    """A subprocess-memory-only adapter cannot recover sessions after a
    process restart -- it must not pretend otherwise."""
    backend = CodexExecBackend()
    assert not hasattr(backend, "discover")


# ---------------------------------------------------------------------------
# End-to-end through AgentBackendDispatcher (real authority boundary)
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        yield state


@pytest.fixture
def task(store):
    value = Task(objective="bounded backend task", status=TaskStatus.READY)
    store.save_task(value)
    return value


def _dispatcher(store, task):
    def admission(task_, request, identity):
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
            allowed_capabilities=tuple(task_.required_capabilities),
        )

    return AgentBackendDispatcher(store, authorize=lambda t, r: True, admission=admission)


def test_dispatcher_drives_codex_exec_backend_to_completion(store, task, tmp_path):
    # AgentBackendDispatcher.result() calls backend.result(session_id) with
    # no wait argument and treats a returned UNKNOWN as durably needing
    # reconciliation -- a plain retry cannot self-heal. So the adapter's
    # *default* wait must itself be enough to observe this fast echo command
    # finish; a short default here (not the 300s production default) keeps
    # the test fast while still exercising the real, unmodified code path.
    backend = CodexExecBackend(command_builder=_echo_builder("dispatcher e2e"), default_wait_seconds=5.0)
    dispatcher = _dispatcher(store, task)
    request = AgentBackendRequest(
        task_id=task.task_id,
        objective="bounded backend task",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )

    session = dispatcher.dispatch(request, backend, dispatch_id="codex-exec-e2e-1", attempt=1)
    assert session.status == AgentBackendStatus.RUNNING

    result = dispatcher.result("codex-exec-e2e-1", backend)
    assert result.status == AgentBackendStatus.COMPLETED


def test_dispatcher_cancel_reaches_codex_exec_backend_process(store, task, tmp_path):
    backend = CodexExecBackend(command_builder=_sleep_builder(30.0), default_wait_seconds=5.0)
    dispatcher = _dispatcher(store, task)
    request = AgentBackendRequest(
        task_id=task.task_id,
        objective="bounded backend task",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )
    dispatcher.dispatch(request, backend, dispatch_id="codex-exec-e2e-cancel", attempt=1)
    dispatcher.cancel("codex-exec-e2e-cancel", backend)

    result = dispatcher.result("codex-exec-e2e-cancel", backend)
    assert result.status == AgentBackendStatus.CANCELLED
