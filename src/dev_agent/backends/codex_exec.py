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
  thread drains stdout/stderr via ``Popen.communicate()`` (matching the
  existing pattern in tools/executor.py's run_subprocess) so a slow or
  hanging invocation cannot block the caller.
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
  reserved for when this adapter's own subprocess-output reader thread
  fails unexpectedly, i.e. genuinely losing the ability to observe outcome.
- No discover() method is implemented, so AgentBackendDispatcher.reconcile_start
  correctly falls through to "backend discovery unavailable" and marks the
  dispatch UNKNOWN. This is honest: an in-process-memory session map cannot
  recover a session after this process restarts, since the OS process
  handle itself is gone.
- The default command_builder's exact `codex exec` flags are unverified
  against a live installed CLI in this environment (neither `codex` nor
  `claude` is installed here). Override command_builder at construction to
  match the operator's actual installed CLI version before production use.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from threading import Lock, Thread
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


def _default_command_builder(request: AgentBackendRequest) -> tuple[str, ...]:
    """Best-effort ``codex exec`` invocation.

    Not verified against a live binary in this environment. Pass a
    ``command_builder`` at construction to match the operator's installed
    codex CLI version before production use.
    """
    return ("codex", "exec", "--json", request.objective)


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
        "returncode",
        "communicate_failed",
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
        self.returncode: int | None = None
        # True only if the background reader thread itself raised while
        # draining the process's pipes -- a distinct, genuinely ambiguous
        # condition from "still running": we can no longer observe this
        # process's true state at all, which is exactly what UNKNOWN means.
        self.communicate_failed = False
        self.started_event_emitted = False
        self.completion_event_emitted = False


def _truncate(text: str | None, max_bytes: int) -> str | None:
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "...[truncated]"


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
    ) -> None:
        if backend_version is not None:
            self.identity = AgentBackendIdentity(
                backend_id="codex-exec",
                backend_version=backend_version,
                capabilities=("text",),
            )
        self._command_builder = command_builder or _default_command_builder
        self._popen: PopenFactory = popen or subprocess.Popen
        self._max_output_bytes = max_output_bytes
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
            stdin=subprocess.DEVNULL,
            text=True,
            cwd=str(workspace),
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )

        state = _SessionState(process=process, task_id=request.task_id)
        session_id = str(uuid4())

        def _wait() -> None:
            try:
                stdout, stderr = process.communicate()
            except BaseException:
                with self._lock:
                    state.communicate_failed = True
                return
            with self._lock:
                state.stdout = _truncate(stdout, self._max_output_bytes)
                state.stderr = _truncate(stderr, self._max_output_bytes)
                state.returncode = process.returncode

        thread = Thread(target=_wait, daemon=True)
        state.thread = thread
        with self._lock:
            self._sessions[session_id] = state
        thread.start()

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
        reserved here for a communicate() thread that raised unexpectedly
        (see the ``except BaseException`` in the background thread).
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
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.CANCELLING if state.cancelled else AgentBackendStatus.RUNNING,
                    reconciliation_metadata={"reason": "cancel_requested" if state.cancelled else "still_running"},
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
                    },
                )
            return AgentBackendResult(
                session_id=session_id,
                status=AgentBackendStatus.FAILED,
                reconciliation_metadata={
                    "returncode": state.returncode,
                    "stderr_excerpt": (state.stderr or "")[:2000],
                },
            )


__all__ = ["CodexExecBackend", "CodexExecBackendError", "CommandBuilder"]
