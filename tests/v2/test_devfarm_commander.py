import hashlib
import json
import subprocess
from datetime import datetime, timezone

import pytest

from scripts.devfarm import DevFarmError, write_manifest
from scripts.devfarm import main as devfarm_main
from scripts.devfarm_commander import (
    CommanderPlanStore,
    PlanConflictError,
    collect_plan,
    create_plan,
    dispatch_plan,
    mark_integrated,
    recover_orphaned_dispatches,
    reassign_task,
    resume_plan,
    verify_plan,
)
from scripts.devfarm_orchestrator import DevFarmOrchestrator, HostConcurrencyGovernor, RemoteConcurrencyGovernor
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.fake.provider import FakeProvider


class _WorkerProvider(FakeProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"
    intelligence_tier = "L1"

    def __init__(self, output):
        self.output = output

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[json.dumps(self.output)],
        )


class _FailingVerifier:
    def verify(self, root, manifest_paths):
        raise RuntimeError("verification boundary unavailable")


class _OneProposalExplodes(DevFarmOrchestrator):
    def _propose(self, root, assignment):
        if assignment.manifest_path.stem == "worker-a":
            raise RuntimeError("unexpected worker exception")
        return super()._propose(root, assignment)


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _patch(path):
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,3 @@\n"
        " def test_target():\n"
        "     assert True\n"
        "+    return None\n"
    )


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "commander-tests@example.invalid")
    _git(root, "config", "user.name", "Commander Tests")
    targets = ["tests/v2/worker_a.py", "tests/v2/worker_b.py"]
    for target in targets:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    baseline = root / "tests/v2/baseline.py"
    baseline.write_text("def test_baseline():\n    assert True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "commander baseline")
    return root, targets, _git(root, "rev-parse", "HEAD").stdout.strip()


def _manifest(root, revision, task_id, target):
    return write_manifest(
        root,
        {
            "task_id": task_id,
            "objective": "Add a harmless return to the focused test.",
            "base_revision": revision,
            "allowed_files": [target],
            "read_files": [target],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": [target],
            "requirements": [],
            "acceptance": ["focused test passes"],
            "test_commands": [f"python -m pytest {target} tests/v2/baseline.py -q"],
            "max_attempts": 2,
            "output_contract": {},
        },
    )


def _integrate_worker_patch(root, task_id, task):
    attempt_id = task["last_attempt_id"]
    patch_path = root / ".devfarm" / "results" / task_id / "attempts" / attempt_id / "patch.diff"
    patch = patch_path.read_text(encoding="utf-8")
    subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "apply", "--whitespace=error", "-"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "add", "--all")
    _git(root, "commit", "-m", f"integrate {task_id}")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    return revision, attempt_id, digest


def _integrate_codex_review(root):
    review = root / "docs" / "commander-review.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# Commander review\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "integrate Codex review")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    diff = _git(root, "diff-tree", "--root", "--binary", "--no-commit-id", "-r", revision, "--").stdout
    return revision, hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _plan(root, revision, targets):
    manifests = [_manifest(root, revision, f"worker-{letter}", target) for letter, target in zip(("a", "b"), targets)]
    return create_plan(
        root,
        {
            "run_id": "commander-run-001",
            "objective": "Run two independent bounded worker tasks, then review them.",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "worker-b",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-b.json",
                    "ownership": [targets[1]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "codex-review",
                    "owner": "codex",
                    "dependencies": ["worker-a", "worker-b"],
                    "ownership": ["docs/commander-review.md"],
                },
            ],
        },
    )


def test_commander_plan_dispatch_verify_resume_and_integrate(tmp_path):
    root, targets, revision = _repo(tmp_path)
    plan = _plan(root, revision, targets)
    assert plan["status"] == "READY"
    assert {task["status"] for task in plan["tasks"][:2]} == {"READY"}
    assert plan["tasks"][2]["status"] == "PLANNED"

    providers = {
        "worker-a": _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"}),
        "worker-b": _WorkerProvider({"status": "completed", "changed_files": [targets[1]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[1]), "notes": "ready"}),
    }
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    proposed = dispatch_plan(root, "commander-run-001", providers=providers, orchestrator=orchestrator)
    assert {task["status"] for task in proposed["tasks"][:2]} == {"PROPOSED"}
    for task in proposed["tasks"][:2]:
        assert task["dispatch_id"]
        assert datetime.fromisoformat(task["dispatch_started_at"]).tzinfo is not None
        assert datetime.fromisoformat(task["dispatch_deadline_at"]).tzinfo is not None
        assert task["dispatch_owner_pid"] > 0
    assert not (root / ".devfarm/worktrees/worker-a").exists()
    assert not (root / ".devfarm/worktrees/worker-b").exists()

    verified = verify_plan(root, "commander-run-001", orchestrator=orchestrator)
    assert {task["status"] for task in verified["tasks"][:2]} == {"HOST_VERIFIED"}
    resumed = resume_plan(root, "commander-run-001")
    assert resumed["tasks"][2]["status"] == "PLANNED"
    assert len(resumed["results"]) >= 4

    a_revision, a_attempt, a_digest = _integrate_worker_patch(root, "worker-a", verified["tasks"][0])
    mark_integrated(
        root,
        "commander-run-001",
        "worker-a",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=a_revision,
        source_attempt_id=a_attempt,
        verified_patch_digest=a_digest,
    )
    still_waiting = CommanderPlanStore(root).load("commander-run-001")
    assert still_waiting["tasks"][2]["status"] == "PLANNED"
    b_revision, b_attempt, b_digest = _integrate_worker_patch(root, "worker-b", still_waiting["tasks"][1])
    mark_integrated(
        root,
        "commander-run-001",
        "worker-b",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=b_revision,
        source_attempt_id=b_attempt,
        verified_patch_digest=b_digest,
    )
    released = CommanderPlanStore(root).load("commander-run-001")
    assert released["tasks"][2]["status"] == "READY"
    c_revision, c_digest = _integrate_codex_review(root)
    mark_integrated(
        root,
        "commander-run-001",
        "codex-review",
        note="Codex completed the integration review",
        target_ref="HEAD",
        integration_revision=c_revision,
        source_attempt_id="codex-review",
        verified_patch_digest=c_digest,
    )
    final = CommanderPlanStore(root).load("commander-run-001")
    assert final["tasks"][0]["status"] == "INTEGRATED"
    assert final["tasks"][2]["status"] == "INTEGRATED"


def test_supervisor_approved_integration_applies_patch_and_records_git_proof(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-integration-run",
            "objective": "integrate one explicitly approved worker patch",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
            "notes": "ready",
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    dispatch_plan(root, "supervisor-integration-run", providers={"worker-a": provider}, orchestrator=orchestrator)
    verify_plan(root, "supervisor-integration-run", orchestrator=orchestrator)
    runner = CodexSupervisedCommanderRun(root, "supervisor-integration-run")
    runner.create()
    review_step = runner.advance(providers={})
    assert review_step.status == "REVIEWING"
    assert review_step.metrics["codex_review_request_count"] == 1
    assert review_step.metrics["codex_review_count"] == 0
    assert len(review_step.review_packets) == 1
    assert "patch" not in review_step.review_packets[0]
    task = runner.plan()["tasks"][0]
    with pytest.raises(DevFarmError, match="approval"):
        runner.integrate_approved_worker(
            "worker-a",
            decision_id="missing-review",
            commit_message="integrate worker patch",
            target_checkout=root,
            target_ref="HEAD",
        )

    decision_step = runner.record_review_decision(
        "worker-a",
        attempt_id=task["last_attempt_id"],
        decision="APPROVE_INTEGRATION",
        evidence_refs=[{"kind": "verification", "path": task["result_ref"]}],
    )
    decision_id = runner.plan()["review_decisions"][0]["decision_id"]
    integrated = runner.integrate_approved_worker(
        "worker-a",
        decision_id=decision_id,
        commit_message="integrate worker patch",
        target_checkout=root,
        target_ref="HEAD",
    )

    assert decision_step.status == "INTEGRATING"
    assert integrated.plan_status == "INTEGRATED"
    integrated_plan = runner.plan()
    integrated_task = integrated_plan["tasks"][0]
    assert integrated_task["status"] == "INTEGRATED"
    assert integrated_task["integration_revision"] == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert "return None" in (root / targets[0]).read_text(encoding="utf-8")


def test_supervisor_rework_decision_creates_next_attempt_manifest(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-rework-run",
            "objective": "rework one verified worker proposal",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    dispatch_plan(root, "supervisor-rework-run", providers={"worker-a": provider}, orchestrator=orchestrator)
    verify_plan(root, "supervisor-rework-run", orchestrator=orchestrator)
    runner = CodexSupervisedCommanderRun(root, "supervisor-rework-run")
    runner.create()
    runner.advance(providers={})
    task = runner.plan()["tasks"][0]
    attempt_id = task["last_attempt_id"]
    runner.record_review_decision(
        "worker-a",
        attempt_id=attempt_id,
        decision="REWORK",
        required_correction="Address the review finding before another verification.",
        evidence_refs=[{"kind": "verification", "path": task["result_ref"]}],
    )
    assert runner.plan()["tasks"][0]["status"] == "REJECTED"
    assert runner.plan()["results"][-1]["stage"] == "review"
    assert collect_plan(root, "supervisor-rework-run")["tasks"][0]["status"] == "REJECTED"

    handoff = runner.rework_handoff(
        "worker-a",
        failure_evidence_reference={"type": "result", "path": task["result_ref"]},
        review_findings_reference={"type": "review", "attempt_id": attempt_id},
        required_correction="Address the review finding before another verification.",
    )
    reassigned = runner.reassign(
        "worker-a",
        provider_id="cloudflare",
        model_id="worker-model",
        rework_handoff=handoff,
    )
    new_task = reassigned and runner.plan()["tasks"][0]
    assert new_task["status"] == "READY"
    assert new_task.get("last_attempt_id") is None
    # The previous latest projection remains on disk, but cannot be collected
    # as the new attempt before the new manifest is dispatched.
    assert runner.plan()["tasks"][0]["manifest_path"] != ".devfarm/tasks/worker-a.json"
    assert collect_plan(root, "supervisor-rework-run")["tasks"][0]["status"] == "READY"
    new_manifest = json.loads((root / new_task["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["rework_handoff"]["kind"] == "repair_request"
    dispatch_plan(root, "supervisor-rework-run", providers={"worker-a": provider}, orchestrator=orchestrator)
    assert runner.plan()["tasks"][0]["status"] == "PROPOSED"


def test_commander_keeps_sibling_proposal_when_one_worker_raises(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _plan(root, revision, targets)
    providers = {
        "worker-a": _WorkerProvider(
            {
                "status": "completed",
                "changed_files": [targets[0]],
                "tests_run": [],
                "tests_passed": True,
                "known_issues": [],
                "assumptions": [],
                "patch": _patch(targets[0]),
            }
        ),
        "worker-b": _WorkerProvider(
            {
                "status": "completed",
                "changed_files": [targets[1]],
                "tests_run": [],
                "tests_passed": True,
                "known_issues": [],
                "assumptions": [],
                "patch": _patch(targets[1]),
            }
        ),
    }

    dispatched = dispatch_plan(
        root,
        "commander-run-001",
        providers=providers,
        orchestrator=_OneProposalExplodes(),
    )

    by_id = {task["task_id"]: task for task in dispatched["tasks"]}
    assert by_id["worker-a"]["status"] == "REJECTED"
    assert by_id["worker-a"]["block_reason"] == "proposal_failed"
    assert by_id["worker-b"]["status"] == "PROPOSED"
    assert any(
        item["task_id"] == "worker-b" and item["status"] == "completed"
        for item in dispatched["results"]
    )


def test_commander_rejects_overlapping_ownership(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    with pytest.raises(DevFarmError, match="ownership"):
        create_plan(
            root,
            {
                "run_id": "overlap-run",
                "objective": "reject overlap",
                "base_revision": revision,
                "tasks": [
                    {"task_id": "a", "owner": "codex", "ownership": ["src/shared.py"]},
                    {"task_id": "b", "owner": "codex", "ownership": ["src/shared.py"]},
                ],
            },
        )


def test_commander_reassigns_a_failed_worker_within_attempt_limit(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "reassign-run",
            "objective": "bounded reassignment",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "status": "BLOCKED",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    assert plan["status"] == "BLOCKED"
    reassigned = reassign_task(
        root,
        "reassign-run",
        "worker-a",
        provider_id="openrouter",
        model_id="openrouter/free",
    )
    assert reassigned["tasks"][0]["status"] == "READY"
    assert reassigned["tasks"][0]["assignment"]["provider_id"] == "openrouter"


def test_commander_reassign_with_rework_creates_immutable_manifest_revision(tmp_path):
    root, targets, revision = _repo(tmp_path)
    original = _manifest(root, revision, "worker-a", targets[0]).relative_to(root).as_posix()
    plan = create_plan(
        root,
        {
            "run_id": "rework-manifest-run",
            "objective": "reassign with review correction",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "status": "REJECTED",
                    "manifest_path": original,
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )

    reworked = reassign_task(
        root,
        "rework-manifest-run",
        "worker-a",
        provider_id="openrouter",
        model_id="openrouter/free",
        rework_handoff={
            "kind": "repair_request",
            "requirements": ["Fix the failed assertion."],
            "payload_reference": {"type": "result", "path": ".devfarm/results/worker-a/result.json"},
        },
    )

    task = reworked["tasks"][0]
    assert task["status"] == "READY"
    assert task["manifest_path"] != original
    assert original in task["manifest_history"]
    assert (root / original).is_file()
    new_manifest = json.loads((root / task["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["base_revision"] == revision
    assert new_manifest["rework_handoff"]["kind"] == "repair_request"
    assert reworked["plan_revision"] == plan["plan_revision"] + 1


def test_commander_persists_host_verification_boundary_failure(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "verification-failure-run",
            "objective": "persist verifier failure",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    dispatch_plan(
        root,
        "verification-failure-run",
        providers={"worker-a": _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"})},
    )

    result = verify_plan(root, "verification-failure-run", orchestrator=_FailingVerifier())

    assert result["tasks"][0]["status"] == "REJECTED"
    assert result["tasks"][0]["block_reason"] == "host_verification_failed"
    assert "verification boundary unavailable" in result["tasks"][0]["last_error"]
    assert any(item["stage"] == "host_verification" and item["status"] == "failed" for item in result["results"])

    recovered = verify_plan(root, "verification-failure-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    assert recovered["tasks"][0]["status"] == "HOST_VERIFIED"
    assert "block_reason" not in recovered["tasks"][0]
    assert "last_error" not in recovered["tasks"][0]


def test_commander_clears_stale_verification_error_after_reassigned_success(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "verification-retry-clean-run",
            "objective": "clear stale verification state after a successful retry",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
            "notes": "ready",
        }
    )
    dispatch_plan(root, "verification-retry-clean-run", providers={"worker-a": provider})
    failed = verify_plan(root, "verification-retry-clean-run", orchestrator=_FailingVerifier())
    assert failed["tasks"][0]["status"] == "REJECTED"

    reassign_task(
        root,
        "verification-retry-clean-run",
        "worker-a",
        provider_id="cloudflare",
        model_id="@cf/meta/llama-3.1-8b-instruct",
    )
    dispatch_plan(root, "verification-retry-clean-run", providers={"worker-a": provider})
    verified = verify_plan(root, "verification-retry-clean-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)

    task = verified["tasks"][0]
    assert task["status"] == "HOST_VERIFIED"
    assert "block_reason" not in task
    assert "last_error" not in task


def test_commander_rejects_dependency_cycles_and_cli_can_read_status(tmp_path, capsys):
    root, _targets, revision = _repo(tmp_path)
    with pytest.raises(DevFarmError, match="cycle"):
        create_plan(
            root,
            {
                "run_id": "cycle-run",
                "objective": "reject cycle",
                "base_revision": revision,
                "tasks": [
                    {"task_id": "a", "owner": "codex", "dependencies": ["b"], "ownership": ["docs/a.md"]},
                    {"task_id": "b", "owner": "codex", "dependencies": ["a"], "ownership": ["docs/b.md"]},
                ],
            },
        )
    create_plan(
        root,
        {
            "run_id": "cli-run",
            "objective": "show status",
            "base_revision": revision,
            "tasks": [{"task_id": "codex-task", "owner": "codex", "ownership": ["docs/cli.md"]}],
        },
    )
    assert devfarm_main(["status", "cli-run", "--root", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "cli-run"
    assert output["tasks"][0]["status"] == "READY"


def test_commander_plan_save_uses_revision_cas(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    store = CommanderPlanStore(root)
    plan = create_plan(
        root,
        {
            "run_id": "cas-run",
            "objective": "prevent lost plan updates",
            "base_revision": revision,
            "tasks": [{"task_id": "task", "owner": "codex", "ownership": ["docs/task.md"]}],
        },
    )
    first = store.load("cas-run")
    second = store.load("cas-run")
    first["tasks"][0]["integration_note"] = "first update"
    saved = store.save(first)
    assert saved["plan_revision"] == plan["plan_revision"] + 1
    second["tasks"][0]["integration_note"] = "stale update"
    with pytest.raises(PlanConflictError, match="revision conflict"):
        store.save(second)
    assert store.load("cas-run")["tasks"][0]["integration_note"] == "first update"


def test_commander_recovers_expired_dispatched_task_without_blind_retry(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "orphaned-dispatch-run",
            "objective": "recover a lost worker process",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    task = plan["tasks"][0]
    task.update(
        {
            "status": "DISPATCHED",
            "attempt_count": 1,
            "dispatch_id": "dispatch-001",
            "dispatch_started_at": "2026-01-01T00:00:00+00:00",
            "dispatch_deadline_at": "2026-01-01T00:01:00+00:00",
            "dispatch_owner_pid": 999999,
        }
    )
    CommanderPlanStore(root).save(plan, expected_revision=plan["plan_revision"])

    recovered = recover_orphaned_dispatches(
        root,
        "orphaned-dispatch-run",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
    )

    recovered_task = recovered["tasks"][0]
    assert recovered_task["status"] == "BLOCKED"
    assert recovered_task["block_reason"] == "orphaned_dispatch"
    assert recovered_task["dispatch_recovery"] == "reconciliation_required"
    assert recovered_task["attempt_count"] == 1
    assert recovered["results"][-1]["stage"] == "dispatch_recovery"
    with pytest.raises(DevFarmError, match="reconciliation"):
        reassign_task(
            root,
            "orphaned-dispatch-run",
            "worker-a",
            provider_id="cloudflare",
            model_id="worker-model",
        )


def test_commander_keeps_dispatched_task_waiting_before_deadline(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "live-dispatch-run",
            "objective": "keep a live dispatch waiting",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    task = plan["tasks"][0]
    task.update(
        {
            "status": "DISPATCHED",
            "attempt_count": 1,
            "dispatch_id": "dispatch-002",
            "dispatch_started_at": "2026-01-01T00:00:00+00:00",
            "dispatch_deadline_at": "2026-01-01T00:10:00+00:00",
            "dispatch_owner_pid": 999999,
        }
    )
    CommanderPlanStore(root).save(plan, expected_revision=plan["plan_revision"])

    waiting = recover_orphaned_dispatches(
        root,
        "live-dispatch-run",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
    )

    assert waiting["tasks"][0]["status"] == "DISPATCHED"
    assert waiting["tasks"][0]["attempt_count"] == 1


def test_commander_integration_requires_git_evidence_and_records_it(tmp_path):
    root, targets, revision = _repo(tmp_path)
    manifest = _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "integration-evidence-run",
            "objective": "prove integration with Git evidence",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    dispatch_plan(
        root,
        "integration-evidence-run",
        providers={
            "worker-a": _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[0]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[0]),
                    "notes": "ready",
                }
            )
        },
    )
    verified = verify_plan(root, "integration-evidence-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    task = verified["tasks"][0]
    attempt_id = task["last_attempt_id"]
    patch_path = root / ".devfarm" / "results" / "worker-a" / "attempts" / attempt_id / "patch.diff"
    patch = patch_path.read_text(encoding="utf-8")
    applied = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "apply", "--whitespace=error", "-"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=True,
    )
    assert applied.returncode == 0
    _git(root, "add", targets[0])
    _git(root, "commit", "-m", "integrate worker patch")
    integration_revision = _git(root, "rev-parse", "HEAD").stdout.strip()

    integrated = mark_integrated(
        root,
        "integration-evidence-run",
        "worker-a",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=integration_revision,
        source_attempt_id=attempt_id,
        verified_patch_digest=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
    )
    integrated_task = integrated["tasks"][0]
    assert integrated_task["status"] == "INTEGRATED"
    assert integrated_task["target_ref"] == "HEAD"
    assert integrated_task["integration_revision"] == integration_revision
    assert integrated_task["source_attempt_id"] == attempt_id
    assert integrated_task["verified_patch_digest"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()


def test_commander_reissues_dependent_manifest_from_integration_revision(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    _manifest(root, revision, "worker-b", targets[1])
    original_manifest = ".devfarm/tasks/worker-b.json"
    create_plan(
        root,
        {
            "run_id": "dependent-baseline-run",
            "objective": "advance a dependent worker baseline",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "worker-b",
                    "owner": "worker",
                    "dependencies": ["worker-a"],
                    "manifest_path": original_manifest,
                    "ownership": [targets[1]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
            ],
        },
    )
    dispatch_plan(
        root,
        "dependent-baseline-run",
        providers={
            "worker-a": _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[0]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[0]),
                    "notes": "ready",
                }
            )
        },
    )
    verified = verify_plan(root, "dependent-baseline-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    integration_revision, attempt_id, digest = _integrate_worker_patch(root, "worker-a", verified["tasks"][0])
    integrated = mark_integrated(
        root,
        "dependent-baseline-run",
        "worker-a",
        note="integrated before dependent release",
        target_ref="HEAD",
        integration_revision=integration_revision,
        source_attempt_id=attempt_id,
        verified_patch_digest=digest,
    )

    dependent = integrated["tasks"][1]
    assert dependent["status"] == "READY"
    assert dependent["manifest_path"] != original_manifest
    assert (root / original_manifest).is_file()
    new_manifest = json.loads((root / dependent["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["base_revision"] == integration_revision
    assert original_manifest in dependent["manifest_history"]
