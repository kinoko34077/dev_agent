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
  AgentBackendStatus.UNKNOWN rather than inventing a "still running" result
  status that AgentBackendResult's schema does not accept -- this is
  intentional: "not enough confirmed evidence yet" is exactly what UNKNOWN
  means throughout this codebase's fail-closed design, and the caller is
  expected to poll result() again.
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
        default_wait_seconds: float = 300.0,
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
        # AgentBackendDispatcher.result() calls backend.result(session_id)
        # with no wait argument, and treats a returned UNKNOWN as a durable
        # "needs explicit reconciliation" signal -- once persisted, a plain
        # retry cannot self-heal even if the process finishes moments later.
        # So the *default* wait must itself be long enough to cover a normal
        # completion; only a genuinely stuck process should fall through to
        # UNKNOWN. Override default_wait_seconds for one call via the
        # wait_seconds keyword (used by this module's own tests to observe
        # the "still running" branch quickly).
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
                stdout, stderr = None, None
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
        """Block for up to ``wait_seconds`` (default: ``default_wait_seconds``
        from construction) for the process to exit, then return the terminal
        outcome -- or UNKNOWN if it still has not exited within that bound.

        ``wait_seconds`` (adapter-specific, not part of the AgentBackend
        Protocol signature) lets a caller override the adapter's configured
        default for one call, e.g. a short wait in tests. The default is
        deliberately generous (not 0): AgentBackendDispatcher.result() calls
        this with no argument and treats a returned UNKNOWN as "needs
        explicit reconciliation" durably -- a plain retry cannot undo that
        even if the process finishes moments later -- so returning UNKNOWN
        must mean "genuinely stuck past its allotted time", not merely
        "check back shortly".
        """
        state = self._require_session(session_id)
        effective_wait = self._default_wait_seconds if wait_seconds is None else wait_seconds
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, effective_wait))
        with self._lock:
            if state.returncode is None:
                return AgentBackendResult(
                    session_id=session_id,
                    status=AgentBackendStatus.UNKNOWN,
                    reconciliation_metadata={"reason": "still_running"},
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
