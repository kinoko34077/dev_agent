from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.devfarm_self_improvement as self_improvement_cli


class _FakeSupervisor:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id

    def plan(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": "ACTIVE",
            "plan_revision": 3,
            "tasks": [
                {
                    "task_id": "task-1",
                    "status": "HOST_VERIFIED",
                    "owner": "worker",
                    "attempt_count": 1,
                    "last_attempt_id": "attempt-1",
                    "result_ref": ".devfarm/results/task-1/result.json",
                    "manifest_path": ".devfarm/tasks/task-1.json",
                }
            ],
            "supervisor": {
                "metrics": {
                    "worker_dispatch_count": 1,
                    "worker_success_count": 1,
                    "codex_review_request_count": 1,
                }
            },
        }

    def review_packet(self, task_id: str) -> dict[str, object]:
        assert task_id == "task-1"
        return {
            "task_id": task_id,
            "attempt_id": "attempt-1",
            "status": "HOST_VERIFIED",
            "result_ref": ".devfarm/results/task-1/result.json",
            "verification_ref": ".devfarm/results/task-1/verification.json",
            "changed_files": ["src/example.py"],
            "verification_summary": {
                "host_verified": True,
                "host_tests_passed": True,
            },
        }


def test_observe_cli_composes_public_supervisor_packet_without_raw_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(self_improvement_cli, "CodexSupervisedCommanderRun", _FakeSupervisor)
    output = Path(".devfarm/self-improvement/observations/observation.json")

    assert (
        self_improvement_cli.main(
            [
                "observe",
                "run-1",
                "task-1",
                "--root",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    record = json.loads((tmp_path / output).read_text(encoding="utf-8"))
    assert record["status"] == "OBSERVED"
    assert record["metrics"]["host_verified"] is True
    assert record["evidence_refs"]
    assert "stdout" not in json.dumps(record)
    assert "conversation" not in json.dumps(record)


def test_observe_cli_rejects_output_outside_bounded_artifact_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(self_improvement_cli, "CodexSupervisedCommanderRun", _FakeSupervisor)

    assert (
        self_improvement_cli.main(
            [
                "observe",
                "run-1",
                "task-1",
                "--root",
                str(tmp_path),
                "--output",
                "outside.json",
            ]
        )
        == 2
    )


def test_diagnose_and_plan_cli_preserve_grounded_proposal_only_chain(tmp_path: Path) -> None:
    base = tmp_path / ".devfarm" / "self-improvement"
    base.mkdir(parents=True)
    observation = {
        "observation_id": "observation-1",
        "subject": "worker verification",
        "source": "host_verification",
        "observed_at": "2026-09-14T12:00:00+00:00",
        "status": "DEGRADED",
        "metrics": {"attempts": 2},
        "evidence_refs": [{"kind": "verification", "path": ".devfarm/results/task/verification.json"}],
    }
    observation_path = base / "observation.json"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    diagnosis_path = Path(".devfarm/self-improvement/diagnosis.json")
    plan_path = Path(".devfarm/self-improvement/plan.json")

    assert (
        self_improvement_cli.main(
            [
                "diagnose",
                str(observation_path),
                "--root",
                str(tmp_path),
                "--category",
                "reliability",
                "--severity",
                "normal",
                "--confidence",
                "medium",
                "--cause",
                "bounded verification failed",
                "--focus",
                "worker contract",
                "--output",
                str(diagnosis_path),
            ]
        )
        == 0
    )
    diagnosis = json.loads((tmp_path / diagnosis_path).read_text(encoding="utf-8"))
    assert diagnosis["status"] == "PROPOSAL_ONLY"
    assert diagnosis["observation_id"] == "observation-1"

    assert (
        self_improvement_cli.main(
            [
                "plan",
                str(observation_path),
                str(tmp_path / diagnosis_path),
                "--root",
                str(tmp_path),
                "--objective",
                "reduce verification failures",
                "--step",
                "add a bounded contract check",
                "--acceptance",
                "Host Verification remains fail-closed",
                "--exclusion",
                "do not relax protected path validation",
                "--risk",
                "normal",
                "--output",
                str(plan_path),
            ]
        )
        == 0
    )
    plan = json.loads((tmp_path / plan_path).read_text(encoding="utf-8"))
    assert plan["status"] == "PROPOSAL_ONLY"
    assert plan["requires_human_approval"] is True
    assert "dispatch" not in plan
    assert "integration" not in plan


def test_cli_artifact_output_is_append_only(tmp_path: Path) -> None:
    output = tmp_path / ".devfarm" / "self-improvement" / "record.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")

    with pytest.raises(Exception, match="immutable"):
        self_improvement_cli._write_record_artifact(tmp_path, output, {"value": 1})
