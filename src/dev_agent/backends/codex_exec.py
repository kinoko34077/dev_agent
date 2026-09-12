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


def _build_default_command(
    *,
    sandbox_mode: str,
    ignore_user_config: bool,
    ignore_rules: bool,
) -> tuple[str, ...]:
    command = ["codex", "exec", "--json"]
    if sandbox_mode:
        command.extend(["--sandbox", sandbox_mode])
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


class _SessionState:
    __slots__ = (
        "process",
        "thread",
        "task_id",
        "cancelled",
        "stdout",
        "stderr",
        "stdout_truncated",
        "stderr_truncated",
        "returncode",
        "communicate_failed",
        "deadline_exceeded",
        "started_at",
        "started_event_emitted",
        "completion_event_emitted",
    )

    def __init__(self, *, process: "subprocess.Popen[str]", task_id: str) -> None:
        self.process = process
        self.thread: Thread | None = None
        self.task_id = task_id
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
        self.deadline_exceeded = False
        self.started_at = time.monotonic()
        self.started_event_emitted = False
        self.completion_event_emitted = False


_STREAM_CHUNK_CHARS = 65536


def _drain_bounded(pipe: Any, max_chars: int, out: dict[str, Any]) -> None:
    """Read ``pipe`` to EOF, keeping at most ``max_chars`` and discarding the
    rest -- but continuing to read past the cap so the writing process is
    never blocked on a full pipe buffer. Writes "text" and "truncated" into
    ``out``. Runs on its own thread; a caller must run one of these per pipe
    (stdout and stderr) concurrently to avoid the classic dual-pipe deadlock.
    """
    collected: list[str] = []
    total = 0
    truncated = False
    try:
        while True:
            chunk = pipe.read(_STREAM_CHUNK_CHARS)
            if not chunk:
                break
            if not truncated:
                remaining = max_chars - total
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
    # text past max_chars.
    out["text"] = "".join(collected)
    out["truncated"] = truncated


class CodexExecBackend:
    """AgentBackend adapter that runs one ``codex exec`` invocation per session."""

    identity = AgentBackendIdentity(
        backend_id="codex-exec",
        backend_version="unversioned",
        capabilities=("text",),
    )

    def __init__(
        self,
        *,
        command_builder: CommandBuilder | None = None,
        popen: PopenFactory | None = None,
        backend_version: str | None = None,
        max_output_bytes: int = 2 * 1024 * 1024,
        default_wait_seconds: float = 0.0,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
        sandbox_mode: str = "read-only",
        ignore_user_config: bool = True,
        ignore_rules: bool = True,
    ) -> None:
        if backend_version is not None:
            self.identity = AgentBackendIdentity(
                backend_id="codex-exec",
                backend_version=backend_version,
                capabilities=("text",),
            )
        if isinstance(max_runtime_seconds, bool) or not isinstance(max_runtime_seconds, (int, float)) or max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be a positive number")
        if command_builder is not None:
            self._command_builder: CommandBuilder = command_builder
        else:
            default_command = _build_default_command(
                sandbox_mode=sandbox_mode,
                ignore_user_config=ignore_user_config,
                ignore_rules=ignore_rules,
            )
            self._command_builder = lambda _request: default_command
        self._popen: PopenFactory = popen or subprocess.Popen
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

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        process = self._popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            cwd=str(workspace),
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        try:
            process.stdin.write(request.objective)
            process.stdin.close()
        except BaseException:
            terminate_process_tree(process)
            raise

        state = _SessionState(process=process, task_id=request.task_id)
        session_id = str(uuid4())
        exited = Event()

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
                with self._lock:
                    state.communicate_failed = True
                exited.set()
                return
            finally:
                # Explicitly close pipe file descriptors as soon as this
                # session is done with them, rather than relying on garbage
                # collection -- a long test/CI run that spawns many sessions
                # in one process should not accumulate open fds waiting for
                # GC, especially under a container's typically low fd
                # ulimit.
                for pipe in (getattr(process, "stdin", None), getattr(process, "stdout", None), getattr(process, "stderr", None)):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
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
            with self._lock:
                if state.returncode is not None or state.communicate_failed:
                    return
                state.deadline_exceeded = True
            terminate_process_tree(process)

        thread = Thread(target=_wait, daemon=True)
        state.thread = thread
        with self._lock:
            self._sessions[session_id] = state
        thread.start()
        Thread(target=_watchdog, daemon=True).start()

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
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, effective_wait))
        with self._lock:
            if state.communicate_failed:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.UNKNOWN,
                    reconciliation_metadata={"reason": "communicate_failed"},
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


__all__ = ["CodexExecBackend", "CodexExecBackendError", "CommandBuilder", "DEFAULT_MAX_RUNTIME_SECONDS"]
