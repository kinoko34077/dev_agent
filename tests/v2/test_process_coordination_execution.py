from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from src.dev_agent.coordination.guardian_process import (
    GuardianProcessService,
    GuardianProcessExecutionError,
    InMemoryProcessOwnership,
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
    lease_until = "2099-01-01T00:00:00+00:00"
    store.register_peer(_peer(role="codex", instance_id="codex-1", generation=1, lease_until=lease_until))
    store.register_peer(_peer(role="agent", instance_id="agent-1", generation=12, lease_until=lease_until))
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


def test_shared_process_ownership_rejects_duplicate_after_guardian_restart(tmp_path):
    class _Process:
        pid = 9010

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("the first Guardian must not be stopped by the second")

        def wait(self, timeout):
            raise AssertionError("the first Guardian must not be stopped by the second")

    ownership = InMemoryProcessOwnership()
    runtime_one = SubprocessProcessRuntime(popen=lambda *args, **kwargs: _Process(), ownership=ownership)
    runtime_one.start(_profile(tmp_path))

    runtime_two = SubprocessProcessRuntime(popen=lambda *args, **kwargs: _Process(), ownership=ownership)
    with pytest.raises(GuardianProcessExecutionError, match="ownership"):
        runtime_two.start(_profile(tmp_path))


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


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership is Windows-specific")
def test_windows_job_object_ends_managed_child_when_guardian_process_dies(tmp_path):
    sentinel = tmp_path / "child-completed.txt"
    child_code = (
        "import time; "
        "from pathlib import Path; "
        f"time.sleep(30); Path({str(sentinel)!r}).write_text('completed', encoding='utf-8')"
    )
    guardian_code = (
        "import sys; "
        "from pathlib import Path; "
        "from src.dev_agent.coordination.guardian_process import LaunchProfile, SubprocessProcessRuntime; "
        f"profile=LaunchProfile(profile_id='job-child', role='agent', generation=1, revision='rev-job', "
        f"executable={sys.executable!r}, arguments=('-c', {child_code!r}), runtime_root=Path({str(tmp_path)!r}), "
        "environment_profile='minimal'); "
        "handle=SubprocessProcessRuntime().start(profile); "
        "print(handle.pid, flush=True); "
        "sys.stdin.read()"
    )
    guardian = subprocess.Popen(
        [sys.executable, "-c", guardian_code],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    try:
        line = guardian.stdout.readline() if guardian.stdout is not None else ""
        child_pid = int(line.strip())
        guardian.terminate()
        guardian.wait(timeout=5)
        for _ in range(50):
            process_listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if str(child_pid) not in process_listing.stdout:
                break
            time.sleep(0.1)
        assert str(child_pid) not in process_listing.stdout
        assert not sentinel.exists()
    finally:
        if guardian.poll() is None:
            guardian.kill()
            guardian.wait(timeout=5)
        if child_pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
