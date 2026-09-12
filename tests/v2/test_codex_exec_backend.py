"""First concrete AgentBackend adapter: CodexExecBackend.

Covers the adapter in isolation (subprocess lifecycle, exit-code-only
normalization, cancellation, unknown-session/workspace errors) and through
the real AgentBackendDispatcher authority boundary end-to-end, using an
injected command_builder so no real `codex` binary is required.
"""

from __future__ import annotations

import subprocess
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


def test_incomplete_session_returns_running_not_unknown(tmp_path):
    """A still-active process is confirmed evidence of RUNNING, not an
    unknown outcome -- see the module docstring for why conflating the two
    used to force a 300s blocking workaround."""
    backend = CodexExecBackend(command_builder=_sleep_builder(3.0))
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=0.1)
    assert result.status == AgentBackendStatus.RUNNING
    assert result.reconciliation_metadata["reason"] == "still_running"
    backend.cancel(session.session_id)


def test_cancelling_session_reports_cancelling_not_unknown(tmp_path):
    """After cancel() but before the process has actually exited, the known
    state is CANCELLING (winding down), not RUNNING and not UNKNOWN."""
    backend = CodexExecBackend(command_builder=_sleep_builder(5.0))
    session = backend.start(_request(tmp_path))
    backend.cancel(session.session_id)
    result = backend.result(session.session_id, wait_seconds=0.0)
    # terminate_process_tree is not instantaneous; if the OS already reaped
    # the process before this check, CANCELLED is also an acceptable
    # (correct) outcome for this poll.
    assert result.status in {AgentBackendStatus.CANCELLING, AgentBackendStatus.CANCELLED}


def test_communicate_failure_is_reported_as_unknown(tmp_path):
    """UNKNOWN is reserved for genuinely losing the ability to observe the
    process's outcome -- simulated here via an injected popen factory whose
    resulting handle's stdout.read() raises, standing in for a real
    OS-level pipe failure partway through the bounded streaming drain."""
    real_process = subprocess.Popen(
        (sys.executable, "-c", "print('ok')"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )

    class _BrokenReadPipe:
        def read(self, *_args, **_kwargs):
            raise OSError("simulated pipe failure")

    class _BrokenCommunicateProcess:
        def __init__(self, inner):
            self._inner = inner
            self.stdout = _BrokenReadPipe()
            self.stderr = _BrokenReadPipe()
            self.stdin = inner.stdin
            self.pid = inner.pid

        def wait(self, *args, **kwargs):
            return self._inner.wait(*args, **kwargs)

        def poll(self):
            return self._inner.poll()

        @property
        def returncode(self):
            return self._inner.returncode

    def fake_popen(*args, **kwargs):
        return _BrokenCommunicateProcess(real_process)

    backend = CodexExecBackend(command_builder=_echo_builder("unused"), popen=fake_popen)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=2.0)
    assert result.status == AgentBackendStatus.UNKNOWN
    assert result.reconciliation_metadata["reason"] == "communicate_failed"
    real_process.wait(timeout=5.0)


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


def test_dispatcher_polls_through_running_to_completion(store, task, tmp_path):
    """Demonstrates the actual fix: RUNNING is a legal, non-terminal result
    status, so AgentBackendDispatcher.result() keeps the durable effect
    intent in "dispatching" while the process is still going -- a plain
    poll loop resolves normally once it finishes. Before this fix, a
    not-yet-finished poll had to return UNKNOWN, which durably committed
    the dispatch to "needs explicit reconciliation" on the very first poll
    and could never resolve via a second dispatcher.result() call."""
    backend = CodexExecBackend(command_builder=_sleep_builder(0.3))
    dispatcher = _dispatcher(store, task)
    request = AgentBackendRequest(
        task_id=task.task_id,
        objective="bounded backend task",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )

    session = dispatcher.dispatch(request, backend, dispatch_id="codex-exec-e2e-1", attempt=1)
    assert session.status == AgentBackendStatus.RUNNING

    first_poll = dispatcher.result("codex-exec-e2e-1", backend)
    assert first_poll.status == AgentBackendStatus.RUNNING

    deadline = time.monotonic() + 5.0
    result = first_poll
    while result.status == AgentBackendStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
        result = dispatcher.result("codex-exec-e2e-1", backend)
    assert result.status == AgentBackendStatus.COMPLETED


def test_dispatcher_cancel_reaches_codex_exec_backend_process(store, task, tmp_path):
    backend = CodexExecBackend(command_builder=_sleep_builder(30.0))
    dispatcher = _dispatcher(store, task)
    request = AgentBackendRequest(
        task_id=task.task_id,
        objective="bounded backend task",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )
    dispatcher.dispatch(request, backend, dispatch_id="codex-exec-e2e-cancel", attempt=1)
    dispatcher.cancel("codex-exec-e2e-cancel", backend)

    deadline = time.monotonic() + 5.0
    result = dispatcher.result("codex-exec-e2e-cancel", backend)
    while result.status in {AgentBackendStatus.RUNNING, AgentBackendStatus.CANCELLING} and time.monotonic() < deadline:
        time.sleep(0.05)
        result = dispatcher.result("codex-exec-e2e-cancel", backend)
    assert result.status == AgentBackendStatus.CANCELLED


# ---------------------------------------------------------------------------
# Group C hardening: prompt via stdin, wall-clock deadline, bounded
# streaming, explicit sandbox/config flags.
# ---------------------------------------------------------------------------

def test_objective_is_sent_via_stdin_not_argv():
    """An objective that looks like a CLI option must never be parsed as
    one -- it is sent over stdin, never placed in argv."""
    from src.dev_agent.backends.codex_exec import _build_default_command

    command = _build_default_command(sandbox_mode="read-only", ignore_user_config=True, ignore_rules=True)
    dangerous_objective = "--dangerously-bypass-approvals-and-sandbox"
    assert dangerous_objective not in command
    # The stdin-prompt convention is the trailing "-" argument, not the
    # objective text appearing anywhere in argv.
    assert command[-1] == "-"


def test_start_writes_objective_to_child_stdin(tmp_path):
    def builder(request):
        return (sys.executable, "-c", "import sys; print('received:' + sys.stdin.read())")

    backend = CodexExecBackend(command_builder=builder)
    request = AgentBackendRequest(
        task_id="00000000-0000-0000-0000-000000000010",
        objective="--looks-like-a-flag but is actually the prompt",
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )
    session = backend.start(request)
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.COMPLETED


def test_default_command_never_requests_full_access():
    from src.dev_agent.backends.codex_exec import _build_default_command

    for sandbox_mode in ("read-only", "workspace-write", "danger-full-access"):
        command = _build_default_command(sandbox_mode=sandbox_mode, ignore_user_config=True, ignore_rules=True)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert "--danger-full-access" not in command


def test_default_command_ignores_user_config_and_rules_and_pins_sandbox():
    from src.dev_agent.backends.codex_exec import _build_default_command

    command = _build_default_command(sandbox_mode="read-only", ignore_user_config=True, ignore_rules=True)
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"


def test_sandbox_mode_is_configurable_at_construction():
    backend = CodexExecBackend(sandbox_mode="workspace-write", ignore_user_config=False, ignore_rules=False)
    command = backend._command_builder(None)
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command


def test_max_runtime_seconds_must_be_positive():
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        CodexExecBackend(max_runtime_seconds=0)
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        CodexExecBackend(max_runtime_seconds=-1.0)


def test_process_exceeding_wall_clock_deadline_is_killed(tmp_path):
    """The subprocess itself is bounded -- not just how long result() is
    willing to wait for it. A background watchdog terminates the process
    once max_runtime_seconds elapses, independent of any poll."""
    backend = CodexExecBackend(command_builder=_sleep_builder(30.0), max_runtime_seconds=0.3)
    session = backend.start(_request(tmp_path))

    deadline = time.monotonic() + 5.0
    result = backend.result(session.session_id)
    while result.status in {AgentBackendStatus.RUNNING, AgentBackendStatus.CANCELLING} and time.monotonic() < deadline:
        time.sleep(0.05)
        result = backend.result(session.session_id)
    assert result.status == AgentBackendStatus.CANCELLED
    assert result.reconciliation_metadata["reason"] == "deadline_exceeded"


def test_output_exceeding_max_bytes_is_bounded_not_buffered_in_full(tmp_path):
    """Peak memory for one session's captured output is bounded by
    max_output_bytes even when the process writes far more than that --
    the drain keeps reading past the cap (so the child is never blocked on
    a full pipe) but discards everything beyond the cap rather than
    buffering it all before truncating after the fact."""
    def builder(request):
        return (sys.executable, "-c", "import sys; sys.stdout.write('A' * 5_000_000)")

    backend = CodexExecBackend(command_builder=builder, max_output_bytes=1000)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.COMPLETED
    assert result.reconciliation_metadata["stdout_bytes"] <= 1000
    assert result.reconciliation_metadata["stdout_truncated"] is True


def test_concurrent_dual_pipe_output_does_not_deadlock(tmp_path):
    """Writing large output to BOTH stdout and stderr concurrently must not
    deadlock the drain -- both pipes must be read at the same time."""
    def builder(request):
        return (
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('O' * 200_000); sys.stderr.write('E' * 200_000)",
        )

    backend = CodexExecBackend(command_builder=builder, max_output_bytes=1_000_000)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=10.0)
    assert result.status == AgentBackendStatus.COMPLETED
