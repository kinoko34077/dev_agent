from __future__ import annotations

from dataclasses import replace
import sys

import pytest

from src.dev_agent.coordination.guardian_process import (
    GuardianProcessService,
    LaunchProfile,
    ProcessHandle,
    SubprocessProcessRuntime,
)
from src.dev_agent.coordination.protocol import ControlAction, ControlRequest, GuardianActionStatus
from src.dev_agent.coordination.store import CoordinationStore

from tests.v2.test_process_coordination_guardian import _peer


def _profile(tmp_path, *, generation: int = 12, revision: str = "rev-a") -> LaunchProfile:
    return LaunchProfile(
        profile_id="agent-foreground",
        role="agent",
        generation=generation,
        revision=revision,
        executable=sys.executable,
        arguments=("-c", "pass"),
        runtime_root=tmp_path,
        environment_profile="minimal",
    )


def _request(
    *,
    action: ControlAction = ControlAction.RESTART,
    desired_revision: str | None = None,
    target_role: str = "agent",
    target_generation: int = 12,
) -> ControlRequest:
    return ControlRequest(
        request_id=f"request-{action.value.lower()}",
        sender_role="codex",
        sender_instance_id="codex-1",
        sender_generation=1,
        target_role=target_role,
        target_generation=target_generation,
        action=action,
        reason="bounded Guardian process operation",
        created_at="2026-09-15T12:00:00+00:00",
        idempotency_key=f"operation-{action.value.lower()}",
        desired_revision=desired_revision,
    )


def _store(tmp_path):
    store = CoordinationStore(tmp_path / "coordination.sqlite3")
    store.register_peer(_peer(role="codex", instance_id="codex-1", generation=1))
    store.register_peer(_peer(role="agent", instance_id="agent-1", generation=12))
    return store


class _Runtime:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        self.calls.append(("start", profile.profile_id))
        if self.fail:
            raise RuntimeError("process start result is ambiguous")
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=4321,
        )

    def stop(self, profile: LaunchProfile) -> None:
        self.calls.append(("stop", profile.profile_id))
        if self.fail:
            raise RuntimeError("process stop result is ambiguous")

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        self.calls.append(("restart", profile.profile_id))
        if self.fail:
            raise RuntimeError("process restart result is ambiguous")
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=4322,
        )


@pytest.mark.parametrize("action", (ControlAction.START, ControlAction.STOP, ControlAction.RESTART))
def test_guardian_process_service_executes_only_static_process_profiles(tmp_path, action):
    store = _store(tmp_path)
    runtime = _Runtime()
    try:
        record = GuardianProcessService(store, profiles=(_profile(tmp_path),), runtime=runtime).submit(
            _request(action=action),
            now="2026-09-15T12:01:00+00:00",
        )

        assert record.status is GuardianActionStatus.COMPLETED
        assert runtime.calls == [(action.value.lower(), "agent-foreground")]
        assert "command" not in _request(action=action).to_dict()
    finally:
        store.close()


def test_guardian_process_policy_rejects_unbound_profile_and_revision(tmp_path):
    store = _store(tmp_path)
    runtime = _Runtime()
    try:
        service = GuardianProcessService(store, profiles=(_profile(tmp_path),), runtime=runtime)
        missing = service.submit(
            _request(action=ControlAction.START, target_role="codex", target_generation=1),
            now="2026-09-15T12:01:00+00:00",
        )
        mismatch = service.submit(
            _request(action=ControlAction.RESTART, desired_revision="rev-other"),
            now="2026-09-15T12:02:00+00:00",
        )

        assert missing.status is GuardianActionStatus.REJECTED
        assert missing.decision == "PROFILE_NOT_FOUND"
        assert mismatch.status is GuardianActionStatus.REJECTED
        assert mismatch.decision == "REVISION_MISMATCH"
        assert runtime.calls == []
    finally:
        store.close()


def test_guardian_process_failure_is_unknown_and_not_replayed(tmp_path):
    store = _store(tmp_path)
    runtime = _Runtime(fail=True)
    try:
        service = GuardianProcessService(store, profiles=(_profile(tmp_path),), runtime=runtime)
        request = _request(action=ControlAction.RESTART)
        record = service.submit(request, now="2026-09-15T12:01:00+00:00")
        duplicate = service.submit(request, now="2026-09-15T12:02:00+00:00")

        assert record.status is GuardianActionStatus.UNKNOWN
        assert record.reconciliation_required is True
        assert duplicate == record
        assert runtime.calls == [("restart", "agent-foreground")]
    finally:
        store.close()


def test_subprocess_runtime_uses_static_profile_without_shell(tmp_path):
    seen = {}

    class _Process:
        pid = 9001

        def poll(self):
            return None

        def terminate(self):
            seen["terminated"] = True

        def wait(self, timeout):
            seen["timeout"] = timeout
            return 0

    def popen(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _Process()

    runtime = SubprocessProcessRuntime(popen=popen, stop_timeout_seconds=2.0)
    profile = _profile(tmp_path)
    handle = runtime.start(profile)
    runtime.stop(profile)

    assert handle.pid == 9001
    assert seen["args"] == ([sys.executable, "-c", "pass"],)
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["cwd"] == str(tmp_path)
    assert seen["kwargs"]["stdin"] is not None
    assert seen["terminated"] is True
    assert seen["timeout"] == 2.0


def test_subprocess_runtime_cleans_up_process_when_popen_returns_invalid_pid(tmp_path):
    seen = {}

    class _Process:
        pid = 0

        def poll(self):
            return None

        def terminate(self):
            seen["terminated"] = True

        def wait(self, timeout):
            seen["timeout"] = timeout
            return 0

    def popen(*args, **kwargs):
        return _Process()

    runtime = SubprocessProcessRuntime(popen=popen, stop_timeout_seconds=2.0)

    with pytest.raises(RuntimeError, match="invalid pid"):
        runtime.start(_profile(tmp_path))

    assert seen == {"terminated": True, "timeout": 2.0}


def test_guardian_process_service_starts_and_stops_real_local_subprocess(tmp_path):
    store = _store(tmp_path)
    profile = LaunchProfile(
        profile_id="agent-real-local",
        role="agent",
        generation=12,
        revision="rev-a",
        executable=sys.executable,
        arguments=("-c", "import time; time.sleep(30)"),
        runtime_root=tmp_path,
        environment_profile="minimal",
    )
    runtime = SubprocessProcessRuntime(stop_timeout_seconds=5.0)
    service = GuardianProcessService(store, profiles=(profile,), runtime=runtime)
    start_request = _request(action=ControlAction.START)
    stop_request = replace(
        _request(action=ControlAction.STOP),
        request_id="request-stop-real-local",
        idempotency_key="operation-stop-real-local",
    )
    try:
        started = service.submit(start_request, now="2026-09-15T12:01:00+00:00")
        assert started.status is GuardianActionStatus.COMPLETED
        assert started.result_code == "executor_completed"

        stopped = service.submit(stop_request, now="2026-09-15T12:01:01+00:00")
        assert stopped.status is GuardianActionStatus.COMPLETED
        assert stopped.result_code == "executor_completed"
    finally:
        store.close()
