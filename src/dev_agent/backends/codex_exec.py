"""Codex exec concrete AgentBackend adapter.

This is the first concrete implementation of the AgentBackend Protocol
(protocol.py). It launches ``codex exec`` (or an injected equivalent
command) as one bounded subprocess per session. It owns nothing about task
state, budget, approval, lease, or privacy: AgentBackendDispatcher enforces
all of that before ``start()`` is ever called, and the durable identity for
one dispatch is the caller's effect intent (AgentBackendDispatcher.effect_key)
-- this module keeps no second state machine, only an in-memory map from a
locally-issued session_id to the live subprocess handle for the life of this
process.

Design notes:

- Non-blocking start(): ``codex exec`` can run for minutes. start() launches
  the subprocess and returns immediately with status RUNNING; a background
  thread drains stdout/stderr (bounded, streaming -- see below) so a slow or
  hanging invocation cannot block the caller.
- The prompt (``request.objective``) is sent over stdin, never as a CLI
  argument. codex exec has options such as ``--sandbox``, ``--cd``,
  ``--dangerously-bypass-approvals-and-sandbox``; an objective string that
  happens to start with ``--`` would otherwise be parsed as a CLI option
  rather than a prompt (argument/option injection), and a long objective
  would risk platform argv length limits. Both are avoided by never placing
  the objective in argv.
- Every session has a wall-clock deadline (``max_runtime_seconds``). A
  watchdog thread calls terminate_process_tree() the moment that deadline
  is exceeded, independent of whether any caller ever polls result() again
  -- "bounded subprocess" means the process itself is bounded, not just how
  long a caller is willing to wait for it.
- stdout/stderr are drained via streaming reads on background threads (one
  each, running concurrently -- both pipes must be drained at the same time
  to avoid a classic dual-pipe deadlock where the child blocks writing to
  one full pipe while the parent is blocked reading the other), keeping at
  most ``max_output_bytes`` of each and discarding -- but still reading past
  that point so the child is never blocked on a full pipe -- the rest. This
  bounds actual peak memory use per session; the old approach
  (``Popen.communicate()`` then truncate) would already have buffered the
  full output in memory before truncation ever ran.
- result() never trusts the subprocess's own claims of success. Only the
  process exit code (and, in the future, structured JSON output) are treated
  as ground truth. A session whose subprocess has not yet exited returns
  AgentBackendStatus.RUNNING (or CANCELLING once cancel() has been called) --
  a known, non-terminal, Protocol-legal status distinct from UNKNOWN.
  UNKNOWN means "no confirmed evidence of the outcome at all" and durably
  commits AgentBackendDispatcher's effect intent to "needs explicit
  reconciliation", a state a plain retry cannot self-heal out of even once
  the process finishes moments later; RUNNING/CANCELLING instead keep the
  intent in "dispatching" so a later poll resolves normally. UNKNOWN is
  reserved for when this adapter's own subprocess I/O pipeline itself fails
  unexpectedly, i.e. genuinely losing the ability to observe outcome.
- No discover() method is implemented, so AgentBackendDispatcher.reconcile_start
  correctly falls through to "backend discovery unavailable" and marks the
  dispatch UNKNOWN. This is honest: an in-process-memory session map cannot
  recover a session after this process restarts, since the OS process
  handle itself is gone.
- The default command includes ``--sandbox``, ``--ignore-user-config``, and
  ``--ignore-rules`` so the operator's own ambient Codex configuration,
  custom rules, and approval/sandbox policy cannot silently change what this
  adapter is authorized to do -- dev_agent's Authority boundary (already
  enforced by AgentBackendDispatcher before start() is ever called) must be
  the only thing deciding that, not whatever happens to be in the invoking
  user's home directory. The default never includes
  ``--dangerously-bypass-approvals-and-sandbox`` or an equivalent
  full-access override; test_default_command_never_requests_full_access in
  test_codex_exec_backend.py asserts this.
- The exact `codex exec` flags used here (including the stdin-prompt
  convention and the config/sandbox/rules flag names) are unverified
  against a live installed CLI in this environment -- neither `codex` nor
  `claude` is installed here. Override command_builder at construction to
  match the operator's actual installed CLI version before production use.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from ..tools.executor import terminate_process_tree
from .protocol import (
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendSession,
    AgentBackendStatus,
)


CommandBuilder = Callable[[AgentBackendRequest], Sequence[str]]
PopenFactory = Callable[..., "subprocess.Popen[str]"]

# Default wall-clock ceiling for one codex exec session. Chosen as a
# generous but finite bound for a single bounded development turn; an
# operator with genuinely longer turns should raise this explicitly at
# construction rather than leave sessions unbounded.
DEFAULT_MAX_RUNTIME_SECONDS = 600.0

# Flags that must never appear in the default command: each one disables an
# authority boundary (approval, sandbox) that dev_agent's own Control Plane
# is responsible for, not the invoked CLI.
_FORBIDDEN_DEFAULT_FLAGS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--danger-full-access",
)

# The only sandbox modes the production path may request. "danger-full-access"
# is a real codex sandbox value but must never be reachable from this
# adapter's construction path -- fail-closed: reject it (and anything else
# outside this set), rather than passing an unrecognized or dangerous value
# through to the CLI.
_ALLOWED_SANDBOX_MODES = frozenset({"read-only", "workspace-write"})

# Environment variable names inherited from this process's own environment
# into the codex subprocess, mirroring
# scripts/devfarm_worker.py's HostVerificationRunner._environment() allowlist
# approach: only what is needed to locate the interpreter/CLI and behave
# consistently with the host locale, never a blanket inheritance of the
# full parent environment (which would hand the subprocess every Provider
# API key, credential, and secret already present there).
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
    }
)


def _sanitized_environment(home: Path, *, extra_env_passthrough: frozenset[str]) -> dict[str, str]:
    """Build the environment for one codex subprocess: an allowlisted base
    plus a fresh, throwaway HOME/USERPROFILE/CODEX_HOME so any config or
    state codex itself writes never touches the real operator's home
    directory, plus only the explicitly-named extra variables an operator
    has opted into passing through (e.g. codex's own auth variable) -- never
    anything else from this process's environment, in particular no
    Provider API keys or other secrets that happen to be set here.
    """
    environment = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENVIRONMENT_KEYS}
    for name in extra_env_passthrough:
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    home_value = str(home)
    environment["HOME"] = home_value
    environment["USERPROFILE"] = home_value
    environment["CODEX_HOME"] = home_value
    return environment


def _build_default_command(
    *,
    sandbox_mode: str,
    ignore_user_config: bool,
    ignore_rules: bool,
) -> tuple[str, ...]:
    if sandbox_mode not in _ALLOWED_SANDBOX_MODES:
        raise ValueError(
            f"sandbox_mode must be one of {sorted(_ALLOWED_SANDBOX_MODES)}, got {sandbox_mode!r}. "
            "\"danger-full-access\", unknown values, and an empty string are rejected fail-closed."
        )
    command = ["codex", "exec", "--json", "--sandbox", sandbox_mode]
    if ignore_user_config:
        command.append("--ignore-user-config")
    if ignore_rules:
        command.append("--ignore-rules")
    # "-" tells codex exec to read the prompt from stdin instead of argv;
    # start() writes request.objective to the child's stdin after spawning.
    command.append("-")
    for forbidden in _FORBIDDEN_DEFAULT_FLAGS:
        if forbidden in command:  # pragma: no cover - defensive, always false
            raise AssertionError(f"default codex-exec command must never include {forbidden!r}")
    return tuple(command)


class CodexExecBackendError(RuntimeError):
    """Raised for adapter-local usage errors (unknown session, bad request)."""


def _remove_directory(path: str) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


class _SessionState:
    __slots__ = (
        "process",
        "thread",
        "stdin_thread",
        "task_id",
        "cancelled",
        "stdout",
        "stderr",
        "stdout_truncated",
        "stderr_truncated",
        "returncode",
        "communicate_failed",
        "io_failure_confirmed_stopped",
        "deadline_exceeded",
        "started_at",
        "started_event_emitted",
        "completion_event_emitted",
        "home_dir",
        "home_dir_cleaned_up",
    )

    def __init__(self, *, process: "subprocess.Popen[str]", task_id: str, home_dir: str | None = None) -> None:
        self.process = process
        self.thread: Thread | None = None
        self.stdin_thread: Thread | None = None
        self.task_id = task_id
        self.home_dir = home_dir
        self.home_dir_cleaned_up = False
        self.cancelled = False
        self.stdout: str | None = None
        self.stderr: str | None = None
        self.stdout_truncated = False
        self.stderr_truncated = False
        self.returncode: int | None = None
        # True only if this adapter's own I/O drain pipeline raised
        # unexpectedly -- a distinct, genuinely ambiguous condition from
        # "still running": we can no longer observe this process's true
        # state at all, which is exactly what UNKNOWN means.
        self.communicate_failed = False
        self.io_failure_confirmed_stopped: bool | None = None
        self.deadline_exceeded = False
        self.started_at = time.monotonic()
        self.started_event_emitted = False
        self.completion_event_emitted = False


_STREAM_CHUNK_BYTES = 65536


def _drain_bounded(pipe: Any, max_bytes: int, out: dict[str, Any]) -> None:
    """Read ``pipe`` (binary mode) to EOF, keeping at most ``max_bytes`` raw
    bytes and discarding the rest -- but continuing to read past the cap so
    the writing process is never blocked on a full pipe buffer. The kept
    bytes are decoded as UTF-8 (replacing invalid sequences) into "text";
    "truncated" records whether the cap was hit. Runs on its own thread; a
    caller must run one of these per pipe (stdout and stderr) concurrently
    to avoid the classic dual-pipe deadlock.

    The limit is enforced in actual bytes, not decoded characters: reading
    the pipe in text mode would count a multi-byte UTF-8 character (e.g.
    most non-ASCII text) as one unit against the cap while it actually
    consumes several bytes of process memory and matches the documented
    max_output_bytes contract only for ASCII-only output.
    """
    collected: list[bytes] = []
    total = 0
    truncated = False
    try:
        while True:
            chunk = pipe.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            if not truncated:
                remaining = max_bytes - total
                if remaining > 0:
                    collected.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    truncated = True
    except BaseException as exc:  # noqa: BLE001 - reported via out, not raised
        out["error"] = exc
        return
    # The `truncated` flag is the authoritative signal -- no in-band marker
    # is appended to `text`, since doing so would itself push the captured
    # bytes past max_bytes. errors="replace" because a truncation cut can
    # land in the middle of a multi-byte UTF-8 sequence.
    out["text"] = b"".join(collected).decode("utf-8", errors="replace")
    out["truncated"] = truncated


class _CodexExecBackendImpl:
    """Full implementation, parameterized by an arbitrary ``command_builder``.

    Not part of the public production surface -- see ``CodexExecBackend``
    below, which is what production composition code (Operation, DevFarm,
    etc.) must use, and whose public constructor does not accept a
    ``command_builder`` at all. An arbitrary command_builder can bypass
    every safety default this module establishes (sandbox mode,
    --ignore-user-config, --ignore-rules, the stdin-prompt convention that
    prevents argument/option injection) -- production code must never be
    able to construct an instance with one. This class exists so that this
    module's own test suite can exercise the real subprocess/threading
    machinery with an injected fake command (no real ``codex`` binary is
    installed in most environments) without that same door being open to
    Operation or any other production caller. Tests import this class
    directly; production code only ever sees ``CodexExecBackend``.
    """

    identity = AgentBackendIdentity(
        backend_id="codex-exec",
        backend_version="unversioned",
        capabilities=("text",),
    )

    def __init__(
        self,
        *,
        command_builder: CommandBuilder,
        popen: PopenFactory | None = None,
        backend_version: str | None = None,
        max_output_bytes: int = 2 * 1024 * 1024,
        default_wait_seconds: float = 0.0,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
        extra_env_passthrough: frozenset[str] = frozenset(),
    ) -> None:
        if backend_version is not None:
            self.identity = AgentBackendIdentity(
                backend_id="codex-exec",
                backend_version=backend_version,
                capabilities=("text",),
            )
        if isinstance(max_runtime_seconds, bool) or not isinstance(max_runtime_seconds, (int, float)) or max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be a positive number")
        if not callable(command_builder):
            raise TypeError("command_builder must be callable")
        self._command_builder: CommandBuilder = command_builder
        self._popen: PopenFactory = popen or subprocess.Popen
        self._extra_env_passthrough = frozenset(extra_env_passthrough)
        self._max_output_bytes = max_output_bytes
        self._max_runtime_seconds = float(max_runtime_seconds)
        # result() returns AgentBackendStatus.RUNNING (a known, non-terminal,
        # Protocol-legal status) rather than UNKNOWN while the process is
        # still active, so AgentBackendDispatcher.result() keeps the durable
        # effect intent in "dispatching" and a later poll can resolve
        # normally -- see result()'s docstring for why an earlier revision
        # of this adapter needed a long blocking default instead, and why
        # that is no longer necessary. default_wait_seconds is now purely a
        # convenience for a caller that wants one call to block briefly
        # rather than poll in a tight loop; 0.0 (non-blocking) is the
        # correct default now that RUNNING is a legal result status.
        self._default_wait_seconds = default_wait_seconds
        self._lock = Lock()
        self._sessions: dict[str, _SessionState] = {}

    def _require_session(self, session_id: str) -> _SessionState:
        with self._lock:
            state = self._sessions.get(session_id)
        if state is None:
            raise CodexExecBackendError(f"unknown codex-exec session: {session_id}")
        return state

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        if not isinstance(request, AgentBackendRequest):
            raise TypeError("request must be an AgentBackendRequest")
        workspace = Path(request.scope.workspace_id)
        if not workspace.is_dir():
            raise CodexExecBackendError(
                f"codex-exec workspace_id is not an existing directory: {request.scope.workspace_id}"
            )
        command = tuple(self._command_builder(request))
        if not command:
            raise CodexExecBackendError("command_builder produced an empty command")

        # A fresh, throwaway HOME/CODEX_HOME per session -- codex's own
        # config/state writes never touch the real operator's home
        # directory, and this directory (along with everything codex wrote
        # into it) is removed once the session's I/O drain completes.
        home_dir = tempfile.mkdtemp(prefix="codex-exec-home-")
        environment = _sanitized_environment(Path(home_dir), extra_env_passthrough=self._extra_env_passthrough)

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        try:
            process = self._popen(
                list(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                # Binary mode (no text=True): _drain_bounded enforces
                # max_output_bytes in actual bytes, not decoded characters
                # -- a text-mode pipe would undercount multi-byte UTF-8
                # output against the same nominal limit.
                cwd=str(workspace),
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
        except BaseException:
            _remove_directory(home_dir)
            raise

        state = _SessionState(process=process, task_id=request.task_id, home_dir=home_dir)
        session_id = str(uuid4())
        exited = Event()

        def _write_stdin() -> None:
            # Runs on its own thread, started only after the drain and
            # watchdog threads are already running: if the child process
            # never reads stdin (e.g. it is waiting on something else, or
            # never starts reading until some other condition), this
            # write() call can block indefinitely on a full pipe buffer.
            # Blocking start() itself on that write was the original bug --
            # a caller expects start() to return promptly regardless of
            # whether the child ever consumes its prompt.
            try:
                process.stdin.write(request.objective.encode("utf-8"))
            except BaseException:
                if process.poll() is None:
                    # The child is still running and something unexpected
                    # went wrong writing its prompt -- treat this the same
                    # as an I/O drain failure: terminate and confirm,
                    # rather than leaving a process that never received
                    # its prompt running unattended.
                    terminate_process_tree(process)
                    with self._lock:
                        state.communicate_failed = True
                    exited.set()
                # else: the child already exited (e.g. it never reads
                # stdin at all) -- a closed-pipe/broken-pipe write failure
                # here is benign, not a genuinely ambiguous outcome. The
                # real result still comes from _wait()'s own observation
                # of the exit code; do not override it.
            finally:
                # stdin must be closed exactly once regardless of which
                # path above was taken -- a write failure must not leak
                # the fd, and a terminated child's pipe still needs its
                # write end released promptly.
                try:
                    process.stdin.close()
                except BaseException:
                    pass

        def _wait() -> None:
            stdout_box: dict[str, Any] = {}
            stderr_box: dict[str, Any] = {}
            try:
                stdout_thread = Thread(target=_drain_bounded, args=(process.stdout, self._max_output_bytes, stdout_box))
                stderr_thread = Thread(target=_drain_bounded, args=(process.stderr, self._max_output_bytes, stderr_box))
                stdout_thread.start()
                stderr_thread.start()
                stdout_thread.join()
                stderr_thread.join()
                if "error" in stdout_box or "error" in stderr_box:
                    raise stdout_box.get("error") or stderr_box.get("error")
                process.wait()
            except BaseException:
                # An I/O failure while draining the pipes leaves the actual
                # subprocess outcome ambiguous, but the subprocess itself
                # must not be left running unattended in the background --
                # terminate it immediately and confirm the termination
                # actually took effect before reporting anything.
                terminate_process_tree(process)
                confirmed_stopped = False
                for _ in range(20):  # bounded confirmation wait, ~2s total
                    if process.poll() is not None:
                        confirmed_stopped = True
                        break
                    time.sleep(0.1)
                with self._lock:
                    state.communicate_failed = True
                    state.io_failure_confirmed_stopped = confirmed_stopped
                exited.set()
                return
            finally:
                # Explicitly close the pipe file descriptors this thread
                # owns (stdout/stderr) as soon as it is done with them,
                # rather than relying on garbage collection -- a long
                # test/CI run that spawns many sessions in one process
                # should not accumulate open fds waiting for GC, especially
                # under a container's typically low fd ulimit. stdin is
                # deliberately NOT closed here: _write_stdin() owns writing
                # to and closing stdin on its own thread, and closing it
                # from here too would race with that thread (this thread's
                # process.wait() can return before _write_stdin has run at
                # all if the child exits very quickly without reading
                # stdin).
                for pipe in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                with self._lock:
                    if state.home_dir is not None and not state.home_dir_cleaned_up:
                        state.home_dir_cleaned_up = True
                        _remove_directory(state.home_dir)
            with self._lock:
                state.stdout = stdout_box.get("text")
                state.stdout_truncated = bool(stdout_box.get("truncated"))
                state.stderr = stderr_box.get("text")
                state.stderr_truncated = bool(stderr_box.get("truncated"))
                state.returncode = process.returncode
            exited.set()

        def _watchdog() -> None:
            if exited.wait(timeout=self._max_runtime_seconds):
                return
            # exited.wait() timing out means the deadline elapsed before
            # _wait() called exited.set() -- but _wait() could still be in
            # the narrow window between the process actually exiting and
            # acquiring self._lock to record that. process.poll() is the
            # authoritative, race-free check of whether the process has
            # already exited on its own; only fall back to state's flags
            # for the (much rarer) case where the process is genuinely
            # still running.
            if process.poll() is not None:
                return
            with self._lock:
                if state.returncode is not None or state.communicate_failed:
                    return
                state.deadline_exceeded = True
            terminate_process_tree(process)

        thread = Thread(target=_wait, daemon=True)
        stdin_thread = Thread(target=_write_stdin, daemon=True)
        state.thread = thread
        state.stdin_thread = stdin_thread
        with self._lock:
            self._sessions[session_id] = state
        # Order matters: the drain and watchdog threads must already be
        # running before the prompt is written, so a child that starts
        # producing output (or needs to be killed) the moment it starts
        # reading stdin is never left undrained or unbounded even briefly.
        thread.start()
        Thread(target=_watchdog, daemon=True).start()
        stdin_thread.start()

        return AgentBackendSession(
            session_id=session_id,
            task_id=request.task_id,
            backend_id=self.identity.backend_id,
            status=AgentBackendStatus.RUNNING,
        )

    def events(self, session_id: str) -> Iterable[AgentBackendEvent]:
        state = self._require_session(session_id)
        emitted: list[AgentBackendEvent] = []
        with self._lock:
            if not state.started_event_emitted:
                state.started_event_emitted = True
                emitted.append(
                    AgentBackendEvent(
                        session_id=session_id,
                        sequence=1,
                        event_type="process_started",
                        status=AgentBackendStatus.RUNNING,
                    )
                )
            if state.returncode is not None and not state.completion_event_emitted:
                state.completion_event_emitted = True
                emitted.append(
                    AgentBackendEvent(
                        session_id=session_id,
                        sequence=2,
                        event_type="process_exited",
                        payload={"returncode": state.returncode},
                    )
                )
        return tuple(emitted)

    def cancel(self, session_id: str) -> None:
        state = self._require_session(session_id)
        with self._lock:
            state.cancelled = True
        terminate_process_tree(state.process)

    def result(self, session_id: str, *, wait_seconds: float | None = None) -> AgentBackendResult:
        """Return the current known outcome; optionally block briefly first.

        ``wait_seconds`` (adapter-specific, not part of the AgentBackend
        Protocol signature) lets a caller block for up to that long for the
        process to exit before checking, instead of polling in a tight
        loop; it defaults to ``default_wait_seconds`` from construction
        (0.0 -- a non-blocking check).

        While the process has not yet exited, this returns
        AgentBackendStatus.RUNNING (or CANCELLING if cancel() was already
        called) -- not UNKNOWN. RUNNING/CANCELLING mean "confirmed evidence
        the backend is still active"; UNKNOWN means "no confirmed evidence
        of the outcome at all". AgentBackendDispatcher.result() keeps the
        durable effect intent in "dispatching" for either of the former,
        so a caller can simply poll result() again later. Returning UNKNOWN
        for "not done yet" would instead durably commit the dispatch to
        "needs explicit reconciliation" -- a state a plain retry cannot
        undo even once the process finishes moments later -- so UNKNOWN is
        reserved here for this adapter's own I/O pipeline failing
        unexpectedly.

        A session that exceeded its ``max_runtime_seconds`` wall-clock
        deadline is terminated by a background watchdog independent of
        whether result() is ever called again; once that termination
        completes, the outcome here is CANCELLED with
        ``reconciliation_metadata["reason"] == "deadline_exceeded"``.
        """
        state = self._require_session(session_id)
        effective_wait = self._default_wait_seconds if wait_seconds is None else wait_seconds
        # Both the drain thread (state.thread) and the stdin-write thread
        # (state.stdin_thread) can independently observe the process's
        # outcome and set communicate_failed -- e.g. the drain thread can
        # see process.wait() return a real exit code at almost the same
        # moment the stdin thread's write() failure handler is still
        # mid-terminate_process_tree(), not yet having recorded
        # communicate_failed. Joining only one of them let this method
        # observe a bare returncode before the stdin failure was ever
        # recorded, misreporting an I/O failure as a normal exit. Join
        # both within the same overall wait budget so a caller-visible
        # result always reflects whichever thread finishes last.
        deadline = time.monotonic() + max(0.0, effective_wait)
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if state.stdin_thread is not None:
            state.stdin_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            if state.communicate_failed:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.UNKNOWN,
                    reconciliation_metadata={
                        "reason": "communicate_failed",
                        # The subprocess *outcome* remains ambiguous after an
                        # I/O failure -- UNKNOWN is still correct -- but this
                        # confirms whether the process itself was actually
                        # terminated (no orphan left running) or whether
                        # termination could not be confirmed within the
                        # bounded wait, which an operator should treat as a
                        # more urgent reconciliation case.
                        "process_confirmed_stopped": state.io_failure_confirmed_stopped,
                    },
                )
            if state.returncode is None:
                winding_down = state.cancelled or state.deadline_exceeded
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.CANCELLING if winding_down else AgentBackendStatus.RUNNING,
                    reconciliation_metadata={
                        "reason": "deadline_exceeded" if state.deadline_exceeded else "cancel_requested" if state.cancelled else "still_running",
                    },
                )
            if state.deadline_exceeded:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.CANCELLED,
                    reconciliation_metadata={"reason": "deadline_exceeded", "returncode": state.returncode},
                )
            if state.cancelled:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.CANCELLED,
                    reconciliation_metadata={"returncode": state.returncode},
                )
            if state.returncode == 0:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.COMPLETED,
                    reconciliation_metadata={
                        "returncode": 0,
                        "stdout_bytes": len((state.stdout or "").encode("utf-8")),
                        "stdout_truncated": state.stdout_truncated,
                    },
                )
            return AgentBackendResult(
                session_id=session_id,
                status=AgentBackendStatus.FAILED,
                reconciliation_metadata={
                    "returncode": state.returncode,
                    "stderr_excerpt": (state.stderr or "")[:2000],
                    "stderr_truncated": state.stderr_truncated,
                },
            )


class CodexExecBackend:
    """Production AgentBackend adapter for one ``codex exec`` invocation per session.

    This is the only entry point production composition code (Operation,
    DevFarm, etc.) may use. Unlike ``_CodexExecBackendImpl``, its
    constructor does not accept a ``command_builder`` at all -- every
    instance always uses the canonical command (``_build_default_command``):
    a pinned ``--sandbox`` mode from a fail-closed allowlist,
    ``--ignore-user-config``, ``--ignore-rules``, and the objective sent
    over stdin rather than argv. There is no parameter, override, or
    subclassing hook here that lets a caller substitute an arbitrary
    command -- that capability exists only in ``_CodexExecBackendImpl``,
    which this module's own tests import directly and which is never
    reachable from this class's public API.
    """

    def __init__(
        self,
        *,
        popen: PopenFactory | None = None,
        backend_version: str | None = None,
        max_output_bytes: int = 2 * 1024 * 1024,
        default_wait_seconds: float = 0.0,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
        sandbox_mode: str = "read-only",
        ignore_user_config: bool = True,
        ignore_rules: bool = True,
        extra_env_passthrough: frozenset[str] = frozenset(),
    ) -> None:
        canonical_command = _build_default_command(
            sandbox_mode=sandbox_mode,
            ignore_user_config=ignore_user_config,
            ignore_rules=ignore_rules,
        )
        self._impl = _CodexExecBackendImpl(
            command_builder=lambda _request: canonical_command,
            popen=popen,
            backend_version=backend_version,
            max_output_bytes=max_output_bytes,
            default_wait_seconds=default_wait_seconds,
            max_runtime_seconds=max_runtime_seconds,
            extra_env_passthrough=extra_env_passthrough,
        )

    @property
    def identity(self) -> AgentBackendIdentity:
        return self._impl.identity

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        return self._impl.start(request)

    def events(self, session_id: str) -> Iterable[AgentBackendEvent]:
        return self._impl.events(session_id)

    def cancel(self, session_id: str) -> None:
        return self._impl.cancel(session_id)

    def result(self, session_id: str, *, wait_seconds: float | None = None) -> AgentBackendResult:
        return self._impl.result(session_id, wait_seconds=wait_seconds)


__all__ = ["CodexExecBackend", "CodexExecBackendError", "CommandBuilder", "DEFAULT_MAX_RUNTIME_SECONDS"]
