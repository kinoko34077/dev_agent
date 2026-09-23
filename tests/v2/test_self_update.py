from __future__ import annotations

import subprocess

from src.dev_agent.coordination.guardian_process import (
    GuardianProcessExecutor,
    LaunchProfile,
    ProcessHandle,
)
from src.dev_agent.coordination.rolling import RollingRestartService
from src.dev_agent.coordination.self_update import (
    SelfUpdateDecision,
    SelfUpdateService,
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
    (root / "agent.py").write_text("REVISION = 'known-good'\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "known good runtime")
    good = _git(root, "rev-parse", "HEAD")
    (root / "agent.py").write_text("REVISION = 'candidate'\n", encoding="utf-8")
    _git(root, "commit", "-am", "candidate runtime")
    candidate = _git(root, "rev-parse", "HEAD")
    return root, good, candidate


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
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def start(self, profile: LaunchProfile) -> ProcessHandle:
        self.calls.append(("start", profile.profile_id))
        return ProcessHandle(
            profile_id=profile.profile_id,
            role=profile.role,
            generation=profile.generation,
            revision=profile.revision,
            pid=4000 + profile.generation,
        )

    def stop(self, profile: LaunchProfile) -> None:
        self.calls.append(("stop", profile.profile_id))

    def restart(self, profile: LaunchProfile) -> ProcessHandle:
        raise AssertionError("self-update must use rolling start/health/stop")


def _service(store, profiles):
    runtime = _Runtime()
    executor = GuardianProcessExecutor(profiles, runtime)
    return SelfUpdateService(
        store,
        RollingRestartService(executor),
        trusted_ref="HEAD",
    ), runtime


def test_good_candidate_promotes_and_records_active_revision(tmp_path):
    source, good, candidate = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    active_release = store.materialize(good)
    active = _profile(tmp_path, profile_id="active", generation=1, revision=good, runtime_root=active_release.runtime_root)
    candidate_profile = _profile(
        tmp_path,
        profile_id="candidate",
        generation=2,
        revision=candidate,
        runtime_root=tmp_path / "releases" / candidate,
    )
    service, runtime = _service(store, (active, candidate_profile))

    result = service.promote(active, candidate_profile, health_check=lambda handle: handle.revision == candidate)

    assert result.decision is SelfUpdateDecision.PROMOTED
    assert result.active_revision == candidate
    assert runtime.calls == [("start", "candidate"), ("stop", "active")]
    assert service.journal().active_revision == candidate


def test_failed_candidate_is_recorded_and_not_retried_without_new_revision(tmp_path):
    source, good, candidate = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    active_release = store.materialize(good)
    active = _profile(tmp_path, profile_id="active", generation=1, revision=good, runtime_root=active_release.runtime_root)
    candidate_profile = _profile(
        tmp_path,
        profile_id="candidate",
        generation=2,
        revision=candidate,
        runtime_root=tmp_path / "releases" / candidate,
    )
    service, runtime = _service(store, (active, candidate_profile))

    first = service.promote(active, candidate_profile, health_check=lambda _handle: False)
    restarted_service, restarted_runtime = _service(store, (active, candidate_profile))
    second = restarted_service.promote(active, candidate_profile, health_check=lambda _handle: True)

    assert first.decision is SelfUpdateDecision.CANDIDATE_HEALTH_FAILED
    assert second.decision is SelfUpdateDecision.CANDIDATE_ALREADY_FAILED
    assert runtime.calls == [("start", "candidate"), ("stop", "candidate")]
    assert restarted_runtime.calls == []
    assert candidate in service.journal().failed_revisions


def test_preflight_failure_does_not_start_candidate(tmp_path):
    source, good, candidate = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    active_release = store.materialize(good)
    active = _profile(tmp_path, profile_id="active", generation=1, revision=good, runtime_root=active_release.runtime_root)
    candidate_profile = _profile(
        tmp_path,
        profile_id="candidate",
        generation=2,
        revision=candidate,
        runtime_root=tmp_path / "releases" / candidate,
    )
    service, runtime = _service(store, (active, candidate_profile))

    result = service.promote(active, candidate_profile, health_check=lambda _handle: True, preflight=lambda: False)

    assert result.decision is SelfUpdateDecision.PREFLIGHT_FAILED
    assert runtime.calls == []


def test_unresolvable_candidate_is_rejected_before_release_or_process_effect(tmp_path):
    source, good, _candidate = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    active_release = store.materialize(good)
    active = _profile(tmp_path, profile_id="active", generation=1, revision=good, runtime_root=active_release.runtime_root)
    candidate_profile = _profile(
        tmp_path,
        profile_id="candidate",
        generation=2,
        revision="not-a-commit",
        runtime_root=tmp_path / "releases" / "not-a-commit",
    )
    service, runtime = _service(store, (active, candidate_profile))

    result = service.promote(active, candidate_profile, health_check=lambda _handle: True)

    assert result.decision is SelfUpdateDecision.TRUST_REJECTED
    assert runtime.calls == []


def test_rollback_reuses_existing_pinned_release_and_rolling_boundary(tmp_path):
    source, good, candidate = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    good_release = store.materialize(good)
    active = _profile(tmp_path, profile_id="active", generation=1, revision=good, runtime_root=good_release.runtime_root)
    candidate_profile = _profile(
        tmp_path,
        profile_id="candidate",
        generation=2,
        revision=candidate,
        runtime_root=tmp_path / "releases" / candidate,
    )
    lkg_profile = _profile(
        tmp_path,
        profile_id="active-lkg",
        generation=3,
        revision=good,
        runtime_root=good_release.runtime_root,
    )
    service, runtime = _service(store, (active, candidate_profile, lkg_profile))
    promoted = service.promote(active, candidate_profile, health_check=lambda _handle: True)
    assert promoted.decision is SelfUpdateDecision.PROMOTED

    rollback = service.rollback(candidate_profile, lkg_profile, health_check=lambda handle: handle.revision == good)

    assert rollback.decision is SelfUpdateDecision.ROLLED_BACK
    assert rollback.active_revision == good
    assert runtime.calls == [
        ("start", "candidate"),
        ("stop", "active"),
        ("start", "active-lkg"),
        ("stop", "candidate"),
    ]
