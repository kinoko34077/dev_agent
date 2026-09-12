from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.devfarm import DevFarmError, write_manifest
from scripts.devfarm_codex import run_codex_attempt
from src.dev_agent.backends.protocol import (
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendSession,
    AgentBackendStatus,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _manifest(root: Path, revision: str, *, allowed_files: list[str]) -> Path:
    return write_manifest(
        root,
        {
            "task_id": "codex-attempt-001",
            "objective": "make the narrow test fixture change",
            "base_revision": revision,
            "allowed_files": allowed_files,
            "read_files": ["tests/v2/test_target.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["codex-exec"],
            "outbound_files": ["tests/v2/test_target.py"],
            "requirements": ["change only the declared fixture files"],
            "acceptance": ["the host can inspect the complete diff"],
            "test_commands": ["python -m pytest tests/v2/test_target.py -q"],
            "max_attempts": 2,
            "output_contract": {"files": ["result.json", "patch.diff"]},
        },
    )


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", cwd=root)
    _git("config", "user.email", "codex-tests@example.invalid", cwd=root)
    _git("config", "user.name", "Codex Tests", cwd=root)
    target = root / "tests/v2/test_target.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    (root / ".gitignore").write_text(".devfarm/\n", encoding="utf-8")
    _git("add", ".gitignore", "tests/v2/test_target.py", cwd=root)
    _git("commit", "-m", "baseline", cwd=root)
    return root, _git("rev-parse", "HEAD", cwd=root).stdout.strip()


class _EditingBackend:
    identity = AgentBackendIdentity(backend_id="codex-exec", backend_version="test")

    def __init__(self, *, out_of_scope: bool = False) -> None:
        self.out_of_scope = out_of_scope
        self._session = "session-001"
        self._result = AgentBackendResult(session_id=self._session, status=AgentBackendStatus.COMPLETED)

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        workspace = Path(request.scope.workspace_id)
        target = workspace / "tests/v2/test_target.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# Codex edit\n", encoding="utf-8")
        new_file = workspace / ("README.md" if self.out_of_scope else "tests/v2/new_file.py")
        new_file.write_text("def generated_fixture():\n    return True\n", encoding="utf-8")
        return AgentBackendSession(
            session_id=self._session,
            task_id=request.task_id,
            backend_id=self.identity.backend_id,
            status=AgentBackendStatus.RUNNING,
        )

    def events(self, session_id: str):
        return [AgentBackendEvent(session_id=session_id, sequence=1, event_type="completed", status=AgentBackendStatus.COMPLETED)]

    def cancel(self, session_id: str) -> None:
        self._result = AgentBackendResult(session_id=session_id, status=AgentBackendStatus.CANCELLED)

    def result(self, session_id: str) -> AgentBackendResult:
        return self._result


def test_codex_attempt_uses_isolated_worktree_and_detects_untracked_changes(tmp_path: Path):
    root, revision = _repo(tmp_path)
    manifest_path = _manifest(root, revision, allowed_files=["tests/v2/test_target.py", "tests/v2/new_file.py"])

    attempt = run_codex_attempt(root, manifest_path, backend=_EditingBackend())

    assert attempt["authority_status"] == "ACCEPTED"
    assert attempt["changed_files"] == ["tests/v2/new_file.py", "tests/v2/test_target.py"]
    assert "tests/v2/new_file.py" in attempt["patch"]
    assert attempt["patch_sha256"]
    assert attempt["verification_trust_level"] == "STATIC_ONLY"
    assert attempt["result"]["tests_passed"] is False
    assert _git("status", "--porcelain", cwd=root).stdout == ""

    worktree = Path(attempt["workspace"])
    status = _git("status", "--porcelain", cwd=worktree).stdout
    assert "?? tests/v2/new_file.py" in status
    assert " M tests/v2/test_target.py" in status
    artifact = root / ".devfarm" / "results" / "codex-attempt-001" / "attempts" / attempt["attempt_id"] / "patch.diff"
    assert artifact.read_text(encoding="utf-8") == attempt["patch"]


def test_codex_attempt_rejects_out_of_scope_change_before_verification(tmp_path: Path):
    root, revision = _repo(tmp_path)
    manifest_path = _manifest(root, revision, allowed_files=["tests/v2/test_target.py"])

    attempt = run_codex_attempt(root, manifest_path, backend=_EditingBackend(out_of_scope=True))

    assert attempt["authority_status"] == "REJECTED"
    assert attempt["result"]["status"] == "failed"
    assert "outside manifest allowed_files" in attempt["result"]["known_issues"][0]
    assert attempt["host_verification_started"] is False


def test_codex_attempt_trusted_host_exec_requires_attempt_approval(tmp_path: Path):
    root, revision = _repo(tmp_path)
    manifest_path = _manifest(root, revision, allowed_files=["tests/v2/test_target.py", "tests/v2/new_file.py"])

    with pytest.raises(DevFarmError, match="explicit operator approval"):
        run_codex_attempt(
            root,
            manifest_path,
            backend=_EditingBackend(),
            trust_level="TRUSTED_HOST_EXEC",
        )


def test_codex_attempt_trusted_host_exec_records_host_evidence_after_approval(tmp_path: Path):
    root, revision = _repo(tmp_path)
    manifest_path = _manifest(root, revision, allowed_files=["tests/v2/test_target.py", "tests/v2/new_file.py"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_commands"] = ["python -m pytest tests/v2/test_target.py -q"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    attempt = run_codex_attempt(
        root,
        manifest_path,
        backend=_EditingBackend(),
        trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )

    assert attempt["host_verification_started"] is True
    assert attempt["result"]["worker_metrics"]["host_verified"] is True
    assert attempt["result"]["worker_metrics"]["result_accepted"] is False
    assert attempt["result"]["host_verified_tests"][0]["passed"] is True
