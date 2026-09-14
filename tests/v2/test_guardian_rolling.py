from __future__ import annotations

import pytest

from src.dev_agent.coordination.guardian_process import (
    GuardianProcessExecutor,
    GuardianProcessExecutionError,
    LaunchProfile,
    ProcessHandle,
)
from src.dev_agent.coordination.rolling import RollingDecision, RollingRestartService


def _profile(tmp_path, *, profile_id: str, generation: int, revision: str) -> LaunchProfile:
    return LaunchProfile(
        profile_id=profile_id,
        role="agent",
        generation=generation,
        revision=revision,
        executable=str(tmp_path / "agent-runtime.exe"),
        arguments=(),
        runtime_root=tmp_path,
        environment_profile="minimal",
    )


class _Runtime:
    def __init__(self, *, fail_stop: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_stop = fail_stop

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        self.calls.append(("start", profile.profile_id))
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=1000 + profile.generation,
        )

    def stop(self, profile: LaunchProfile) -> None:
        self.calls.append(("stop", profile.profile_id))
        if self.fail_stop and profile.profile_id == "old":
            raise GuardianProcessExecutionError("stop outcome is unknown")

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        raise AssertionError("rolling service must not use restart")


def test_rolling_restart_starts_and_health_checks_new_before_stopping_old(tmp_path):
    runtime = _Runtime()
    service = RollingRestartService(
        GuardianProcessExecutor(
            (_profile(tmp_path, profile_id="old", generation=1, revision="rev-old"),
             _profile(tmp_path, profile_id="new", generation=2, revision="rev-new")),
            runtime,
        )
    )
    observed: list[int] = []

    result = service.roll(
        _profile(tmp_path, profile_id="old", generation=1, revision="rev-old"),
        _profile(tmp_path, profile_id="new", generation=2, revision="rev-new"),
        health_check=lambda handle: observed.append(handle.generation) or True,
    )

    assert result.decision is RollingDecision.COMPLETED
    assert result.new_handle is not None
    assert result.old_stopped is True
    assert result.reconciliation_required is False
    assert observed == [2]
    assert runtime.calls == [("start", "new"), ("stop", "old")]


def test_unhealthy_new_generation_keeps_old_running(tmp_path):
    runtime = _Runtime()
    old = _profile(tmp_path, profile_id="old", generation=1, revision="rev-old")
    new = _profile(tmp_path, profile_id="new", generation=2, revision="rev-new")
    service = RollingRestartService(GuardianProcessExecutor((old, new), runtime))

    result = service.roll(old, new, health_check=lambda _handle: False)

    assert result.decision is RollingDecision.NEW_HEALTH_FAILED
    assert result.old_stopped is False
    assert result.reconciliation_required is False
    assert runtime.calls == [("start", "new"), ("stop", "new")]


def test_old_stop_failure_never_retries_and_requires_reconciliation(tmp_path):
    runtime = _Runtime(fail_stop=True)
    old = _profile(tmp_path, profile_id="old", generation=1, revision="rev-old")
    new = _profile(tmp_path, profile_id="new", generation=2, revision="rev-new")
    service = RollingRestartService(GuardianProcessExecutor((old, new), runtime))

    result = service.roll(old, new, health_check=lambda _handle: True)

    assert result.decision is RollingDecision.OLD_STOP_UNKNOWN
    assert result.old_stopped is False
    assert result.reconciliation_required is True
    assert runtime.calls == [("start", "new"), ("stop", "old")]


def test_rolling_restart_requires_a_strictly_new_generation(tmp_path):
    runtime = _Runtime()
    old = _profile(tmp_path, profile_id="old", generation=2, revision="rev-old")
    new = _profile(tmp_path, profile_id="new", generation=3, revision="rev-new")
    same = _profile(tmp_path, profile_id="same", generation=2, revision="rev-new")
    service = RollingRestartService(GuardianProcessExecutor((old, new), runtime))

    with pytest.raises(ValueError, match="new generation"):
        service.roll(old, same, health_check=lambda _handle: True)

    assert runtime.calls == []
