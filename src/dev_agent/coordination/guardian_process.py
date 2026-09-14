"""Static-profile process execution for the deterministic Guardian boundary.

The existing :mod:`guardian` module owns request validation, generation
fencing, and the durable action journal.  This module adds only the narrow
execution adapter for G1.  A request contains intent, never a command.  The
command, working directory, and environment profile come from a Host-owned
``LaunchProfile`` that must be explicitly supplied to the service.

This is intentionally not a daemon, scheduler, drain coordinator, rolling
updater, or recovery mechanism.  The subprocess runtime keeps live handles in
the current Guardian process; an interrupted action remains UNKNOWN and is
not replayed after a Guardian restart.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol

from .guardian import GuardianActionService, GuardianDecision, GuardianEvaluation, GuardianPolicy
from .protocol import (
    ControlAction,
    ControlRequest,
    CoordinationValidationError,
    GuardianActionRecord,
    PeerRecord,
)
from .protocol_helpers import ensure_json_safe, ensure_secret_free, validate_identifier, validate_text, validate_timestamp
from .store import CoordinationStore


_PROCESS_ACTIONS = frozenset({ControlAction.START, ControlAction.STOP, ControlAction.RESTART})
_MAX_ARGUMENTS = 32
_MAX_ENVIRONMENT_ITEMS = 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoordinationValidationError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class LaunchProfile:
    """Host-configured executable profile; never materialized from a request."""

    profile_id: str
    role: str
    generation: int
    revision: str
    executable: str
    arguments: tuple[str, ...] = field(default_factory=tuple)
    runtime_root: Path | str = "."
    environment_profile: str = "minimal"

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", validate_identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "role", validate_identifier(self.role, "role"))
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        object.__setattr__(self, "revision", validate_text(self.revision, "revision", max_chars=512))
        executable = validate_text(self.executable, "executable", max_chars=4_096)
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            raise CoordinationValidationError("executable must be an absolute Host-configured path")
        object.__setattr__(self, "executable", str(executable_path))

        if isinstance(self.arguments, (str, bytes)) or not isinstance(self.arguments, Sequence):
            raise CoordinationValidationError("arguments must be a sequence")
        if len(self.arguments) > _MAX_ARGUMENTS:
            raise CoordinationValidationError("arguments contains too many values")
        arguments = tuple(validate_text(item, f"arguments[{index}]", max_chars=4_096) for index, item in enumerate(self.arguments))
        object.__setattr__(self, "arguments", arguments)

        runtime_root = Path(self.runtime_root)
        if not runtime_root.is_absolute():
            raise CoordinationValidationError("runtime_root must be an absolute Host-configured path")
        object.__setattr__(self, "runtime_root", runtime_root)
        object.__setattr__(self, "environment_profile", validate_identifier(self.environment_profile, "environment_profile"))
        encoded = ensure_json_safe(self.to_dict(), "launch profile")
        ensure_secret_free(encoded, "launch profile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "role": self.role,
            "generation": self.generation,
            "revision": self.revision,
            "executable": self.executable,
            "arguments": list(self.arguments),
            "runtime_root": str(self.runtime_root),
            "environment_profile": self.environment_profile,
        }


@dataclass(frozen=True)
class ProcessHandle:
    """Bounded live-process identity returned by a runtime adapter."""

    profile_id: str
    role: str
    generation: int
    revision: str
    pid: int
    started_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", validate_identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "role", validate_identifier(self.role, "role"))
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        object.__setattr__(self, "revision", validate_text(self.revision, "revision", max_chars=512))
        object.__setattr__(self, "pid", _positive_int(self.pid, "pid"))
        object.__setattr__(self, "started_at", validate_timestamp(self.started_at, "started_at"))
        encoded = ensure_json_safe(self.to_dict(), "process handle")
        ensure_secret_free(encoded, "process handle")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "role": self.role,
            "generation": self.generation,
            "revision": self.revision,
            "pid": self.pid,
            "started_at": self.started_at,
        }


class GuardianProcessExecutionError(RuntimeError):
    """A process effect could not be confirmed; the journal closes UNKNOWN."""


class ProcessRuntime(Protocol):
    """Minimal process adapter used by the Guardian executor."""

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        ...

    def stop(self, profile: LaunchProfile) -> None:
        ...

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        ...


def _profile_map(profiles: Sequence[LaunchProfile]) -> dict[tuple[str, int], LaunchProfile]:
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence) or not profiles:
        raise CoordinationValidationError("profiles must be a non-empty sequence")
    result: dict[tuple[str, int], LaunchProfile] = {}
    seen_ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, LaunchProfile):
            raise CoordinationValidationError("profiles must contain LaunchProfile values")
        key = (profile.role, profile.generation)
        if key in result or profile.profile_id in seen_ids:
            raise CoordinationValidationError("profiles must have unique role/generation and profile_id values")
        result[key] = profile
        seen_ids.add(profile.profile_id)
    return result


class GuardianProcessPolicy(GuardianPolicy):
    """Generation policy narrowed to static START/STOP/RESTART profiles."""

    def __init__(self, profiles: Sequence[LaunchProfile], *, allowed_sender_roles: Sequence[str] = ("agent", "codex")) -> None:
        super().__init__(allowed_sender_roles=allowed_sender_roles, allowed_actions=_PROCESS_ACTIONS)
        self.profiles = _profile_map(profiles)

    def evaluate(
        self,
        request: ControlRequest,
        *,
        peers: Sequence[PeerRecord],
        now: str,
    ) -> GuardianEvaluation:
        base = super().evaluate(request, peers=peers, now=now)
        if not base.accepted:
            return base
        profile = self.profiles.get((request.target_role, request.target_generation))
        if profile is None:
            return self._process_result(request, GuardianDecision.PROFILE_NOT_FOUND, "no static launch profile is bound to the target generation")
        if request.desired_revision is not None and request.desired_revision != profile.revision:
            return self._process_result(request, GuardianDecision.REVISION_MISMATCH, "desired revision does not match the static launch profile")
        target = next(
            peer
            for peer in peers
            if peer.role == request.target_role and peer.generation == request.target_generation
        )
        if target.revision != profile.revision:
            return self._process_result(request, GuardianDecision.REVISION_MISMATCH, "target peer revision does not match the static launch profile")
        return base

    @staticmethod
    def _process_result(request: ControlRequest, decision: GuardianDecision, reason: str) -> GuardianEvaluation:
        return GuardianEvaluation(
            request_id=request.request_id,
            decision=decision,
            reason=reason,
            target_role=request.target_role,
            target_generation=request.target_generation,
        )


class GuardianProcessExecutor:
    """Resolve a request to a static profile and invoke the injected runtime."""

    def __init__(self, profiles: Sequence[LaunchProfile], runtime: ProcessRuntime) -> None:
        self.profiles = _profile_map(profiles)
        if not callable(getattr(runtime, "start", None)) or not callable(getattr(runtime, "stop", None)) or not callable(getattr(runtime, "restart", None)):
            raise CoordinationValidationError("runtime must provide start, stop, and restart")
        self.runtime = runtime

    def bound_profile(self, profile: LaunchProfile) -> LaunchProfile:
        if not isinstance(profile, LaunchProfile):
            raise CoordinationValidationError("profile must be a LaunchProfile")
        bound = self.profiles.get((profile.role, profile.generation))
        if bound is None or bound != profile:
            raise GuardianProcessExecutionError("launch profile is not bound to this Guardian")
        return bound

    def start_profile(self, profile: LaunchProfile) -> ProcessHandle:
        """Start one already-bound static profile and validate its handle."""

        bound = self.bound_profile(profile)
        handle = self.runtime.start(bound)
        self._validate_handle(handle, bound)
        return handle

    def stop_profile(self, profile: LaunchProfile) -> None:
        """Stop one already-bound static profile without retrying an uncertain result."""

        bound = self.bound_profile(profile)
        self.runtime.stop(bound)

    def execute(self, request: ControlRequest) -> None:
        if not isinstance(request, ControlRequest):
            raise CoordinationValidationError("request must be a ControlRequest")
        if request.action not in _PROCESS_ACTIONS:
            raise GuardianProcessExecutionError("request action is outside the Guardian process boundary")
        profile = self.profiles.get((request.target_role, request.target_generation))
        if profile is None:
            raise GuardianProcessExecutionError("static launch profile is unavailable")
        if request.desired_revision is not None and request.desired_revision != profile.revision:
            raise GuardianProcessExecutionError("request revision does not match static launch profile")
        if request.action is ControlAction.START:
            self.start_profile(profile)
            return
        elif request.action is ControlAction.STOP:
            self.stop_profile(profile)
            return
        else:
            handle = self.runtime.restart(profile)
        self._validate_handle(handle, profile)

    @staticmethod
    def _validate_handle(handle: ProcessHandle, profile: LaunchProfile) -> None:
        if not isinstance(handle, ProcessHandle):
            raise GuardianProcessExecutionError("runtime returned an invalid process handle")
        if (
            handle.profile_id != profile.profile_id
            or handle.role != profile.role
            or handle.generation != profile.generation
            or handle.revision != profile.revision
        ):
            raise GuardianProcessExecutionError("runtime returned an unbound process handle")


class GuardianProcessService:
    """Public G1 composition over the existing Guardian action journal."""

    def __init__(
        self,
        store: CoordinationStore,
        *,
        profiles: Sequence[LaunchProfile],
        runtime: ProcessRuntime,
        allowed_sender_roles: Sequence[str] = ("agent", "codex"),
    ) -> None:
        if not isinstance(store, CoordinationStore):
            raise CoordinationValidationError("store must be a CoordinationStore")
        profiles = tuple(profiles)
        self.policy = GuardianProcessPolicy(profiles, allowed_sender_roles=allowed_sender_roles)
        self.executor = GuardianProcessExecutor(profiles, runtime)
        self.actions = GuardianActionService(store, policy=self.policy, executor=self.executor)

    def submit(self, request: ControlRequest, *, now: str | None = None) -> GuardianActionRecord:
        return self.actions.submit(request, now=now)

    def reconcile_interrupted(self, request_id: str, *, now: str | None = None) -> GuardianActionRecord:
        return self.actions.reconcile_interrupted(request_id, now=now)


EnvironmentFactory = Callable[[LaunchProfile], Mapping[str, str]]


def _minimal_environment(_profile: LaunchProfile) -> dict[str, str]:
    """Return non-secret process basics; callers may inject a Host environment."""

    names = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    return {name: value for name in names if (value := os.environ.get(name)) is not None}


class SubprocessProcessRuntime:
    """Small foreground runtime using only static profiles and ``shell=False``."""

    def __init__(
        self,
        *,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
        environment_factory: EnvironmentFactory | None = None,
        stop_timeout_seconds: float = 10.0,
    ) -> None:
        if isinstance(stop_timeout_seconds, bool) or not isinstance(stop_timeout_seconds, (int, float)) or stop_timeout_seconds <= 0:
            raise CoordinationValidationError("stop_timeout_seconds must be positive")
        self._popen = popen or subprocess.Popen
        self._environment_factory = environment_factory or _minimal_environment
        self.stop_timeout_seconds = float(stop_timeout_seconds)
        self._processes: dict[tuple[str, int], subprocess.Popen[str]] = {}

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        self._validate_profile_root(profile)
        key = (profile.role, profile.generation)
        current = self._processes.get(key)
        if current is not None:
            if current.poll() is None:
                raise GuardianProcessExecutionError("target process is already running")
            self._processes.pop(key, None)
        command = [profile.executable, *profile.arguments]
        try:
            process = self._popen(
                command,
                cwd=str(profile.runtime_root),
                env=dict(self._environment_factory(profile)),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception as exc:
            raise GuardianProcessExecutionError("process start failed") from exc
        pid = getattr(process, "pid", None)
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise GuardianProcessExecutionError("process start returned an invalid pid")
        self._processes[key] = process
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=pid,
        )

    def stop(self, profile: LaunchProfile) -> None:
        process = self._processes.get((profile.role, profile.generation))
        if process is None:
            raise GuardianProcessExecutionError("target process handle is unavailable; reconcile before retry")
        if process.poll() is not None:
            self._processes.pop((profile.role, profile.generation), None)
            return
        try:
            process.terminate()
            process.wait(timeout=self.stop_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise GuardianProcessExecutionError("process stop was not confirmed before deadline") from exc
        except Exception as exc:
            raise GuardianProcessExecutionError("process stop failed") from exc
        self._processes.pop((profile.role, profile.generation), None)

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        self.stop(profile)
        return self.start(profile)

    @staticmethod
    def _validate_profile_root(profile: LaunchProfile) -> None:
        try:
            if not profile.runtime_root.is_dir():
                raise GuardianProcessExecutionError("runtime_root is not an existing directory")
        except OSError as exc:
            raise GuardianProcessExecutionError("runtime_root could not be inspected") from exc


__all__ = [
    "EnvironmentFactory",
    "GuardianProcessExecutionError",
    "GuardianProcessExecutor",
    "GuardianProcessPolicy",
    "GuardianProcessService",
    "LaunchProfile",
    "ProcessHandle",
    "ProcessRuntime",
    "SubprocessProcessRuntime",
]
