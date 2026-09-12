"""First concrete AgentBackend adapter: CodexExecBackend.

Covers the adapter in isolation (subprocess lifecycle, exit-code-only
normalization, cancellation, unknown-session/workspace errors) and through
the real AgentBackendDispatcher authority boundary end-to-end, using an
injected command_builder so no real `codex` binary is required.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.dev_agent.backends import (
    AgentBackendDispatcher,
    AgentBackendRequest,
    AgentBackendScope,
    AgentBackendStatus,
    BackendAdmission,
)
from src.dev_agent.backends.codex_exec import CodexExecBackend, CodexExecBackendError, _CodexExecBackendImpl
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


def _process_group_kwargs() -> dict:
    """Match production's process-group creation flags (see codex_exec.py's
    start()) for a real subprocess.Popen constructed directly by a test
    fixture -- so that a fixture-side real_process is killable through the
    exact same terminate_process_tree() code path (os.killpg on POSIX,
    taskkill /T /F on Windows) production sessions use, not merely killable
    via a bare process.kill() fallback."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


# ---------------------------------------------------------------------------
# Adapter unit tests
# ---------------------------------------------------------------------------

def test_start_returns_running_session_immediately(tmp_path):
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(1.0))
    session = backend.start(_request(tmp_path))
    assert session.status == AgentBackendStatus.RUNNING
    assert session.backend_id == "codex-exec"
    backend.result(session.session_id, wait_seconds=3.0)  # drain thread before teardown


def test_start_returns_promptly_even_when_child_never_reads_stdin(tmp_path):
    """start() must not block on writing the objective to the child's
    stdin. Uses a child that never reads stdin at all and a large objective
    (bigger than a typical OS pipe buffer, ~64KB) that would block a
    synchronous write() into a full, undrained pipe -- start() must still
    return in well under the child's own runtime."""
    huge_objective = "X" * (2 * 1024 * 1024)  # 2MB, far past any OS pipe buffer
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(5.0))
    request = AgentBackendRequest(
        task_id="00000000-0000-0000-0000-000000000020",
        objective=huge_objective,
        scope=AgentBackendScope(workspace_id=str(tmp_path)),
    )
    started_at = time.monotonic()
    session = backend.start(request)
    elapsed = time.monotonic() - started_at
    assert session.status == AgentBackendStatus.RUNNING
    assert elapsed < 1.0, f"start() took {elapsed:.2f}s -- it must not block on stdin"
    backend.cancel(session.session_id)


def test_successful_exit_normalizes_to_completed(tmp_path):
    backend = _CodexExecBackendImpl(command_builder=_echo_builder("ok"))
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.COMPLETED
    assert result.reconciliation_metadata["returncode"] == 0


def test_nonzero_exit_normalizes_to_failed_not_trusting_self_report(tmp_path):
    """The subprocess's own stdout could claim success; only exit code counts."""
    def builder(request):
        return (sys.executable, "-c", "print('status: success'); import sys; sys.exit(1)")

    backend = _CodexExecBackendImpl(command_builder=builder)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.FAILED
    assert result.reconciliation_metadata["returncode"] == 1


def test_incomplete_session_returns_running_not_unknown(tmp_path):
    """A still-active process is confirmed evidence of RUNNING, not an
    unknown outcome -- see the module docstring for why conflating the two
    used to force a 300s blocking workaround."""
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(3.0))
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=0.1)
    assert result.status == AgentBackendStatus.RUNNING
    assert result.reconciliation_metadata["reason"] == "still_running"
    backend.cancel(session.session_id)


def test_cancelling_session_reports_cancelling_not_unknown(tmp_path):
    """After cancel() but before the process has actually exited, the known
    state is CANCELLING (winding down), not RUNNING and not UNKNOWN."""
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(5.0))
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
        # Binary mode, matching production's Popen contract exactly (see
        # codex_exec.py's start()) -- _write_stdin() writes
        # request.objective.encode("utf-8") (bytes). A text-mode pipe here
        # would reject that write with a TypeError, exercising a mismatch
        # that can never occur against a real production-shaped process.
        **_process_group_kwargs(),
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

        def kill(self):
            # terminate_process_tree() falls through to process.kill() as
            # its last resort when the OS-level group termination
            # (taskkill /T /F on Windows, os.killpg on POSIX) does not
            # immediately reflect the process as stopped when re-checked.
            # A fake process wrapper without this method makes that
            # fallback raise AttributeError from inside an already-running
            # exception handler, which can silently crash the background
            # thread before it ever records communicate_failed/exited --
            # leaving the session stuck as RUNNING forever instead of
            # resolving to UNKNOWN as this test expects.
            return self._inner.kill()

        @property
        def returncode(self):
            return self._inner.returncode

    def fake_popen(*args, **kwargs):
        return _BrokenCommunicateProcess(real_process)

    backend = _CodexExecBackendImpl(command_builder=_echo_builder("unused"), popen=fake_popen)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=2.0)
    assert result.status == AgentBackendStatus.UNKNOWN
    assert result.reconciliation_metadata["reason"] == "communicate_failed"
    assert result.reconciliation_metadata["process_confirmed_stopped"] is True
    real_process.wait(timeout=5.0)


def test_io_failure_terminates_a_long_running_process_no_orphan_survives(tmp_path):
    """A long-running process must not be left as an orphan after an I/O
    drain failure -- terminate_process_tree() is called immediately and its
    effect is confirmed via a real, still-running subprocess (not one that
    would have exited on its own regardless)."""
    real_process = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        # Binary mode -- see the matching note in
        # test_communicate_failure_is_reported_as_unknown above.
        **_process_group_kwargs(),
    )
    assert real_process.poll() is None  # confirm it is genuinely running

    class _BrokenReadPipe:
        def read(self, *_args, **_kwargs):
            raise OSError("simulated pipe failure")

    class _BrokenLongRunningProcess:
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

        def kill(self):
            # See the matching note in
            # test_communicate_failure_is_reported_as_unknown above --
            # terminate_process_tree()'s fallback call requires this.
            return self._inner.kill()

        @property
        def returncode(self):
            return self._inner.returncode

    def fake_popen(*args, **kwargs):
        return _BrokenLongRunningProcess(real_process)

    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(30.0), popen=fake_popen)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=3.0)
    assert result.status == AgentBackendStatus.UNKNOWN
    assert result.reconciliation_metadata["process_confirmed_stopped"] is True
    # The real underlying process must actually be dead -- not just
    # reported as such.
    assert real_process.poll() is not None
    real_process.wait(timeout=5.0)


def test_cancel_terminates_process_and_result_reports_cancelled(tmp_path):
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(30.0))
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
    backend = _CodexExecBackendImpl(command_builder=_echo_builder("should not run"))
    bad_request = AgentBackendRequest(
        task_id="00000000-0000-0000-0000-000000000002",
        objective="x",
        scope=AgentBackendScope(workspace_id=str(tmp_path / "does-not-exist")),
    )
    with pytest.raises(CodexExecBackendError, match="not an existing directory"):
        backend.start(bad_request)


def test_events_emit_started_then_exited_exactly_once(tmp_path):
    backend = _CodexExecBackendImpl(command_builder=_echo_builder("ok"))
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
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(0.3))
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
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(30.0))
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

    backend = _CodexExecBackendImpl(command_builder=builder)
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

    for sandbox_mode in ("read-only", "workspace-write"):
        command = _build_default_command(sandbox_mode=sandbox_mode, ignore_user_config=True, ignore_rules=True)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert "--danger-full-access" not in command


def test_danger_full_access_sandbox_mode_cannot_produce_a_command():
    """"danger-full-access" is a real codex sandbox value but must be
    unreachable from this adapter's production construction path --
    fail-closed rejection at the point a command would be built, not a
    downstream filter on the resulting argv."""
    from src.dev_agent.backends.codex_exec import _build_default_command

    with pytest.raises(ValueError, match="sandbox_mode"):
        _build_default_command(sandbox_mode="danger-full-access", ignore_user_config=True, ignore_rules=True)


@pytest.mark.parametrize("bad_sandbox_mode", ["danger-full-access", "unknown-mode", "", "READ-ONLY"])
def test_constructor_rejects_non_allowlisted_sandbox_mode(bad_sandbox_mode):
    with pytest.raises(ValueError, match="sandbox_mode"):
        CodexExecBackend(sandbox_mode=bad_sandbox_mode)


def test_default_command_ignores_user_config_and_rules_and_pins_sandbox():
    from src.dev_agent.backends.codex_exec import _build_default_command

    command = _build_default_command(sandbox_mode="read-only", ignore_user_config=True, ignore_rules=True)
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"


def test_sandbox_mode_is_configurable_at_construction():
    backend = CodexExecBackend(sandbox_mode="workspace-write", ignore_user_config=False, ignore_rules=False)
    command = backend._impl._command_builder(None)
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command


def test_production_backend_has_no_command_builder_parameter():
    """Structural guarantee for item 6: production composition code cannot
    pass an arbitrary command_builder to CodexExecBackend at all -- not
    merely "the default is safe if you don't override it"."""
    import inspect

    signature = inspect.signature(CodexExecBackend.__init__)
    assert "command_builder" not in signature.parameters


def test_production_backend_still_runs_the_canonical_command_end_to_end(tmp_path, monkeypatch):
    """CodexExecBackend (the production class) still exercises the real
    subprocess/threading pipeline -- only the command source differs from
    _CodexExecBackendImpl, not the behavior. Verified by monkeypatching the
    canonical command builder itself (module-level, not a constructor
    override) to a fast no-op command, since the real `codex` binary is not
    installed in this environment."""
    import src.dev_agent.backends.codex_exec as codex_exec_module

    fast_command = (sys.executable, "-c", "print('ok')")
    monkeypatch.setattr(codex_exec_module, "_build_default_command", lambda **_kwargs: fast_command)

    backend = CodexExecBackend()
    session = backend.start(_request(tmp_path))
    assert session.status == AgentBackendStatus.RUNNING
    result = backend.result(session.session_id, wait_seconds=5.0)
    assert result.status == AgentBackendStatus.COMPLETED


def test_max_runtime_seconds_must_be_positive():
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        CodexExecBackend(max_runtime_seconds=0)
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        CodexExecBackend(max_runtime_seconds=-1.0)


def test_process_exceeding_wall_clock_deadline_is_killed(tmp_path):
    """The subprocess itself is bounded -- not just how long result() is
    willing to wait for it. A background watchdog terminates the process
    once max_runtime_seconds elapses, independent of any poll."""
    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(30.0), max_runtime_seconds=0.3)
    session = backend.start(_request(tmp_path))

    deadline = time.monotonic() + 5.0
    result = backend.result(session.session_id)
    while result.status in {AgentBackendStatus.RUNNING, AgentBackendStatus.CANCELLING} and time.monotonic() < deadline:
        time.sleep(0.05)
        result = backend.result(session.session_id)
    assert result.status == AgentBackendStatus.CANCELLED
    assert result.reconciliation_metadata["reason"] == "deadline_exceeded"


def test_watchdog_does_not_flag_deadline_exceeded_for_an_already_exited_process(tmp_path):
    """Race regression: if the watchdog's timeout elapses at nearly the
    same instant the process actually exits, it must defer to
    process.poll() (the authoritative, OS-level signal) rather than to
    _wait()'s own bookkeeping, which may not have caught up yet. Modeled
    here with a process wrapper whose poll() unconditionally reports
    "already exited" regardless of the real process's state -- exactly the
    condition the watchdog must trust over its own timeout firing."""
    real_process = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(5)"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        # Binary mode -- matches production's Popen contract.
        **_process_group_kwargs(),
    )

    class _ClaimsAlreadyExitedProcess:
        def __init__(self, inner):
            self._inner = inner
            self.stdin = inner.stdin
            self.stdout = inner.stdout
            self.stderr = inner.stderr
            self.pid = inner.pid

        def poll(self):
            return 0  # always reports "already exited", win the race

        def wait(self, *args, **kwargs):
            return self._inner.wait(*args, **kwargs)

        def kill(self):
            # terminate_process_tree() never reaches this fallback for this
            # particular wrapper (poll() always reports "exited" so it
            # returns immediately) -- present anyway for consistency with
            # the real Popen interface and in case that short-circuit
            # behavior ever changes.
            return self._inner.kill()

        @property
        def returncode(self):
            return self._inner.returncode

    def fake_popen(*args, **kwargs):
        return _ClaimsAlreadyExitedProcess(real_process)

    backend = _CodexExecBackendImpl(command_builder=_sleep_builder(5.0), popen=fake_popen, max_runtime_seconds=0.2)
    session = backend.start(_request(tmp_path))

    # Give the watchdog's short deadline time to fire and make its decision.
    time.sleep(0.6)
    result = backend.result(session.session_id, wait_seconds=0.0)

    # The watchdog must have deferred to poll() and NOT flagged the
    # deadline as exceeded, and must not have terminated the (per poll(),
    # already-exited) process.
    assert result.reconciliation_metadata.get("reason") != "deadline_exceeded"
    assert real_process.poll() is None  # the real process was never killed

    real_process.terminate()
    real_process.wait(timeout=5.0)


def test_output_exceeding_max_bytes_is_bounded_not_buffered_in_full(tmp_path):
    """Peak memory for one session's captured output is bounded by
    max_output_bytes even when the process writes far more than that --
    the drain keeps reading past the cap (so the child is never blocked on
    a full pipe) but discards everything beyond the cap rather than
    buffering it all before truncating after the fact."""
    def builder(request):
        return (sys.executable, "-c", "import sys; sys.stdout.write('A' * 5_000_000)")

    backend = _CodexExecBackendImpl(command_builder=builder, max_output_bytes=1000)
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

    backend = _CodexExecBackendImpl(command_builder=builder, max_output_bytes=1_000_000)
    session = backend.start(_request(tmp_path))
    result = backend.result(session.session_id, wait_seconds=10.0)
    assert result.status == AgentBackendStatus.COMPLETED


# ---------------------------------------------------------------------------
# Item 7: subprocess environment allowlist -- no secret inheritance
# ---------------------------------------------------------------------------

def test_subprocess_does_not_inherit_arbitrary_parent_env_vars(tmp_path, monkeypatch):
    """The codex subprocess must not see Provider API keys or other secrets
    that happen to be set in this process's own environment -- only an
    allowlisted base plus explicitly opted-in extras."""
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", "super-secret-value")
    monkeypatch.setenv("ANOTHER_SECRET_TOKEN", "also-secret")

    def builder(request):
        return (
            sys.executable,
            "-c",
            "import os; print('HAS_KEY=' + str('SOME_PROVIDER_API_KEY' in os.environ)); "
            "print('HAS_TOKEN=' + str('ANOTHER_SECRET_TOKEN' in os.environ))",
        )

    backend = _CodexExecBackendImpl(command_builder=builder)
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    stdout = backend._sessions[session.session_id].stdout or ""
    assert "HAS_KEY=False" in stdout
    assert "HAS_TOKEN=False" in stdout


def test_subprocess_gets_a_fresh_temporary_home_and_codex_home(tmp_path):
    real_home = str(Path.home())

    def builder(request):
        return (
            sys.executable,
            "-c",
            "import os; print('HOME=' + os.environ.get('HOME', '')); print('CODEX_HOME=' + os.environ.get('CODEX_HOME', ''))",
        )

    backend = _CodexExecBackendImpl(command_builder=builder)
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    stdout = backend._sessions[session.session_id].stdout or ""
    assert "codex-exec-home-" in stdout
    # HOME and CODEX_HOME must point at the SAME fresh directory, and it
    # must not literally be the real operator's home directory (a temp
    # directory living under the same user profile on Windows is fine and
    # expected -- it must not be equal to the real home path itself).
    home_line = next(line for line in stdout.splitlines() if line.startswith("HOME="))
    codex_home_line = next(line for line in stdout.splitlines() if line.startswith("CODEX_HOME="))
    home_value = home_line.split("=", 1)[1]
    assert home_value == codex_home_line.split("=", 1)[1]
    assert home_value != real_home


def test_temporary_home_directory_is_removed_after_session_completes(tmp_path):
    backend = _CodexExecBackendImpl(command_builder=_echo_builder("ok"))
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    home_dir = backend._sessions[session.session_id].home_dir
    assert home_dir is not None
    assert not Path(home_dir).exists()


def test_extra_env_passthrough_allows_only_explicitly_named_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_TOKEN", "the-real-codex-token")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    def builder(request):
        return (
            sys.executable,
            "-c",
            "import os; print('AUTH=' + os.environ.get('CODEX_AUTH_TOKEN', 'MISSING')); "
            "print('HAS_UNRELATED=' + str('UNRELATED_SECRET' in os.environ))",
        )

    backend = _CodexExecBackendImpl(command_builder=builder, extra_env_passthrough=frozenset({"CODEX_AUTH_TOKEN"}))
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    stdout = backend._sessions[session.session_id].stdout or ""
    assert "AUTH=the-real-codex-token" in stdout
    assert "HAS_UNRELATED=False" in stdout


def test_production_backend_exposes_extra_env_passthrough_parameter():
    import inspect

    signature = inspect.signature(CodexExecBackend.__init__)
    assert "extra_env_passthrough" in signature.parameters


# ---------------------------------------------------------------------------
# Item 8: max_output_bytes is a real byte limit, not a character count
# ---------------------------------------------------------------------------

def test_max_output_bytes_is_enforced_in_actual_bytes_for_multibyte_text(tmp_path):
    """Each of these Japanese characters is 3 bytes in UTF-8. A char-count
    based limit would let 3x more actual bytes through than a byte-based
    one for the same nominal cap -- this proves the cap is bytes, not
    decoded characters."""
    multibyte_char = "あ"  # U+3042 HIRAGANA LETTER A, 3 bytes in UTF-8
    char_count = 10_000
    total_bytes = char_count * len(multibyte_char.encode("utf-8"))
    assert total_bytes == 30_000

    def builder(request):
        return (
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write(('\\u3042' * {char_count}).encode('utf-8'))",
        )

    max_bytes = 1000  # far below the 30,000 actual bytes the process writes
    backend = _CodexExecBackendImpl(command_builder=builder, max_output_bytes=max_bytes)
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    state = backend._sessions[session.session_id]
    assert state.stdout_truncated is True
    # The captured text, re-encoded, must stay close to the byte cap --
    # proving the limit was enforced against actual bytes, not character
    # count. A char-count-based cap would have let through max_bytes
    # *characters* of 3-byte content, i.e. ~30,000 actual bytes for this
    # payload -- 30x over budget. A small tolerance (4 bytes) accounts for
    # the U+FFFD replacement character produced by errors="replace" when
    # the raw-byte cut lands mid-character, which can itself re-encode to
    # a few bytes more than the exact raw-byte count kept internally.
    recoded_length = len(state.stdout.encode("utf-8", errors="ignore"))
    assert recoded_length <= max_bytes + 4
    assert recoded_length < total_bytes // 2  # nowhere close to the full 30,000 bytes


def test_multibyte_output_under_the_cap_is_captured_intact(tmp_path):
    """A small multi-byte payload well under the cap must decode cleanly,
    with no corruption from the byte-oriented drain."""
    message = "こんにちは"  # well under any reasonable byte cap

    def builder(request):
        return (
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({message!r}.encode('utf-8'))",
        )

    backend = _CodexExecBackendImpl(command_builder=builder, max_output_bytes=10_000)
    session = backend.start(_request(tmp_path))
    backend.result(session.session_id, wait_seconds=5.0)
    state = backend._sessions[session.session_id]
    assert state.stdout_truncated is False
    assert state.stdout == message
