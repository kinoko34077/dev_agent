from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from scripts.devfarm import write_manifest
from scripts.devfarm_commander import create_plan
from scripts.devfarm_orchestrator import DevFarmOrchestrator
from scripts.devfarm_self_repair import integrate_approved_repair
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.intelligence.self_improvement import (
    ObservationRecord,
    diagnose_observation,
    propose_improvement,
)
from src.dev_agent.intelligence.self_repair import (
    RepairEvidence,
    RepairExecutionRequest,
    RepairPolicy,
)
from src.dev_agent.providers.fake.provider import FakeProvider


class _WorkerProvider(FakeProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[json.dumps(self.output)],
        )


class _ApprovalStore:
    def __init__(self) -> None:
        self.consumed = False

    def has_approval(self, approval_id: str, **kwargs: object) -> bool:
        return approval_id == "repair-approval"

    def consume_approval(self, approval_id: str, **kwargs: object) -> bool:
        self.consumed = True
        return approval_id == "repair-approval"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repair-repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "repair-tests@example.invalid")
    _git(root, "config", "user.name", "Repair Tests")
    target = "tests/v2/repair_target.py"
    target_path = root / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    trusted = "tests/v2/repair_baseline.py"
    trusted_path = root / trusted
    trusted_path.write_text("def test_baseline():\n    assert True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "repair baseline")
    revision = _git(root, "rev-parse", "HEAD")
    manifest = write_manifest(
        root,
        {
            "task_id": "repair-worker-task",
            "objective": "Add one harmless statement to the focused test.",
            "base_revision": revision,
            "allowed_files": [target],
            "read_files": [target, trusted],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": [target],
            "requirements": [],
            "acceptance": ["focused test passes"],
            "test_commands": [f"python -m pytest {target} {trusted} -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    return root, revision, manifest.relative_to(root).as_posix()


def _patch() -> str:
    return (
        "diff --git a/tests/v2/repair_target.py b/tests/v2/repair_target.py\n"
        "--- a/tests/v2/repair_target.py\n"
        "+++ b/tests/v2/repair_target.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def test_target():\n"
        "     assert True\n"
        "+    return None\n"
    )


def test_approved_repair_adapter_uses_real_supervisor_host_integration(tmp_path: Path) -> None:
    root, revision, manifest_ref = _repository(tmp_path)
    create_plan(
        root,
        {
            "run_id": "repair-supervisor-run",
            "objective": "verify an explicitly approved repair candidate",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "repair-worker-task",
                    "owner": "worker",
                    "manifest_path": manifest_ref,
                    "ownership": ["tests/v2/repair_target.py"],
                    "assignment": {
                        "provider_id": "cloudflare",
                        "model_id": "@cf/meta/llama-3.1-8b-instruct",
                    },
                }
            ],
        },
    )
    runner = CodexSupervisedCommanderRun(root, "repair-supervisor-run")
    runner.create()
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": ["tests/v2/repair_target.py"],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(),
            "notes": "bounded repair candidate",
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    step = runner.run_until_intervention(
        providers={"repair-worker-task": provider},
        orchestrator=orchestrator,
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
        max_wait_seconds=30,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(
            AssertionError("verified worker must not sleep")
        ),
    )
    assert step.status == "REVIEWING"
    packet = runner.review_packet("repair-worker-task")
    task = runner.plan()["tasks"][0]
    patch_ref = next(item["path"] for item in packet["artifact_refs"] if item["kind"] == "patch")
    assert isinstance(packet["verification_ref"], str)
    verification_ref = packet["verification_ref"]
    assert isinstance(packet["patch_sha256"], str)
    assert isinstance(task["last_attempt_id"], str)

    observation = ObservationRecord(
        subject="verified repair candidate",
        source="host_verification",
        observed_at="2026-09-14T16:00:00+09:00",
        status="OBSERVED",
        metrics={"host_verified": 1},
        evidence_refs=({"kind": "verification", "path": verification_ref},),
        observation_id="observation-real-repair-1",
    )
    diagnosis = diagnose_observation(
        observation,
        category="reliability",
        severity="normal",
        confidence="high",
        causes=("bounded repair candidate was independently verified",),
        recommended_focus=("explicit approval-bound integration",),
        diagnosis_id="diagnosis-real-repair-1",
    )
    improvement_plan = propose_improvement(
        observations=(observation,),
        diagnoses=(diagnosis,),
        objective="apply one bounded verified repair",
        steps=("integrate the exact verified patch through the Host helper",),
        acceptance=("keep the checkout clean and preserve the rollback reference",),
        exclusions=("do not retry or rollback automatically",),
        risk="low",
        plan_id="improvement-plan-real-repair-1",
    )
    evidence = RepairEvidence(
        plan_id=improvement_plan.plan_id,
        base_revision=revision,
        attempt_id=task["last_attempt_id"],
        patch_ref=patch_ref,
        patch_sha256=packet["patch_sha256"],
        manifest_ref=manifest_ref,
        verification_ref=verification_ref,
        changed_files=tuple(packet["changed_files"]),
        verification_status="passed",
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
        independent_verification=True,
        external_outcome_known=True,
        rollback_ref="git:last-known-good",
    )
    candidate_result = RepairPolicy().evaluate(improvement_plan, evidence)
    assert candidate_result.candidate is not None

    decision_step = runner.record_review_decision(
        "repair-worker-task",
        attempt_id=task["last_attempt_id"],
        decision="APPROVE_INTEGRATION",
        evidence_refs=[{"kind": "verification", "path": verification_ref}],
    )
    assert decision_step.status == "INTEGRATING"
    decision_id = runner.plan()["review_decisions"][0]["decision_id"]
    target_checkout = root.resolve()
    request = RepairExecutionRequest(
        candidate_id=candidate_result.candidate.candidate_id,
        run_id="repair-supervisor-run",
        task_id="repair-worker-task",
        attempt_id=task["last_attempt_id"],
        base_revision=revision,
        patch_sha256=packet["patch_sha256"],
        manifest_ref=manifest_ref,
        verification_ref=verification_ref,
        rollback_ref="git:last-known-good",
        review_decision_id=decision_id,
        target_checkout_ref=str(target_checkout),
        target_ref="HEAD",
        commit_message="integrate approved repair",
        approval_id="repair-approval",
        call_id="repair-call",
    )
    approval_store = _ApprovalStore()

    result = integrate_approved_repair(
        runner,
        candidate_result.candidate,
        request,
        approval_store=approval_store,
        target_checkout=target_checkout,
    )

    assert result.plan_status == "INTEGRATED"
    assert approval_store.consumed is True
    assert "return None" in (root / "tests/v2/repair_target.py").read_text(encoding="utf-8")
    assert runner.plan()["tasks"][0]["status"] == "INTEGRATED"
    assert _git(root, "status", "--short", "--", ".", ":(exclude).devfarm") == ""
    assert hashlib.sha256(_patch().encode("utf-8")).hexdigest() == packet["patch_sha256"]
