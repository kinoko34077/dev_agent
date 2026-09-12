from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts.devfarm import write_manifest
from scripts.handoff_cycle import (
    CodexDevFarmExecutor,
    HandoffCycleError,
    OneCycleDevelopmentLoop,
)
from src.dev_agent.backends.protocol import (
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendSession,
    AgentBackendStatus,
)
from src.dev_agent.handoff import HandoffEnvelope, HandoffKind, HandoffRole


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class _Planner:
    def plan(self, request: HandoffEnvelope) -> HandoffEnvelope:
        assert request.source_role == HandoffRole.HUMAN.value
        assert request.target_role == HandoffRole.PLANNER.value
        return HandoffEnvelope(
            kind=HandoffKind.IMPLEMENTATION_INSTRUCTION.value,
            subject=request.subject,
            instruction="execute the narrow manifest task",
            source_role=HandoffRole.PLANNER.value,
            target_role=HandoffRole.EXECUTOR.value,
            payload={"manifest_path": request.payload["manifest_path"]},
        )


class _Reviewer:
    def review(self, request: HandoffEnvelope) -> HandoffEnvelope:
        assert request.source_role == HandoffRole.EXECUTOR.value
        return HandoffEnvelope(
            kind=HandoffKind.ROADMAP_COMPARISON.value,
            subject="review result",
            instruction="return to human authority; do not start another cycle",
            source_role=HandoffRole.REVIEWER.value,
            target_role=HandoffRole.HUMAN.value,
            payload={"execution": request.payload},
        )


class _EditingBackend:
    identity = AgentBackendIdentity(backend_id="codex-exec", backend_version="cycle-test")

    def __init__(self) -> None:
        self._result = AgentBackendResult(session_id="cycle-session", status=AgentBackendStatus.COMPLETED)

    def start(self, request: AgentBackendRequest) -> AgentBackendSession:
        target = Path(request.scope.workspace_id) / "tests" / "target.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# cycle edit\n", encoding="utf-8")
        return AgentBackendSession(
            session_id="cycle-session",
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


def _fixture(tmp_path: Path) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", cwd=root)
    _git("config", "user.email", "cycle@example.invalid", cwd=root)
    _git("config", "user.name", "Cycle Test", cwd=root)
    target = root / "tests" / "target.py"
    target.parent.mkdir()
    target.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    (root / ".gitignore").write_text(".devfarm/\n", encoding="utf-8")
    _git("add", ".gitignore", "tests/target.py", cwd=root)
    _git("commit", "-m", "baseline", cwd=root)
    revision = _git("rev-parse", "HEAD", cwd=root)
    manifest = write_manifest(
        root,
        {
            "task_id": "handoff-cycle-001",
            "objective": "make the narrow fixture change",
            "base_revision": revision,
            "allowed_files": ["tests/target.py"],
            "read_files": ["tests/target.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["codex-exec"],
            "outbound_files": ["tests/target.py"],
            "requirements": ["change only the target fixture"],
            "acceptance": ["Host can inspect the diff"],
            "test_commands": ["python -m pytest tests/target.py -q"],
            "max_attempts": 1,
            "output_contract": {"files": ["result.json", "patch.diff"]},
        },
    )
    return root, revision, manifest


def test_one_cycle_stops_at_human_boundary_and_preserves_references(tmp_path: Path):
    root, revision, manifest = _fixture(tmp_path)
    executor = CodexDevFarmExecutor(root, backend=_EditingBackend())
    loop = OneCycleDevelopmentLoop(_Planner(), executor, _Reviewer())

    result = loop.run(
        objective="narrow fixture dogfood",
        instruction="plan and execute one bounded change",
        repository_reference={"repository": "fixture", "branch": "test", "commit": revision},
        payload={"manifest_path": str(manifest.relative_to(root)).replace("\\", "/")},
    )

    assert result.cycle_count == 1
    assert result.stopped is True
    assert result.reviewer_handoff.target_role == "human"
    assert result.executor_handoff.payload["authority_status"] == "ACCEPTED"
    assert result.executor_handoff.payload["result_accepted"] is False
    assert _git("status", "--porcelain", cwd=root) == ""


def test_cycle_rejects_role_that_attempts_to_continue_automatically():
    class _BadReviewer(_Reviewer):
        def review(self, request: HandoffEnvelope) -> HandoffEnvelope:
            return HandoffEnvelope(
                kind=HandoffKind.IMPLEMENTATION_INSTRUCTION.value,
                subject="bad",
                instruction="continue",
                source_role=HandoffRole.REVIEWER.value,
                target_role=HandoffRole.EXECUTOR.value,
            )

    class _NoopExecutor:
        def execute(self, request: HandoffEnvelope) -> HandoffEnvelope:
            return HandoffEnvelope(
                kind=HandoffKind.EXECUTION_RESULT.value,
                subject="done",
                instruction="review",
                source_role=HandoffRole.EXECUTOR.value,
                target_role=HandoffRole.REVIEWER.value,
            )

    with pytest.raises(HandoffCycleError, match="unexpected handoff roles"):
        OneCycleDevelopmentLoop(_Planner(), _NoopExecutor(), _BadReviewer()).run(
            objective="objective",
            instruction="instruction",
            payload={"manifest_path": "not-used"},
        )
