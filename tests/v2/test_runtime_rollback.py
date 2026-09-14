from __future__ import annotations

import subprocess

from src.dev_agent.coordination.guardian_process import (
    GuardianProcessExecutionError,
    GuardianProcessExecutor,
    LaunchProfile,
    ProcessHandle,
)
from src.dev_agent.coordination.rolling import RollingRestartService
from src.dev_agent.coordination.runtime_rollback import (
    RollbackDecision,
    RuntimeRollbackService,
)
from src.dev_agent.coordination.runtime_release import RevisionPinnedRuntimeStore


def _git(root, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path):
    root = tmp_path / "development"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "runtime-tests@example.invalid")
    _git(root, "config", "user.name", "Runtime Tests")
    (root / "agent.py").write_text("REVISION = 'good'\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "known good runtime")
    good = _git(root, "rev-parse", "HEAD")
    (root / "agent.py").write_text("REVISION = 'bad'\n", encoding="utf-8")
    _git(root, "commit", "-am", "bad runtime")
    bad = _git(root, "rev-parse", "HEAD")
    return root, good, bad


def _profile(tmp_path, *, profile_id: str, generation: int, revision: str, runtime_root) -> LaunchProfile:
    return LaunchProfile(
        profile_id=profile_id,
        role="agent",
        generation=generation,
        revision=revision,
        executable=str(tmp_path / f"{profile_id}.exe"),
        runtime_root=runtime_root,
        environment_profile="minimal",
    )


class _Runtime:
    def __init__(self, *, fail_old_stop: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_old_stop = fail_old_stop

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        self.calls.append(("start", profile.profile_id))
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=3000 + profile.generation,
        )

    def stop(self, profile: LaunchProfile) -> None:
        self.calls.append(("stop", profile.profile_id))
        if self.fail_old_stop and profile.profile_id == "old":
            raise GuardianProcessExecutionError("old process stop outcome is unknown")

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        raise AssertionError("rollback must use rolling start/health/stop")


def _service(tmp_path, *, old, new, runtime, source):
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    executor = GuardianProcessExecutor((old, new), runtime)
    return RuntimeRollbackService(store, RollingRestartService(executor))


def test_rollback_materializes_last_known_good_before_rolling_to_it(tmp_path):
    source, good, bad = _repository(tmp_path)
    release_root = tmp_path / "releases" / good
    old = _profile(tmp_path, profile_id="old", generation=1, revision=bad, runtime_root=tmp_path / "mutable")
    new = _profile(tmp_path, profile_id="good", generation=2, revision=good, runtime_root=release_root)
    runtime = _Runtime()

    result = _service(tmp_path, old=old, new=new, runtime=runtime, source=source).rollback(
        old,
        new,
        health_check=lambda handle: handle.revision == good,
    )

    assert result.decision is RollbackDecision.COMPLETED
    assert result.release is not None
    assert result.release.revision == good
    assert result.rolling.old_stopped is True
    assert runtime.calls == [("start", "good"), ("stop", "old")]


def test_rollback_does_not_execute_when_known_good_release_is_unavailable(tmp_path):
    source, _good, bad = _repository(tmp_path)
    old = _profile(tmp_path, profile_id="old", generation=1, revision=bad, runtime_root=tmp_path / "mutable")
    new = _profile(tmp_path, profile_id="good", generation=2, revision="0" * 40, runtime_root=tmp_path / "releases" / ("0" * 40))
    runtime = _Runtime()

    result = _service(tmp_path, old=old, new=new, runtime=runtime, source=source).rollback(
        old,
        new,
        health_check=lambda _handle: True,
    )

    assert result.decision is RollbackDecision.RELEASE_UNAVAILABLE
    assert result.release is None
    assert result.rolling is None
    assert runtime.calls == []


def test_rollback_keeps_old_generation_when_known_good_health_fails(tmp_path):
    source, good, bad = _repository(tmp_path)
    old = _profile(tmp_path, profile_id="old", generation=1, revision=bad, runtime_root=tmp_path / "mutable")
    new = _profile(tmp_path, profile_id="good", generation=2, revision=good, runtime_root=tmp_path / "releases" / good)
    runtime = _Runtime()

    result = _service(tmp_path, old=old, new=new, runtime=runtime, source=source).rollback(
        old,
        new,
        health_check=lambda _handle: False,
    )

    assert result.decision is RollbackDecision.NEW_HEALTH_FAILED
    assert result.rolling is not None
    assert result.rolling.old_stopped is False
    assert result.rolling.reconciliation_required is False
    assert runtime.calls == [("start", "good"), ("stop", "good")]


def test_rollback_reports_unknown_when_old_generation_stop_is_not_confirmed(tmp_path):
    source, good, bad = _repository(tmp_path)
    old = _profile(tmp_path, profile_id="old", generation=1, revision=bad, runtime_root=tmp_path / "mutable")
    new = _profile(tmp_path, profile_id="good", generation=2, revision=good, runtime_root=tmp_path / "releases" / good)
    runtime = _Runtime(fail_old_stop=True)

    result = _service(tmp_path, old=old, new=new, runtime=runtime, source=source).rollback(
        old,
        new,
        health_check=lambda _handle: True,
    )

    assert result.decision is RollbackDecision.OLD_STOP_UNKNOWN
    assert result.rolling is not None
    assert result.rolling.reconciliation_required is True
    assert runtime.calls == [("start", "good"), ("stop", "old")]
