from __future__ import annotations

import subprocess

import pytest

from src.dev_agent.coordination.runtime_release import (
    RevisionPinnedRuntimeStore,
    RuntimeReleaseError,
)


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
    (root / "agent.py").write_text("REVISION = 'one'\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "runtime baseline")
    return root, _git(root, "rev-parse", "HEAD")


def test_materialize_pins_a_clean_release_to_the_exact_commit(tmp_path):
    source, revision = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")

    release = store.materialize("HEAD")
    again = store.materialize(revision)

    assert release.revision == revision
    assert release.runtime_root == tmp_path / "releases" / revision
    assert release.runtime_root.joinpath("agent.py").read_text(encoding="utf-8") == "REVISION = 'one'\n"
    assert again == release
    assert _git(release.runtime_root, "status", "--porcelain") == ""


def test_materialize_reuses_only_an_unchanged_release_and_never_follows_source_changes(tmp_path):
    source, revision = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    release = store.materialize(revision)

    (source / "agent.py").write_text("REVISION = 'mutable checkout'\n", encoding="utf-8")
    reused = store.materialize(revision)

    assert reused == release
    assert release.runtime_root.joinpath("agent.py").read_text(encoding="utf-8") == "REVISION = 'one'\n"


def test_create_rollback_proof_materializes_clean_release_without_starting_runtime(tmp_path):
    source, revision = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")

    proof = store.create_rollback_proof(
        revision,
        health_status="passed",
        verified_at="2026-09-16T12:00:00+00:00",
    )

    assert proof.revision == revision
    assert proof.release_materialized is True
    assert proof.release_clean is True
    assert proof.release_ref == f".devfarm/runtime-releases/{revision}"
    assert (tmp_path / "releases" / revision / "agent.py").exists()


def test_materialize_rejects_a_modified_release_without_overwriting_it(tmp_path):
    source, revision = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")
    release = store.materialize(revision)
    release.runtime_root.joinpath("agent.py").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RuntimeReleaseError, match="release worktree is not clean"):
        store.materialize(revision)
    assert release.runtime_root.joinpath("agent.py").read_text(encoding="utf-8") == "tampered\n"


def test_materialize_rejects_missing_or_partially_created_release(tmp_path):
    source, revision = _repository(tmp_path)
    store = RevisionPinnedRuntimeStore(source, tmp_path / "releases")

    with pytest.raises(RuntimeReleaseError, match="could not resolve revision"):
        store.materialize("does-not-exist")

    partial = tmp_path / "releases" / revision
    partial.mkdir(parents=True)
    partial.joinpath("unexpected.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(RuntimeReleaseError, match="already exists"):
        store.materialize(revision)


def test_release_store_rejects_a_release_root_inside_the_mutable_checkout(tmp_path):
    source, _revision = _repository(tmp_path)

    with pytest.raises(RuntimeReleaseError, match="outside source_root"):
        RevisionPinnedRuntimeStore(source, source / ".runtime" / "releases")
